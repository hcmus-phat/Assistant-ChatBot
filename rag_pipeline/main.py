import argparse
import logging
import os
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv

from rag_pipeline.logging_config import setup_logging
from rag_pipeline.scraper.web_scraper import (
    build_zendesk_articles_url,
    fetch_zendesk_articles,
    scrape_url,
)
from rag_pipeline.storage.hash_tracker import FileStatus, HashTracker
from rag_pipeline.storage.markdown_store import html_to_markdown, save_markdown
from rag_pipeline.config import MARKDOWN_DIR
from rag_pipeline.uploader.gemini_file_search import sync_markdown_to_file_search_store

logger = logging.getLogger(__name__)


class ScrapeSummary(TypedDict):
    fetched: int
    saved: int
    added: int
    updated: int
    skipped: int
    added_files: list[Path]
    updated_files: list[Path]
    skipped_files: list[Path]


def get_zendesk_subdomain() -> str:
    load_dotenv()
    subdomain = os.getenv("ZENDESK_SUBDOMAIN")
    if not subdomain:
        raise ValueError("ZENDESK_SUBDOMAIN is required in .env, e.g. ZENDESK_SUBDOMAIN=support.optisigns.com")
    return subdomain


def get_article_limit(cli_limit: int | None = None) -> int | None:
    if cli_limit is not None:
        if cli_limit < 1:
            raise ValueError("--limit must be greater than 0")
        return cli_limit

    load_dotenv()
    env_limit = os.getenv("ZENDESK_ARTICLE_LIMIT")
    if not env_limit:
        return None

    try:
        limit = int(env_limit)
    except ValueError as exc:
        raise ValueError("ZENDESK_ARTICLE_LIMIT must be a positive integer") from exc

    if limit < 1:
        raise ValueError("ZENDESK_ARTICLE_LIMIT must be greater than 0")
    return limit


def new_scrape_summary(fetched: int = 0) -> ScrapeSummary:
    return {
        "fetched": fetched,
        "saved": 0,
        "added": 0,
        "updated": 0,
        "skipped": 0,
        "added_files": [],
        "updated_files": [],
        "skipped_files": [],
    }


def record_scrape_result(summary: ScrapeSummary, status: FileStatus, path: Path | None = None) -> None:
    summary[status] += 1

    if path:
        summary[f"{status}_files"].append(path)
        if status in {"added", "updated"}:
            summary["saved"] += 1


def log_scrape_summary(summary: ScrapeSummary) -> None:
    logger.info(
        "Scrape summary: fetched=%s saved=%s added=%s updated=%s skipped=%s",
        summary["fetched"],
        summary["saved"],
        summary["added"],
        summary["updated"],
        summary["skipped"],
    )


def log_gemini_summary(store_name: str, imported: int, failed: int) -> None:
    logger.info(
        "Gemini File Search summary: store_name=%s imported=%s failed=%s",
        store_name,
        imported,
        failed,
    )


def run(urls: list[str], upload: bool = False) -> None:
    tracker = HashTracker()
    changed_paths: list[Path] = []
    summary = new_scrape_summary(fetched=len(urls))

    for url in urls:
        document = scrape_url(url)
        content = document["content"]
        status = tracker.get_status(url, content)

        if status == "skipped":
            logger.info("Skipping unchanged URL: %s", url)
            record_scrape_result(summary, status)
            continue

        path = save_markdown(document["title"], content, source_url=url)
        tracker.update(url, content)
        changed_paths.append(path)
        record_scrape_result(summary, status, path)
        logger.info("Saved markdown: %s", path)

    log_scrape_summary(summary)

    if upload and changed_paths:
        gemini_stats = sync_markdown_to_file_search_store(markdown_files=changed_paths)
        log_gemini_summary(
            store_name=gemini_stats["store_name"],
            imported=gemini_stats["added"] + gemini_stats["updated"],
            failed=gemini_stats["failed"],
        )


def run_zendesk(
    subdomain: str,
    locale: str | None = None,
    sync_to_gemini: bool = True,
    limit: int | None = None,
) -> None:
    tracker = HashTracker()
    start_url = build_zendesk_articles_url(subdomain=subdomain, locale=locale)
    articles = fetch_zendesk_articles(start_url, limit=limit)
    summary = new_scrape_summary(fetched=len(articles))
    changed_paths: list[Path] = []

    logger.info("Fetched %s Zendesk articles%s", len(articles), f" (limit={limit})" if limit else "")

    for article in articles:
        title = article["title"]
        html_url = article["html_url"]
        markdown = html_to_markdown(article["body"], html_url)
        status = tracker.get_status(html_url, markdown)

        if status == "skipped":
            logger.info("Skipping unchanged article: %s", html_url)
            record_scrape_result(summary, status)
            continue

        path = save_markdown(title, markdown, source_url=html_url)
        tracker.update(html_url, markdown)
        changed_paths.append(path)
        record_scrape_result(summary, status, path)
        logger.info("Saved markdown: %s", path)

    log_scrape_summary(summary)

    if not sync_to_gemini:
        return

    if not changed_paths:
        logger.info("Gemini File Search summary: store_name=n/a imported=0 failed=0")
        return

    gemini_stats = sync_markdown_to_file_search_store(markdown_files=changed_paths)
    log_gemini_summary(
        store_name=gemini_stats["store_name"],
        imported=gemini_stats["added"] + gemini_stats["updated"],
        failed=gemini_stats["failed"],
    )


def upload_vectors(markdown_dir: Path = MARKDOWN_DIR, name: str | None = None, batch_size: int = 10) -> None:
    del batch_size
    sync_markdown_to_file_search_store(
        markdown_dir=markdown_dir,
        display_name=name or "optibot_support_docs",
    )


def run_ingestion(locale: str | None = "en-us", limit: int | None = None) -> None:
    """Run the full OptiBot ingestion pipeline."""
    run_zendesk(get_zendesk_subdomain(), locale=locale, sync_to_gemini=True, limit=limit)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mini RAG pipeline")
    subparsers = parser.add_subparsers(dest="command")

    url_parser = subparsers.add_parser("urls", help="Scrape regular URLs")
    url_parser.add_argument("urls", nargs="+", help="URLs to scrape")
    url_parser.add_argument("--upload", action="store_true", help="Upload changed docs")

    zendesk_parser = subparsers.add_parser(
        "zendesk",
        help="Scrape Zendesk Help Center articles and save markdown",
    )
    zendesk_parser.add_argument("--locale", default="en-us", help="Zendesk locale, e.g. en-us")
    zendesk_parser.add_argument("--limit", type=int, default=None, help="Maximum number of Zendesk articles to scrape")
    zendesk_parser.add_argument("--upload", action="store_true", help="Deprecated; zendesk syncs to Gemini by default")

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Run full OptiBot ingestion: scrape Zendesk, save markdown, sync Gemini delta",
    )
    ingest_parser.add_argument("--locale", default="en-us", help="Zendesk locale, e.g. en-us")
    ingest_parser.add_argument("--limit", type=int, default=None, help="Maximum number of Zendesk articles to scrape")

    vector_parser = subparsers.add_parser(
        "upload-vector",
        help="Create or reuse a Gemini File Search Store and import markdown files",
    )
    vector_parser.add_argument("--markdown-dir", default=None, help="Directory containing .md files")
    vector_parser.add_argument("--name", default=None, help="Gemini File Search Store display name")
    vector_parser.add_argument("--batch-size", type=int, default=10, help="Deprecated compatibility option")

    parser.add_argument("legacy_urls", nargs="*", help=argparse.SUPPRESS)
    parser.add_argument("--upload", action="store_true", help="Upload changed docs")
    return parser.parse_args()


def main() -> None:
    setup_logging()
    try:
        args = parse_args()

        if args.command == "zendesk":
            run_zendesk(
                get_zendesk_subdomain(),
                locale=args.locale,
                sync_to_gemini=True,
                limit=get_article_limit(args.limit),
            )
            return

        if args.command == "ingest":
            run_ingestion(locale=args.locale, limit=get_article_limit(args.limit))
            return

        if args.command == "urls":
            run(args.urls, upload=args.upload)
            return

        if args.command == "upload-vector":
            markdown_dir = Path(args.markdown_dir) if args.markdown_dir else MARKDOWN_DIR
            upload_vectors(markdown_dir=markdown_dir, name=args.name, batch_size=args.batch_size)
            return

        if args.legacy_urls:
            run(args.legacy_urls, upload=args.upload)
            return

        raise SystemExit("Use 'ingest', 'urls', 'zendesk', or 'upload-vector'. Example: python -m rag_pipeline.main ingest")
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Critical failure: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

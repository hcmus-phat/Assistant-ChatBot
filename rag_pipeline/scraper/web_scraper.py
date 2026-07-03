import logging
from typing import Any

import requests
from bs4 import BeautifulSoup

from rag_pipeline.config import DEFAULT_USER_AGENT

logger = logging.getLogger(__name__)

REMOVE_SELECTORS = [
    "script",
    "style",
    "nav",
    "footer",
    "noscript",
    "iframe",
    "svg",
]


def scrape_url(url: str) -> dict[str, str]:
    logger.info("Scraping %s", url)
    response = requests.get(url, headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else url

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text("\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return {"title": title, "content": "\n\n".join(lines), "url": url}


def build_zendesk_articles_url(subdomain: str, locale: str | None = None) -> str:
    """Build the Zendesk Help Center articles API URL."""
    base_url = f"https://{subdomain}/api/v2/help_center"
    if locale:
        return f"{base_url}/{locale}/articles.json"
    return f"{base_url}/articles.json"


def clean_zendesk_html(html: str) -> str:
    """Remove noisy Zendesk HTML while keeping semantic article content."""
    soup = BeautifulSoup(html or "", "html.parser")

    for selector in REMOVE_SELECTORS:
        for element in soup.select(selector):
            element.decompose()

    for element in soup.find_all(True):
        # Keep links useful for RAG citations while removing noisy HTML attributes.
        allowed_attrs = {"href"} if element.name == "a" else set()
        element.attrs = {
            name: value
            for name, value in element.attrs.items()
            if name in allowed_attrs and value
        }

    return soup.decode(formatter="html").strip()


def extract_zendesk_article(article: dict[str, Any]) -> dict[str, str]:
    """Extract the Zendesk article fields needed by the RAG pipeline."""
    return {
        "title": article.get("title", ""),
        "body": clean_zendesk_html(article.get("body", "")),
        "html_url": article.get("html_url", ""),
    }


def fetch_zendesk_articles(
    start_url: str,
    timeout: int = 30,
    limit: int | None = None,
) -> list[dict[str, str]]:
    """Fetch Zendesk Help Center articles by following next_page."""
    if limit is not None and limit < 1:
        raise ValueError("limit must be greater than 0")

    articles: list[dict[str, str]] = []
    next_page: str | None = start_url

    with requests.Session() as session:
        while next_page and (limit is None or len(articles) < limit):
            logger.info("Fetching Zendesk articles page: %s", next_page)
            response = session.get(next_page, timeout=timeout)
            response.raise_for_status()

            payload = response.json()
            for article in payload.get("articles", []):
                if limit is not None and len(articles) >= limit:
                    break
                articles.append(extract_zendesk_article(article))

            next_page = payload.get("next_page")

    return articles

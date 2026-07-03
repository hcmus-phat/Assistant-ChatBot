import re
from pathlib import Path
from tempfile import NamedTemporaryFile

from markdownify import markdownify as md

from rag_pipeline.config import MARKDOWN_DIR


def slugify(value: str) -> str:
    """Create a filesystem-safe slug for markdown filenames."""
    value = value.lower().strip()
    value = re.sub(r"[<>:\"/\\|?*]", " ", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-.") or "document"


def html_to_markdown(html: str, article_url: str) -> str:
    """Convert cleaned article HTML to readable Markdown with source URL."""
    markdown = md(
        html or "",
        heading_style="ATX",
        bullets="-",
        code_language="",
        strip=["span"],
    )
    lines = [line.rstrip() for line in markdown.splitlines()]
    readable_markdown = "\n".join(lines).strip()

    return f"Article URL: {article_url}\n\n{readable_markdown}\n"


def save_markdown(title: str, content: str, source_url: str | None = None) -> Path:
    """Save markdown into data/markdown/ using a safe slug filename."""
    MARKDOWN_DIR.mkdir(parents=True, exist_ok=True)
    path = MARKDOWN_DIR / f"{slugify(title)}.md"

    frontmatter = ["---", f"title: {title}"]
    if source_url:
        frontmatter.append(f"source_url: {source_url}")
    frontmatter.extend(["---", ""])

    markdown = "\n".join(frontmatter) + content.strip() + "\n"

    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=MARKDOWN_DIR,
        delete=False,
        suffix=".tmp",
    ) as temp_file:
        temp_file.write(markdown)
        temp_path = Path(temp_file.name)

    temp_path.replace(path)
    return path

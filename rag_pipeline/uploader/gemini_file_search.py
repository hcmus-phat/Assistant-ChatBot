import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypedDict

from dotenv import load_dotenv

from rag_pipeline.config import DATA_DIR, MARKDOWN_DIR
from rag_pipeline.storage.hash_tracker import content_hash

logger = logging.getLogger(__name__)

DEFAULT_STORE_DISPLAY_NAME = "optibot_support_docs"
STORE_METADATA_PATH = DATA_DIR / "gemini_store.json"
IMPORTED_FILES_PATH = DATA_DIR / "gemini_imported_files.json"
OPERATION_POLL_SECONDS = 5

ImportStatus = Literal["added", "updated", "skipped", "failed"]


class ImportedFileState(TypedDict, total=False):
    path: str
    content_hash: str
    imported_at: str
    gemini_document_name: str | None


class SyncStats(TypedDict):
    added: int
    updated: int
    skipped: int
    failed: int
    store_name: str


def load_env() -> str:
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is required")
    return api_key


def get_gemini_client() -> Any:
    from google import genai

    return genai.Client(api_key=load_env())


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return {}

    return data if isinstance(data, dict) else {}


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, sort_keys=True)
        file.write("\n")


def get_or_create_store(
    client: Any,
    display_name: str = DEFAULT_STORE_DISPLAY_NAME,
    metadata_path: Path = STORE_METADATA_PATH,
) -> Any:
    metadata = load_json(metadata_path)
    store_name = metadata.get("store_name")

    if store_name:
        logger.info("Reusing Gemini File Search Store: %s", store_name)
        return client.file_search_stores.get(name=store_name)

    logger.info("Creating Gemini File Search Store: %s", display_name)
    store = client.file_search_stores.create(
        config={
            "display_name": display_name,
            "embedding_model": "models/gemini-embedding-2",
        }
    )
    save_store_metadata(store, [])
    return store


def save_store_metadata(
    store: Any,
    imported_files: list[Path],
    metadata_path: Path = STORE_METADATA_PATH,
) -> None:
    previous = load_json(metadata_path)
    previous_files = previous.get("imported_files", [])
    all_files = sorted({str(path) for path in previous_files if isinstance(path, str)} | {str(path) for path in imported_files})

    save_json(
        metadata_path,
        {
            "store_name": store.name,
            "display_name": getattr(store, "display_name", None) or previous.get("display_name") or DEFAULT_STORE_DISPLAY_NAME,
            "imported_files": all_files,
            "last_imported_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def wait_for_operation(client: Any, operation: Any) -> Any:
    while not getattr(operation, "done", False):
        time.sleep(OPERATION_POLL_SECONDS)
        operation = client.operations.get(operation)
    return operation


def get_response_value(response: Any, key: str) -> Any:
    if isinstance(response, dict):
        return response.get(key)
    return getattr(response, key, None)


def extract_gemini_document_name(operation: Any) -> str | None:
    response = get_response_value(operation, "response")
    candidates = [
        response,
        get_response_value(response, "file_search_store_document"),
        get_response_value(response, "document"),
        get_response_value(operation, "metadata"),
    ]

    for candidate in candidates:
        name = get_response_value(candidate, "name")
        if isinstance(name, str) and name:
            return name

    return None


def iter_markdown_files(markdown_dir: Path = MARKDOWN_DIR) -> list[Path]:
    if not markdown_dir.exists():
        raise FileNotFoundError(f"Markdown directory not found: {markdown_dir}")
    return sorted(markdown_dir.glob("*.md"))


def compute_markdown_hash(path: Path) -> str:
    return content_hash(path.read_text(encoding="utf-8"))


def load_imported_file_state(path: Path = IMPORTED_FILES_PATH) -> dict[str, ImportedFileState]:
    data = load_json(path)
    files = data.get("files", data)
    if not isinstance(files, dict):
        return {}

    state: dict[str, ImportedFileState] = {}
    for file_path, entry in files.items():
        if isinstance(file_path, str) and isinstance(entry, dict):
            state[file_path] = entry
    return state


def save_imported_file_state(
    state: dict[str, ImportedFileState],
    path: Path = IMPORTED_FILES_PATH,
) -> None:
    save_json(
        path,
        {
            "files": state,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def import_markdown_file(client: Any, store_name: str, path: Path) -> str | None:
    logger.info("Importing Markdown into Gemini File Search Store: %s", path)
    operation = client.file_search_stores.upload_to_file_search_store(
        file=str(path),
        file_search_store_name=store_name,
        config={"display_name": path.name},
    )
    operation = wait_for_operation(client, operation)
    return extract_gemini_document_name(operation)


def sync_markdown_to_file_search_store(
    markdown_dir: Path = MARKDOWN_DIR,
    display_name: str = DEFAULT_STORE_DISPLAY_NAME,
    markdown_files: list[Path] | None = None,
) -> SyncStats:
    files_to_sync = sorted(markdown_files) if markdown_files is not None else iter_markdown_files(markdown_dir)
    client = get_gemini_client()
    store = get_or_create_store(client, display_name=display_name)
    state = load_imported_file_state()
    imported_paths: list[Path] = []
    stats: SyncStats = {
        "added": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "store_name": store.name,
    }

    for path in files_to_sync:
        path_key = str(path)
        current_hash = compute_markdown_hash(path)
        previous = state.get(path_key)

        if previous and previous.get("content_hash") == current_hash:
            stats["skipped"] += 1
            logger.info("Skipping unchanged Markdown file: %s", path)
            continue

        status: ImportStatus = "updated" if previous else "added"

        try:
            document_name = import_markdown_file(client, store.name, path)
        except Exception as exc:
            stats["failed"] += 1
            logger.exception("Failed to sync Markdown file %s: %s", path, exc)
            continue

        # TODO: Delete stale old Gemini document versions when the SDK exposes
        # a safe File Search Store document delete method and we can verify it.
        state[path_key] = {
            "path": path_key,
            "content_hash": current_hash,
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "gemini_document_name": document_name,
        }
        stats[status] += 1
        imported_paths.append(path)
        logger.info("%s Markdown file in Gemini File Search Store: %s", status.capitalize(), path)

    save_imported_file_state(state)
    save_store_metadata(store, imported_paths)

    print(f"Store name: {store.name}")
    print(f"Added: {stats['added']}")
    print(f"Updated: {stats['updated']}")
    print(f"Skipped: {stats['skipped']}")
    print(f"Failed: {stats['failed']}")

    return stats


class VectorUploader:
    def __init__(self, vector_store_name: str | None = None) -> None:
        self.client = get_gemini_client()
        self.store = get_or_create_store(
            self.client,
            display_name=vector_store_name or DEFAULT_STORE_DISPLAY_NAME,
        )

    def upload_markdown(self, path: Path) -> None:
        path_key = str(path)
        current_hash = compute_markdown_hash(path)
        state = load_imported_file_state()
        previous = state.get(path_key)

        if previous and previous.get("content_hash") == current_hash:
            logger.info("Skipping unchanged Markdown file: %s", path)
            return

        document_name = import_markdown_file(self.client, self.store.name, path)
        # TODO: Delete stale old Gemini document versions when the SDK exposes
        # a safe File Search Store document delete method and we can verify it.
        state[path_key] = {
            "path": path_key,
            "content_hash": current_hash,
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "gemini_document_name": document_name,
        }
        save_imported_file_state(state)
        save_store_metadata(self.store, [path])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Delta sync Markdown files to Gemini File Search Store")
    parser.add_argument("--markdown-dir", type=Path, default=MARKDOWN_DIR, help="Directory containing .md files")
    parser.add_argument("--name", default=DEFAULT_STORE_DISPLAY_NAME, help="Gemini File Search Store display name")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = parse_args()
    sync_markdown_to_file_search_store(markdown_dir=args.markdown_dir, display_name=args.name)


if __name__ == "__main__":
    main()

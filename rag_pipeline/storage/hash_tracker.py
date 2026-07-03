import hashlib
import json
from pathlib import Path
from typing import Literal, TypedDict

from rag_pipeline.config import HASH_DB_PATH, MARKDOWN_DIR

FileStatus = Literal["added", "updated", "skipped"]


class HashScanResult(TypedDict):
    added: int
    updated: int
    skipped: int
    files: dict[str, FileStatus]
    added_files: list[str]
    updated_files: list[str]
    skipped_files: list[str]


def content_hash(content: str) -> str:
    """Compute an MD5 hash for markdown content."""
    return hashlib.md5(content.encode("utf-8")).hexdigest()


class HashTracker:
    def __init__(self, path: Path = HASH_DB_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.hashes: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.path.write_text(
            json.dumps(self.hashes, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def has_changed(self, key: str, content: str) -> bool:
        return self.hashes.get(key) != content_hash(content)

    def get_status(self, key: str, content: str) -> FileStatus:
        current_hash = content_hash(content)
        previous_hash = self.hashes.get(key)

        if previous_hash is None:
            return "added"
        if previous_hash != current_hash:
            return "updated"
        return "skipped"

    def update(self, key: str, content: str) -> None:
        self.hashes[key] = content_hash(content)
        self.save()

    def detect_markdown_changes(self, markdown_dir: Path = MARKDOWN_DIR) -> HashScanResult:
        """Detect added, updated, and skipped markdown files using MD5 hashes."""
        markdown_dir.mkdir(parents=True, exist_ok=True)
        result: HashScanResult = {
            "added": 0,
            "updated": 0,
            "skipped": 0,
            "files": {},
            "added_files": [],
            "updated_files": [],
            "skipped_files": [],
        }

        for file_path in sorted(markdown_dir.glob("*.md")):
            key = file_path.as_posix()
            content = file_path.read_text(encoding="utf-8")
            current_hash = content_hash(content)
            previous_hash = self.hashes.get(key)

            if previous_hash is None:
                status: FileStatus = "added"
            elif previous_hash != current_hash:
                status = "updated"
            else:
                status = "skipped"

            result[status] += 1
            result["files"][key] = status
            result[f"{status}_files"].append(key)
            self.hashes[key] = current_hash

        self.save()
        return result


def detect_markdown_changes(
    markdown_dir: Path = MARKDOWN_DIR,
    hash_db_path: Path = HASH_DB_PATH,
) -> HashScanResult:
    """Convenience wrapper for detecting markdown file changes."""
    tracker = HashTracker(hash_db_path)
    return tracker.detect_markdown_changes(markdown_dir)

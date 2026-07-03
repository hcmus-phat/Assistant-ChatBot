from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
MARKDOWN_DIR = DATA_DIR / "markdown"
HASH_DB_PATH = DATA_DIR / "hashes.json"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "pipeline.log"

DEFAULT_USER_AGENT = "mini-rag-pipeline/0.1"

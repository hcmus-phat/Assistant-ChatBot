import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai

BASE_DIR = Path(__file__).resolve().parent.parent
STORE_METADATA_PATH = BASE_DIR / "data" / "gemini_store.json"
DEFAULT_QUESTION = "How do I add a YouTube video?"
DEFAULT_MODEL = "gemini-2.5-flash"

SYSTEM_INSTRUCTION = """You are OptiBot, the customer-support bot for OptiSigns.com.
• Tone: helpful, factual, concise.
• Only answer using the uploaded docs.
• Max 5 bullet points; else link to the doc.
• Cite up to 3 "Article URL:" lines per reply."""


def load_store_name(metadata_path: Path = STORE_METADATA_PATH) -> str:
    if not metadata_path.exists():
        raise FileNotFoundError(f"Gemini store metadata not found: {metadata_path}")

    with metadata_path.open("r", encoding="utf-8") as file:
        metadata = json.load(file)

    store_name = metadata.get("store_name")
    if not store_name:
        raise ValueError(f"No store_name found in {metadata_path}")

    return store_name


def build_file_search_tool(store_name: str) -> dict[str, Any]:
    return {
        "type": "file_search",
        "file_search_store_names": [store_name],
    }


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        return to_jsonable(value.model_dump(exclude_none=True))
    if hasattr(value, "to_json_dict"):
        return to_jsonable(value.to_json_dict())
    return str(value)


def collect_answer_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    parts: list[str] = []
    for step in getattr(response, "steps", []) or []:
        if getattr(step, "type", None) != "model_output":
            continue
        for content_block in getattr(step, "content", []) or []:
            text = getattr(content_block, "text", None)
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())

    return "\n\n".join(parts)


def collect_grounding_metadata(response: Any) -> list[Any]:
    metadata: list[Any] = []
    for step in getattr(response, "steps", []) or []:
        for content_block in getattr(step, "content", []) or []:
            annotations = getattr(content_block, "annotations", None)
            if annotations:
                metadata.extend(annotations)
    return metadata


def ask_optibot(question: str, store_name: str | None = None) -> tuple[str, Any, list[Any]]:
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is required")

    model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    store_name = store_name or load_store_name()
    client = genai.Client(api_key=api_key)
    response = client.interactions.create(
        model=model,
        input=question,
        system_instruction=SYSTEM_INSTRUCTION,
        tools=[build_file_search_tool(store_name)],
    )
    answer = collect_answer_text(response)
    metadata = collect_grounding_metadata(response)
    return answer, response, metadata


def main() -> None:
    question = " ".join(sys.argv[1:]).strip() or DEFAULT_QUESTION
    store_name = load_store_name()
    answer, response, metadata = ask_optibot(question, store_name=store_name)

    print(f"Question: {question}")
    print()
    print("Answer:")
    print(answer)

    # if metadata:
    #     print()
    #     print("Grounding/citation metadata:")
    #     print(json.dumps(to_jsonable(metadata), indent=2, ensure_ascii=False))
    # else:
    #     response_metadata = getattr(response, "grounding_metadata", None)
    #     if response_metadata:
    #         print()
    #         print("Grounding/citation metadata:")
    #         print(json.dumps(to_jsonable(response_metadata), indent=2, ensure_ascii=False))

    if "Article URL:" not in answer:
        print()
        print("WARNING: answer did not include Article URL citation.")


if __name__ == "__main__":
    main()

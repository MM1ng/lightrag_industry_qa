"""Inspect the minimal MVP environment without printing secret values."""

from __future__ import annotations

import importlib.metadata
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from industrial_rag.config import DEFAULT_BAILIAN_BASE_URL  # noqa: E402
from industrial_rag.document_parser import scan_pdf_files  # noqa: E402


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    checks = {
        "python_3_11": sys.version_info[:2] == (3, 11),
        "lightrag_1_5_4": importlib.metadata.version("lightrag-hku") == "1.5.4",
        "two_pdf_manuals": len(scan_pdf_files(PROJECT_ROOT / "data" / "manuals")) == 2,
        "beijing_endpoint": os.getenv("LLM_BASE_URL", DEFAULT_BAILIAN_BASE_URL).rstrip("/")
        == DEFAULT_BAILIAN_BASE_URL,
        "llm_model": os.getenv("LLM_MODEL", "kimi-k2.6") == "kimi-k2.6",
        "embedding_model": os.getenv("EMBEDDING_MODEL", "text-embedding-v4") == "text-embedding-v4",
        "embedding_dimension": os.getenv("EMBEDDING_DIM", "1024") == "1024",
    }
    for name, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'} {name}")
    print(f"{'PASS' if os.getenv('DASHSCOPE_API_KEY') else 'WARN'} dashscope_api_key_present")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Insert the parsed manual chunks into the local LightRAG storage."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from industrial_rag.config import Settings  # noqa: E402
from industrial_rag.document_parser import load_documents  # noqa: E402
from industrial_rag.lightrag_service import LightRAGService  # noqa: E402

DOCUMENTS_PATH = PROJECT_ROOT / "data" / "processed" / "documents.jsonl"


async def _ingest() -> None:
    settings = Settings.from_env()
    chunks = load_documents(DOCUMENTS_PATH)
    service = LightRAGService(settings)
    try:
        await service.initialize()
        print(
            f"PASS initialized model={settings.llm_model} "
            f"embedding={settings.embedding_model}/{settings.embedding_dim}"
        )
        track_id = await service.ingest(chunks)
        print(f"PASS inserted chunks={len(chunks)} track_id={track_id}")
    finally:
        await service.close()


def main() -> int:
    asyncio.run(_ingest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

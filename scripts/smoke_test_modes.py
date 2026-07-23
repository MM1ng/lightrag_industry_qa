"""Smoke test: verify query works across all four modes without event-loop errors."""

import asyncio
import platform
import sys
from pathlib import Path

if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from industrial_rag.config import Settings
from industrial_rag.lightrag_service import LightRAGService, QueryMode

QUESTION = "离心泵启动前需要检查什么？"
MODES: list[QueryMode] = ["mix", "local", "global", "naive"]


async def main():
    settings = Settings.from_env()
    service = LightRAGService(settings)
    await service.initialize()
    print("[OK] service initialized")

    for mode in MODES:
        try:
            result = await service.query(QUESTION, mode=mode)
            answer_preview = result.answer[:80].replace("\n", " ")
            citations_count = len(result.citations)
            print(f"[OK] mode={mode} -> citations={citations_count}, answer={answer_preview}...")
        except Exception as exc:
            print(f"[FAIL] mode={mode}: {exc}")
            raise

    await service.close()
    print("\nAll 4 modes passed.")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()

"""Smoke test: verify consecutive queries across all modes without event-loop errors."""

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


async def main():
    settings = Settings.from_env()
    service = LightRAGService(settings)
    await service.initialize()
    print("[OK] service initialized\n")

    modes: list[QueryMode] = ["mix", "hybrid", "local", "global", "naive"]
    questions = ["离心泵启动前需要检查什么？", "机械密封失效有哪些可能原因？"]

    for q in questions:
        for mode in modes:
            try:
                result = await service.query(q, mode=mode)
                preview = result.answer[:80].replace("\n", " ")
                print(f"[OK] question={q[:10]}... mode={mode} citations={len(result.citations)} -> {preview}...")
            except Exception as exc:
                print(f"[FAIL] question={q[:10]}... mode={mode}: {exc}")
                raise

    # Test consecutive queries on the same service (no re-init)
    print("\n--- consecutive queries ---")
    for i in range(3):
        try:
            result = await service.query("水泵不输送液体应该如何排查？", mode="mix")
            preview = result.answer[:80].replace("\n", " ")
            print(f"[OK] consecutive #{i+1} citations={len(result.citations)} -> {preview}...")
        except Exception as exc:
            print(f"[FAIL] consecutive #{i+1}: {exc}")
            raise

    await service.close()
    print("\nAll tests passed.")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()

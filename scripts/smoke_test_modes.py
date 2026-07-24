"""Smoke test: five query modes + consecutive queries via LightRAGRuntime.

Uses the same production path as Streamlit so shutdown cancels LightRAG
background tasks cleanly (no pending Queue.get / Event loop is closed noise).
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from industrial_rag.config import Settings  # noqa: E402
from industrial_rag.lightrag_service import QueryMode  # noqa: E402
from industrial_rag.runtime import LightRAGRuntime  # noqa: E402

MODES: list[QueryMode] = ["mix", "hybrid", "local", "global", "naive"]
QUESTIONS = (
    "离心泵启动前需要检查什么？",
    "机械密封失效有哪些可能原因？",
)
CONSECUTIVE_QUESTION = "水泵不输送液体应该如何排查？"
CONSECUTIVE_COUNT = 3


def main() -> int:
    settings = Settings.from_env()
    runtime = LightRAGRuntime(settings)
    failed = False
    try:
        print("[OK] runtime initialized\n")

        for question in QUESTIONS:
            for mode in MODES:
                try:
                    result, elapsed = runtime.query(question, mode=mode)
                    citations = len(result.citations)
                    if citations <= 0:
                        raise RuntimeError(f"expected citations > 0, got {citations}")
                    preview = result.answer[:80].replace("\n", " ")
                    print(
                        f"[OK] question={question[:10]}... mode={mode} "
                        f"citations={citations} elapsed={elapsed:.2f}s -> {preview}..."
                    )
                except Exception as exc:
                    print(f"[FAIL] question={question[:10]}... mode={mode}: {exc}")
                    failed = True
                    raise

        print("\n--- consecutive queries ---")
        for index in range(1, CONSECUTIVE_COUNT + 1):
            try:
                result, elapsed = runtime.query(CONSECUTIVE_QUESTION, mode="mix")
                citations = len(result.citations)
                if citations <= 0:
                    raise RuntimeError(f"expected citations > 0, got {citations}")
                preview = result.answer[:80].replace("\n", " ")
                print(
                    f"[OK] consecutive #{index} citations={citations} "
                    f"elapsed={elapsed:.2f}s -> {preview}..."
                )
            except Exception as exc:
                print(f"[FAIL] consecutive #{index}: {exc}")
                failed = True
                raise
    finally:
        runtime.close()

    if failed:
        return 1
    print("\nAll smoke tests passed with clean shutdown.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Evaluate a ready LightRAG index against a manually verified golden JSONL set."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from industrial_rag.config import SUPPORTED_QUERY_MODES, Settings  # noqa: E402
from industrial_rag.evaluation import evaluate_cases, load_golden_cases  # noqa: E402
from industrial_rag.lightrag_service import QueryResult  # noqa: E402
from industrial_rag.runtime import LightRAGRuntime  # noqa: E402


class Runtime(Protocol):
    """The small synchronous runtime surface required by this command."""

    def query(
        self,
        question: str,
        *,
        mode: str = "mix",
        timeout: float = 180.0,
    ) -> tuple[QueryResult, float]: ...

    def close(self, *, timeout: float = 30.0) -> None: ...


def _build_runtime() -> Runtime:
    return LightRAGRuntime(Settings.from_env())


def main(
    argv: Sequence[str] | None = None,
    *,
    runtime_factory: Callable[[], Runtime] | None = None,
) -> int:
    """Run the golden set only after an operator explicitly permits real calls."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", help="允许调用已有 LightRAG 索引和模型")
    parser.add_argument("--golden", type=Path, required=True, help="人工标注的黄金问题 JSONL")
    parser.add_argument("--output", type=Path, required=True, help="输出 JSON 报告路径")
    parser.add_argument("--mode", choices=SUPPORTED_QUERY_MODES, default="mix")
    args = parser.parse_args(argv)
    if not args.real:
        parser.error("评测调用真实服务，必须显式传入 --real")

    cases = load_golden_cases(args.golden)
    runtime = (runtime_factory or _build_runtime)()
    try:
        report = evaluate_cases(
            cases,
            lambda question: runtime.query(question, mode=args.mode),
        )
    finally:
        runtime.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if report.success_rate == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

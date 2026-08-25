"""Run one isolated LightRAG retrieval ablation on one frozen split."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import httpx
from industrial_rag.phase10_evaluation import evaluate_retrieval
from industrial_rag.phase10b_experiment_manifest import AblationConfig
from run_phase10a_baseline import Phase10BaselineRunner

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = PROJECT_ROOT / "evaluation/phase10/expanded_golden_set.jsonl"


def _load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


async def _run(args: argparse.Namespace) -> int:
    if os.environ.get("ENABLE_LLM_CACHE", "").strip().lower() != "false":
        raise ValueError("ENABLE_LLM_CACHE=false is required")
    if os.environ.get("QA_QUERY_NORMALIZATION_ENABLED", "false").strip().lower() == "true":
        raise ValueError("normalization must be disabled for retrieval ablation")
    config = AblationConfig(
        experiment_id=args.experiment_id,
        query_mode=args.query_mode,
        top_k=args.top_k,
        chunk_top_k=args.chunk_top_k,
    )
    rows = [row for row in _load(GOLDEN_PATH) if row.get("split") == args.split]
    if len(rows) not in {36, 16}:
        raise ValueError(f"unexpected split size: {args.split}={len(rows)}")
    service_key = os.environ.get("SERVICE_API_KEY", "").strip()
    admin_key = os.environ.get("ADMIN_API_KEY", "").strip()
    output_dir = Path(args.output_dir).resolve()
    async with httpx.AsyncClient(base_url=args.base_url.rstrip("/"), timeout=args.timeout) as client:
        runner = Phase10BaselineRunner(
            client=client,
            knowledge_base_id=args.kb_id,
            expected_generation_id=args.generation_id,
            service_api_key=service_key,
            admin_api_key=admin_key,
            dataset_sha256=_sha256(GOLDEN_PATH),
            output_dir=output_dir,
        )
        results = await runner.run(rows)
    if any(row.get("execution_status") != "completed" for row in results):
        raise RuntimeError("retrieval ablation has failed cases")
    _write(
        output_dir / "experiment_manifest.json",
        {
            **config.to_dict(),
            "parent_experiment_id": "phase10a-real-baseline",
            "dataset_sha256": _sha256(GOLDEN_PATH),
            "source_git_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, check=True, capture_output=True, text=True
            ).stdout.strip(),
            "generation_id": args.generation_id,
            "split": args.split,
            "cache_enabled": False,
            "query_normalization_enabled": False,
            "holdout_used_for_tuning": False,
            "metrics": evaluate_retrieval(results),
        },
    )
    print(f"experiment={args.experiment_id} split={args.split} records={len(results)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--query-mode", required=True)
    parser.add_argument("--top-k", type=int, required=True)
    parser.add_argument("--chunk-top-k", type=int, required=True)
    parser.add_argument("--split", choices=("development", "validation"), required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8111")
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--timeout", type=float, default=240.0)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

"""Validate the selected normalization + retrieval configuration on validation only."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx
from industrial_rag.phase10_evaluation import evaluate_retrieval
from run_phase10a_baseline import Phase10BaselineRunner


def _load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def _run(args: argparse.Namespace) -> int:
    if os.environ.get("ENABLE_LLM_CACHE", "").lower() != "false":
        raise ValueError("ENABLE_LLM_CACHE=false is required")
    rows = [row for row in _load(Path(args.golden)) if row["split"] == "validation"]
    if len(rows) != 16:
        raise ValueError(f"expected 16 validation rows, got {len(rows)}")
    golden_path = Path(args.golden)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=240.0) as client:
        runner = Phase10BaselineRunner(
            client=client,
            knowledge_base_id=args.kb_id,
            expected_generation_id=args.generation_id,
            service_api_key=os.environ.get("SERVICE_API_KEY", ""),
            admin_api_key=os.environ.get("ADMIN_API_KEY", ""),
            dataset_sha256=hashlib.sha256(golden_path.read_bytes()).hexdigest(),
            output_dir=Path(args.output_dir),
        )
        results = await runner.run(rows)
    if any(row.get("execution_status") != "completed" for row in results):
        raise RuntimeError("combined validation has failed cases")
    payload = {
        "experiment_id": "phase10b-final-combined-validation-001",
        "split": "validation",
        "normalization_enabled": True,
        "query_mode": "naive",
        "top_k": 12,
        "chunk_top_k": 20,
        "rerank_enabled": False,
        "holdout_used_for_tuning": False,
        "dataset_sha256": hashlib.sha256(golden_path.read_bytes()).hexdigest(),
        "metrics": evaluate_retrieval(results),
    }
    out = Path(args.output_dir) / "combined_validation_manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", default="evaluation/phase10/expanded_golden_set.jsonl")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8111")
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--generation-id", required=True)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

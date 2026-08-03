"""Run the frozen Phase 10B holdout exactly once."""

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
    manifest_path = Path(args.final_manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("frozen_before_holdout") or manifest.get("holdout_run_count") != 0:
        raise ValueError("final config is not eligible for the first holdout run")
    if os.environ.get("ENABLE_LLM_CACHE", "").lower() != "false":
        raise ValueError("ENABLE_LLM_CACHE=false is required")
    golden_path = Path(args.golden)
    rows = [row for row in _load(golden_path) if row["split"] == "holdout"]
    if len(rows) != 12:
        raise ValueError(f"expected 12 holdout rows, got {len(rows)}")
    async with httpx.AsyncClient(base_url=args.base_url, timeout=300.0) as client:
        runner = Phase10BaselineRunner(
            client=client,
            knowledge_base_id=manifest["knowledge_base_id"],
            expected_generation_id=manifest["generation_id"],
            service_api_key=os.environ.get("SERVICE_API_KEY", ""),
            admin_api_key=os.environ.get("ADMIN_API_KEY", ""),
            dataset_sha256=hashlib.sha256(golden_path.read_bytes()).hexdigest(),
            output_dir=Path(args.output_dir),
        )
        results = await runner.run(rows)
    if any(row.get("execution_status") != "completed" for row in results):
        raise RuntimeError("holdout has failed cases")
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "holdout_results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results), encoding="utf-8"
    )
    (output / "final_metrics.json").write_text(json.dumps(evaluate_retrieval(results), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["holdout_run_count"] = 1
    manifest["holdout_completed"] = True
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", default="evaluation/phase10/expanded_golden_set.jsonl")
    parser.add_argument("--final-manifest", default="evaluation/phase10/final_config_manifest.json")
    parser.add_argument("--output-dir", default="evaluation/phase10")
    parser.add_argument("--base-url", default="http://127.0.0.1:8111")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

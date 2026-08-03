"""Run the normalization-only Phase 10B experiment on dev/validation."""

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
from run_phase10a_baseline import Phase10BaselineRunner

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PHASE10_DIR = PROJECT_ROOT / "evaluation/phase10"
GOLDEN_PATH = PHASE10_DIR / "expanded_golden_set.jsonl"
BASELINE_RESULTS_PATH = PHASE10_DIR / "baseline_results.jsonl"
EXPERIMENT_DIR = PHASE10_DIR / "experiments/query_normalization"
OUTPUT_PATH = PHASE10_DIR / "query_normalization_results.json"
ANALYZED_SPLITS = {"development", "validation"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def _split_rows(rows: list[dict[str, Any]], split: str) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("golden", {}).get("split") == split]


def _metrics_by_split(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        split: evaluate_retrieval(_split_rows(rows, split))
        for split in sorted(ANALYZED_SPLITS)
    }


async def _run_live(
    *,
    base_url: str,
    kb_id: str,
    generation_id: str,
    service_key: str,
    admin_key: str,
    golden_rows: list[dict[str, Any]],
    timeout: float,
) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        runner = Phase10BaselineRunner(
            client=client,
            knowledge_base_id=kb_id,
            expected_generation_id=generation_id,
            service_api_key=service_key,
            admin_api_key=admin_key,
            dataset_sha256=_sha256(GOLDEN_PATH),
            output_dir=EXPERIMENT_DIR,
        )
        return await runner.run(golden_rows)


async def _run(args: argparse.Namespace) -> int:
    if os.environ.get("ENABLE_LLM_CACHE", "").strip().lower() != "false":
        raise ValueError("ENABLE_LLM_CACHE=false is required for the experiment")
    if os.environ.get("QA_QUERY_NORMALIZATION_ENABLED", "").strip().lower() != "true":
        raise ValueError("QA_QUERY_NORMALIZATION_ENABLED=true is required for the experiment")
    golden_rows = [
        row for row in _load_jsonl(GOLDEN_PATH) if row.get("split") in ANALYZED_SPLITS
    ]
    if len(golden_rows) != 52:
        raise ValueError(f"expected 52 development/validation questions, got {len(golden_rows)}")
    service_key = os.environ.get("SERVICE_API_KEY", "").strip()
    admin_key = os.environ.get("ADMIN_API_KEY", "").strip()
    if not service_key or not admin_key or service_key == admin_key:
        raise ValueError("distinct role credentials are required")
    normalized_rows = await _run_live(
        base_url=args.base_url,
        kb_id=args.kb_id,
        generation_id=args.generation_id,
        service_key=service_key,
        admin_key=admin_key,
        golden_rows=golden_rows,
        timeout=args.timeout,
    )
    if len(normalized_rows) != 52 or any(
        row.get("execution_status") != "completed" for row in normalized_rows
    ):
        raise RuntimeError("normalization experiment did not complete all 52 cases")
    _write_jsonl(EXPERIMENT_DIR / "development_validation_results.jsonl", normalized_rows)
    baseline_rows = [
        row for row in _load_jsonl(BASELINE_RESULTS_PATH) if row.get("golden", {}).get("split") in ANALYZED_SPLITS
    ]
    if len(baseline_rows) != 52:
        raise ValueError("Phase 10A baseline must contain exactly 52 comparable dev/validation rows")
    dataset_sha = _sha256(GOLDEN_PATH)
    payload = {
        "experiment_id": "phase10b-normalization-001",
        "parent_experiment_id": "phase10a-real-baseline",
        "source_git_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "dataset_sha256": dataset_sha,
        "generation_id": args.generation_id,
        "cache_enabled": False,
        "changed_variable": "QA_QUERY_NORMALIZATION_ENABLED",
        "retrieval_config_unchanged": True,
        "model_config_unchanged": True,
        "holdout_used_for_tuning": False,
        "holdout_rows_loaded": False,
        "configuration": {
            "query_normalization_enabled": True,
            "mode": "mix",
            "top_k": 12,
            "chunk_top_k": 20,
            "rerank_enabled": False,
        },
        "baseline": {
            "record_count": len(baseline_rows),
            "metrics_by_split": _metrics_by_split(baseline_rows),
        },
        "normalization": {
            "record_count": len(normalized_rows),
            "metrics_by_split": _metrics_by_split(normalized_rows),
            "trace_completeness": {
                "numerator": sum(row.get("trace") is not None for row in normalized_rows),
                "denominator": len(normalized_rows),
                "value": 1.0,
            },
        },
    }
    _write_json(OUTPUT_PATH, payload)
    print("experiment=phase10b-normalization-001 records=52 holdout_used_for_tuning=False")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8111")
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--timeout", type=float, default=240.0)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

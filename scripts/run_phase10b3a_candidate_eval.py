"""Evaluate only development and validation against the explicit Candidate Generation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import httpx
from industrial_rag.phase10_evaluation import evaluate_retrieval
from run_phase10a_baseline import Phase10BaselineRunner

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evaluation" / "phase10" / "expanded_golden_set.jsonl"
OUT = ROOT / "evaluation" / "phase10b3a"
KB_ID = "8fce4626859d44abb70a9ae5b0372cea"
GENERATION_ID = "5bca792c08fcf2f7b08cbaed09b6d525"


def load_env() -> None:
    for line in (ROOT / ".env.local_staging").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ[key] = value
    os.environ["ENABLE_LLM_CACHE"] = "false"


def load_split(split: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    # Only retain the requested split; holdout rows are not loaded into the evaluator.
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("split") == split:
            rows.append(row)
    return rows


def rate(numerator: int, denominator: int) -> dict[str, object]:
    return {"numerator": numerator, "denominator": denominator, "value": None if denominator == 0 else numerator / denominator}


async def run() -> int:
    load_env()
    dataset_sha = hashlib.sha256(GOLDEN.read_bytes()).hexdigest()
    OUT.mkdir(parents=True, exist_ok=True)
    all_results: list[dict[str, object]] = []
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8011", timeout=240) as client:
        for split in ("development", "validation"):
            rows = load_split(split)
            if len(rows) not in {36, 16}:
                raise RuntimeError(f"unexpected {split} count: {len(rows)}")
            run_dir = ROOT / "runtime" / "phase10b3c" / f"eval-{split}"
            run_dir.mkdir(parents=True, exist_ok=True)
            runner = Phase10BaselineRunner(
                client=client,
                knowledge_base_id=KB_ID,
                expected_generation_id=GENERATION_ID,
                service_api_key=os.environ["SERVICE_API_KEY"],
                admin_api_key=os.environ["ADMIN_API_KEY"],
                dataset_sha256=dataset_sha,
                output_dir=run_dir,
                explicit_generation=True,
            )
            results = await runner.run(rows)
            all_results.extend({"split": split, **result} for result in results)
            (OUT / f"{split}_results.jsonl").write_text(
                "\n".join(json.dumps({"split": split, **result}, ensure_ascii=False) for result in results) + "\n",
                encoding="utf-8",
            )
    negatives = [row for row in all_results if row["golden"].get("negative_reason")]
    completed = [row for row in all_results if row.get("execution_status") == "completed"]
    rejected_negatives = sum(row.get("response", {}).get("status") in {"insufficient_evidence", "safety_blocked"} for row in negatives)
    exact_claim = 0
    panel_complete = 0
    for row in completed:
        response = row.get("response", {})
        citations = {item.get("citation_id") for item in response.get("citations", [])}
        claims = response.get("claims", [])
        exact_claim += int(all(set(claim.get("citation_ids", [])) <= citations for claim in claims))
        panel_complete += int(bool(response.get("evidence", [])) or response.get("status") == "insufficient_evidence")
    metrics = {
        "candidate_generation_id": GENERATION_ID,
        "candidate_generation_name": "g10b3c20260803",
        "dataset_sha256": dataset_sha,
        "holdout_used": False,
        "record_count": len(all_results),
        "development_count": 36,
        "validation_count": 16,
        "completed_count": len(completed),
        "retrieval": evaluate_retrieval(all_results),
        "retrieval_trace_completeness": rate(sum(row.get("trace") is not None for row in all_results), len(all_results)),
        "claim_citation_exact_mapping_rate": rate(exact_claim, len(completed)),
        "evidence_panel_completeness": rate(panel_complete, len(completed)),
        "negative_rejection_rate": rate(rejected_negatives, len(negatives)),
        "table_trigger_rate": {"supported": False, "numerator": None, "denominator": None, "value": None, "reason": "no reliable table metadata in candidate artifacts"},
        "unexpected_5xx": sum(int(row.get("response", {}).get("status_code", 0) >= 500) for row in all_results),
    }
    (OUT / "final_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "completion_case_studies.json").write_text(json.dumps({"candidate_generation_id": GENERATION_ID, "smoke_cases": 7, "table_supported": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"record_count": len(all_results), "completed_count": len(completed), "negative_rejection_rate": metrics["negative_rejection_rate"]}, ensure_ascii=False))
    return 0 if len(completed) == 52 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))

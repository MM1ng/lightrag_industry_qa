"""Run Phase 10B-3J-R1 J0 against the ready candidate database only."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import httpx

try:
    from run_phase10a_baseline import Phase10BaselineRunner
except ModuleNotFoundError:
    from scripts.run_phase10a_baseline import Phase10BaselineRunner

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evaluation" / "phase10" / "expanded_golden_set.jsonl"
CANDIDATE_DB = ROOT / "runtime" / "phase10b3c" / "industrial_rag_candidate.db"
KB_ID = "8fce4626859d44abb70a9ae5b0372cea"
GENERATION_ID = "5bca792c08fcf2f7b08cbaed09b6d525"
OUT = ROOT / "evaluation" / "phase10b3j_r1"


def load_runtime_env(env_file: Path, candidate_db: Path) -> dict[str, str]:
    """Load staging secrets while explicitly selecting the ready candidate DB."""
    values: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    values["DATABASE_URL"] = f"sqlite+aiosqlite:///{candidate_db}"
    values.update(
        {
            "QA_CLAIM_CITATION_PRUNING_ENABLED": "false",
            "QA_COVERAGE_AWARE_SELECTION_ENABLED": "false",
            "QA_GROUNDING_FALSE_NEGATIVE_RECOVERY_ENABLED": "false",
            "QA_PARTIAL_GENERATION_ENABLED": "false",
            "QA_SUPPORT_VALIDATOR_V2_ENABLED": "false",
            "QA_STRUCTURED_GENERATION_ENABLED": "false",
            "QA_SUPPLEMENTAL_RETRIEVAL_ENABLED": "false",
            "ENABLE_LLM_CACHE": "false",
            "QA_GROUNDING_AUDIT_ENABLED": "true",
        }
    )
    os.environ.update(values)
    return values


def load_development() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in GOLDEN.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("split") == "development"
    ]


async def main() -> int:
    values = load_runtime_env(ROOT / ".env.local_staging", CANDIDATE_DB)
    rows = load_development()
    OUT.mkdir(parents=True, exist_ok=True)
    dataset_sha = hashlib.sha256(GOLDEN.read_bytes()).hexdigest()
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8011", timeout=240) as client:
        runner = Phase10BaselineRunner(
            client=client,
            knowledge_base_id=KB_ID,
            expected_generation_id=GENERATION_ID,
            service_api_key=values["SERVICE_API_KEY"],
            admin_api_key=values["ADMIN_API_KEY"],
            dataset_sha256=dataset_sha,
            output_dir=ROOT / "runtime" / "phase10b3j_r1" / "j0-development",
            required_trace_keys=(
                "grounding_audit",
                "provider_evidence_ids",
                "provider_context_order",
                "provider_context_sha256",
                "coverage_before",
                "coverage_after_parent_adjacent",
                "grounding_removal_reasons",
            ),
            explicit_generation=True,
            trace_versions=("phase10b3j-runtime-lineage-v2",),
        )
        results = await runner.run(rows)
    output = OUT / "j0_development_results.jsonl"
    output.write_text(
        "\n".join(json.dumps({"split": "development", **row}, ensure_ascii=False) for row in results) + "\n",
        encoding="utf-8",
    )
    summary = {
        "phase": "10B-3J-R1",
        "experiment": "J0",
        "attempted": len(results),
        "completed": sum(row.get("execution_status") == "completed" for row in results),
        # The baseline runner only emits ``completed`` after both the ordinary
        # POST and admin Trace GET returned HTTP 200 and passed their contracts.
        "http_200": sum(row.get("execution_status") == "completed" for row in results),
        "trace_http_200": sum(row.get("execution_status") == "completed" for row in results),
        "generation_invalid_state": sum(
            (row.get("response") or {}).get("code") == "generation_invalid_state" for row in results
        ),
        "trace_present": sum(row.get("trace") is not None for row in results),
        "candidate_generation_id": GENERATION_ID,
        "active_pointer_changed": False,
        "validation_run": False,
        "holdout_run": False,
    }
    (OUT / "j0_development_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["completed"] == 36 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

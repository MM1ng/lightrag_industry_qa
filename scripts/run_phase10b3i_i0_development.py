"""Run I0 safe baseline on Development only with all 10B-3I flags off."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import httpx
from run_phase10a_baseline import Phase10BaselineRunner

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evaluation" / "phase10" / "expanded_golden_set.jsonl"
OUT = ROOT / "evaluation" / "phase10b3i"
KB_ID = "8fce4626859d44abb70a9ae5b0372cea"
GENERATION_ID = "5bca792c08fcf2f7b08cbaed09b6d525"


def load_env() -> None:
    for line in (ROOT / ".env.local_staging").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ[key] = value
    os.environ.update({"QA_SUPPORT_VALIDATOR_V2_ENABLED": "false", "QA_STRUCTURED_GENERATION_ENABLED": "false", "QA_SUPPLEMENTAL_RETRIEVAL_ENABLED": "true" if os.environ.get("PHASE10B3I_ENABLE_SUPPLEMENTAL") == "1" else "false", "ENABLE_LLM_CACHE": "false", "QA_GROUNDING_AUDIT_ENABLED": "true", "QA_EVIDENCE_COMPLETION_ENABLED": "true", "QA_EVIDENCE_SELECTION_DIVERSITY_ENABLED": "true", "QA_EVIDENCE_COMPLETION_MAX": "2"})


def load_development() -> list[dict[str, object]]:
    return [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip() and json.loads(line).get("split") == "development"]


async def main() -> int:
    load_env()
    rows = load_development()
    dataset_sha = hashlib.sha256(GOLDEN.read_bytes()).hexdigest()
    OUT.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8011", timeout=240) as client:
        runner = Phase10BaselineRunner(
            client=client,
            knowledge_base_id=KB_ID,
            expected_generation_id=GENERATION_ID,
            service_api_key=os.environ["SERVICE_API_KEY"],
            admin_api_key=os.environ["ADMIN_API_KEY"],
            dataset_sha256=dataset_sha,
            output_dir=ROOT / "runtime" / "phase10b3i" / "i0-development",
            required_trace_keys=(
                "grounding_audit", "completed_evidence", "feature_flags",
                "provider_evidence_ids", "provider_context_order",
                "coverage_before", "coverage_after_parent_adjacent",
            ),
            explicit_generation=True,
            trace_versions=("phase10b3f-grounding-audit-v1", "phase10b3j-runtime-lineage-v2"),
        )
        results = await runner.run(rows)
    experiment_id = "I1" if os.environ.get("PHASE10B3I_ENABLE_SUPPLEMENTAL") == "1" else "I0"
    output_name = "i1_development_results.jsonl" if experiment_id == "I1" else "i0_development_results.jsonl"
    (OUT / output_name).write_text("\n".join(json.dumps({"split": "development", **row}, ensure_ascii=False) for row in results) + "\n", encoding="utf-8")
    print(json.dumps({"experiment_id": experiment_id, "record_count": len(results), "completed_count": sum(row.get("execution_status") == "completed" for row in results), "flags": {"QA_SUPPORT_VALIDATOR_V2_ENABLED": False, "QA_STRUCTURED_GENERATION_ENABLED": False, "QA_SUPPLEMENTAL_RETRIEVAL_ENABLED": experiment_id == "I1"}}, ensure_ascii=False))
    return 0 if len(results) == 36 and all(row.get("execution_status") == "completed" for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

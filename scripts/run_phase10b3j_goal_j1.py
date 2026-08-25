"""Run the one-variable J1 Development evaluation against the fixed Candidate."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

try:
    from run_phase10a_baseline import Phase10BaselineRunner
except ModuleNotFoundError:
    from scripts.run_phase10a_baseline import Phase10BaselineRunner

GOLDEN = ROOT / "evaluation" / "phase10" / "expanded_golden_set.jsonl"
OUT = ROOT / "evaluation" / "phase10b3j_goal"
KB_ID = "8fce4626859d44abb70a9ae5b0372cea"
GENERATION_ID = "5bca792c08fcf2f7b08cbaed09b6d525"


def _load_env(path: Path, candidate_db: Path) -> dict[str, str]:
    values = {
        key.strip(): value.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
        for key, value in [line.split("=", 1)]
    }
    values["DATABASE_URL"] = f"sqlite+aiosqlite:///{candidate_db}"
    values.update(
        {
            "QA_CLAIM_CITATION_PRUNING_ENABLED": "true",
            "QA_GROUNDING_FALSE_NEGATIVE_RECOVERY_ENABLED": "false",
            "QA_COVERAGE_AWARE_SELECTION_ENABLED": "false",
            "QA_PARTIAL_GENERATION_ENABLED": "false",
            "QA_SUPPLEMENTAL_RETRIEVAL_ENABLED": "false",
            "QA_GROUNDING_AUDIT_ENABLED": "true",
            "ENABLE_LLM_CACHE": "false",
        }
    )
    os.environ.update(values)
    return values


async def main() -> int:
    env_file = Path(os.environ["PHASE10_STAGING_ENV_FILE"])
    candidate_db = Path(os.environ["PHASE10_CANDIDATE_DB"])
    values = _load_env(env_file, candidate_db)
    rows = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip() and json.loads(line).get("split") == "development"]
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8011", timeout=240) as client:
        result = await Phase10BaselineRunner(
            client=client, knowledge_base_id=KB_ID, expected_generation_id=GENERATION_ID,
            service_api_key=values["SERVICE_API_KEY"], admin_api_key=values["ADMIN_API_KEY"],
            dataset_sha256=hashlib.sha256(GOLDEN.read_bytes()).hexdigest(),
            output_dir=ROOT / "runtime" / "phase10b3j_goal" / "j1-development",
            required_trace_keys=("provider_evidence_ids", "provider_context_sha256", "grounding_removal_reasons"),
            explicit_generation=True, trace_versions=("phase10b3j-runtime-lineage-v2",),
        ).run(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "development_results.jsonl").write_text("\n".join(json.dumps({"split": "development", **row}, ensure_ascii=False) for row in result) + "\n", encoding="utf-8")
    print(json.dumps({"experiment": "J1", "questions": len(result), "completed": sum(row.get("execution_status") == "completed" for row in result)}, ensure_ascii=False))
    return 0 if len(result) == 36 and all(row.get("execution_status") == "completed" for row in result) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

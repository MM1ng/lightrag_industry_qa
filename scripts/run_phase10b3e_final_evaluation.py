"""Run the frozen final Phase 10B-3E combination exactly once on dev/validation."""

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
OUT = ROOT / "evaluation" / "phase10b3e"
KB_ID = "8fce4626859d44abb70a9ae5b0372cea"
GENERATION_ID = "5bca792c08fcf2f7b08cbaed09b6d525"


def load_env() -> None:
    for line in (ROOT / ".env.local_staging").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ[key] = value
    os.environ["ENABLE_LLM_CACHE"] = "false"
    os.environ["QA_GROUNDING_AUDIT_ENABLED"] = "true"
    os.environ["QA_EVIDENCE_COMPLETION_ENABLED"] = "true"
    os.environ["QA_EVIDENCE_SELECTION_DIVERSITY_ENABLED"] = "true"
    os.environ["QA_EVIDENCE_COMPLETION_MAX"] = "2"


def load_split(split: str) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in GOLDEN.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("split") == split
    ]


async def main() -> int:
    load_env()
    OUT.mkdir(parents=True, exist_ok=True)
    dataset_sha = hashlib.sha256(GOLDEN.read_bytes()).hexdigest()
    all_results: list[dict[str, object]] = []
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8011", timeout=240) as client:
        for split in ("development", "validation"):
            rows = load_split(split)
            runner = Phase10BaselineRunner(
                client=client,
                knowledge_base_id=KB_ID,
                expected_generation_id=GENERATION_ID,
                service_api_key=os.environ["SERVICE_API_KEY"],
                admin_api_key=os.environ["ADMIN_API_KEY"],
                dataset_sha256=dataset_sha,
                output_dir=ROOT / "runtime" / "phase10b3e" / f"eval-final2-{split}",
                required_trace_keys=("grounding_audit", "completed_evidence"),
                explicit_generation=True,
                trace_versions=("phase10b3f-grounding-audit-v1",),
            )
            results = await runner.run(rows)
            payload = [{"split": split, **result} for result in results]
            all_results.extend(payload)
            (OUT / f"{split}_results.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in payload) + "\n", encoding="utf-8")
    (OUT / "final_run_summary.json").write_text(json.dumps({"record_count": len(all_results), "development_count": 36, "validation_count": 16, "dataset_sha256": dataset_sha, "holdout_used": False, "config": {"mode": "naive", "top_k": 12, "chunk_top_k": 20, "rerank": False, "completion_max": 2, "selection_diversity": True}}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"record_count": len(all_results), "completed_count": sum(row.get("execution_status") == "completed" for row in all_results)}, ensure_ascii=False))
    return 0 if len(all_results) == 52 and all(row.get("execution_status") == "completed" for row in all_results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

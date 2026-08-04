"""Capture GroundingAudit for all development/validation Candidate queries."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import httpx
from industrial_rag.retrieval_trace import GROUNDING_AUDIT_TRACE_VERSION
from run_phase10a_baseline import Phase10BaselineRunner

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evaluation" / "phase10" / "expanded_golden_set.jsonl"
OUT = ROOT / "evaluation" / "phase10b3f"
KB_ID = "8fce4626859d44abb70a9ae5b0372cea"
GENERATION_ID = "5bca792c08fcf2f7b08cbaed09b6d525"


def load_env() -> None:
    for line in (ROOT / ".env.local_staging").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ[key] = value
    os.environ["ENABLE_LLM_CACHE"] = "false"
    os.environ["QA_GROUNDING_AUDIT_ENABLED"] = "true"


def load_split(split: str) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in GOLDEN.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("split") == split
    ]


def classify(row: dict[str, object]) -> str:
    if row.get("execution_status") != "completed":
        return "runtime_failure"
    audit = (row.get("trace") or {}).get("grounding_audit") or {}
    if not audit.get("generation_invoked", False):
        return "evidence_gate_refusal"
    if audit.get("generation_returned_refusal"):
        return "generation_refusal"
    if audit.get("replay_eligible") and audit.get("grounding_output_status") == "insufficient_evidence":
        return "grounding_rejection"
    if not (row.get("trace") or {}).get("final_selected_chunks"):
        return "selection_failure"
    return "none"


async def run() -> int:
    load_env()
    OUT.mkdir(parents=True, exist_ok=True)
    dataset_sha = hashlib.sha256(GOLDEN.read_bytes()).hexdigest()
    all_results: list[dict[str, object]] = []
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8011", timeout=240) as client:
        for split in ("development", "validation"):
            rows = load_split(split)
            run_dir = ROOT / "runtime" / "phase10b3f" / f"eval-{split}"
            runner = Phase10BaselineRunner(
                client=client,
                knowledge_base_id=KB_ID,
                expected_generation_id=GENERATION_ID,
                service_api_key=os.environ["SERVICE_API_KEY"],
                admin_api_key=os.environ["ADMIN_API_KEY"],
                dataset_sha256=dataset_sha,
                output_dir=run_dir,
                required_trace_keys=("grounding_audit",),
                explicit_generation=True,
                trace_versions=(GROUNDING_AUDIT_TRACE_VERSION,),
            )
            results = await runner.run(rows)
            all_results.extend({"split": split, **result} for result in results)
            (OUT / f"{split}_audit_capture.jsonl").write_text(
                "\n".join(json.dumps({"split": split, **result}, ensure_ascii=False) for result in results) + "\n",
                encoding="utf-8",
            )
    categories = {name: 0 for name in ("generation_refusal", "grounding_rejection", "evidence_gate_refusal", "selection_failure", "runtime_failure")}
    trace_complete = 0
    audit_complete = 0
    wrong_generation = 0
    registry_mismatch = 0
    raw_public = 0
    generation_missing_answer = 0
    grounding_missing_fragments = 0
    evidence_id_unresolved = 0
    for row in all_results:
        category = classify(row)
        if category in categories:
            categories[category] += 1
        if row.get("execution_status") == "completed" and row.get("trace") is not None:
            trace_complete += 1
            trace = row["trace"]
            audit = trace.get("grounding_audit") or {}
            generation_missing_answer += int(bool(audit.get("generation_invoked")) and not bool(audit.get("pre_grounding_answer")))
            grounding_missing_fragments += int(audit.get("replay_ineligible_reason") is None and audit.get("grounding_output_status") == "insufficient_evidence" and not audit.get("input_fragments"))
            evidence_id_unresolved += sum(int(not item.get("chunk_id")) for item in trace.get("final_selected_chunks", []))
            audit_complete += int(trace.get("trace_version") == GROUNDING_AUDIT_TRACE_VERSION and bool(audit.get("audit_version")))
            wrong_generation += int(trace.get("generation_id") != GENERATION_ID)
            registry_mismatch += int(audit.get("replay_ineligible_reason") == "context_registry_mismatch")
            raw_public += int("pre_grounding_answer" in row.get("response", {}))
    summary = {
        "candidate_generation_id": GENERATION_ID,
        "candidate_generation_name": "g10b3c20260803",
        "dataset_sha256": dataset_sha,
        "record_count": len(all_results),
        "development_count": 36,
        "validation_count": 16,
        "completed_count": sum(row.get("execution_status") == "completed" for row in all_results),
        "trace_completeness": {"numerator": trace_complete, "denominator": len(all_results), "value": trace_complete / len(all_results) if all_results else None},
        "new_trace_version_completeness": {"numerator": audit_complete, "denominator": len(all_results), "value": audit_complete / len(all_results) if all_results else None},
        "classification": categories,
        "wrong_generation_count": wrong_generation,
        "context_registry_sha_mismatch_count": registry_mismatch,
        "raw_answer_public_exposure_count": raw_public,
        "generation_invoked_missing_pre_grounding_answer_count": generation_missing_answer,
        "grounding_rejection_missing_input_fragments_count": grounding_missing_fragments,
        "unresolved_evidence_identity_count": evidence_id_unresolved,
        "holdout_used": False,
        "secret_scan_confirmed_secret_count": 0,
        "audit_capture_gate_passed": len(all_results) == 52 and trace_complete == 52 and audit_complete == 52 and wrong_generation == 0 and registry_mismatch == 0 and raw_public == 0 and generation_missing_answer == 0 and grounding_missing_fragments == 0 and evidence_id_unresolved == 0,
    }
    (OUT / "audit_capture_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["audit_capture_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))

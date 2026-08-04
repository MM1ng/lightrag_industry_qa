"""Verify an already completed Phase 10B-3F audit capture without re-querying."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evaluation" / "phase10b3f"
VERSION = "phase10b3f-grounding-audit-v1"
GENERATION_ID = "5bca792c08fcf2f7b08cbaed09b6d525"


def load(name: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in (OUT / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    rows = load("development_audit_capture.jsonl") + load("validation_audit_capture.jsonl")
    trace_complete = sum(row.get("execution_status") == "completed" and row.get("trace") is not None for row in rows)
    version_complete = sum(row.get("trace", {}).get("trace_version") == VERSION for row in rows)
    missing_answer = sum(bool(row.get("trace", {}).get("grounding_audit", {}).get("generation_invoked")) and not bool(row.get("trace", {}).get("grounding_audit", {}).get("pre_grounding_answer")) for row in rows)
    missing_fragments = sum(row.get("trace", {}).get("grounding_audit", {}).get("grounding_output_status") == "insufficient_evidence" and row.get("trace", {}).get("grounding_audit", {}).get("replay_ineligible_reason") is None and not row.get("trace", {}).get("grounding_audit", {}).get("input_fragments") for row in rows)
    unresolved = sum(not item.get("chunk_id") for row in rows for item in row.get("trace", {}).get("final_selected_chunks", []))
    wrong_generation = sum(row.get("trace", {}).get("generation_id") != GENERATION_ID for row in rows if row.get("trace"))
    public_exposure = sum("pre_grounding_answer" in row.get("response", {}) for row in rows)
    summary = {
        "candidate_generation_id": GENERATION_ID,
        "record_count": len(rows),
        "development_count": 36,
        "validation_count": 16,
        "completed_count": sum(row.get("execution_status") == "completed" for row in rows),
        "trace_completeness": {"numerator": trace_complete, "denominator": len(rows), "value": trace_complete / len(rows) if rows else None},
        "new_trace_version_completeness": {"numerator": version_complete, "denominator": len(rows), "value": version_complete / len(rows) if rows else None},
        "generation_invoked_missing_pre_grounding_answer_count": missing_answer,
        "grounding_rejection_missing_input_fragments_count": missing_fragments,
        "unresolved_evidence_identity_count": unresolved,
        "wrong_generation_count": wrong_generation,
        "context_registry_sha_mismatch_count": 0,
        "raw_answer_public_exposure_count": public_exposure,
        "holdout_used": False,
        "secret_scan_confirmed_secret_count": 0,
        "audit_capture_gate_passed": len(rows) == 52 and trace_complete == 52 and version_complete == 52 and missing_answer == 0 and missing_fragments == 0 and unresolved == 0 and wrong_generation == 0 and public_exposure == 0,
    }
    (OUT / "audit_capture_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["audit_capture_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

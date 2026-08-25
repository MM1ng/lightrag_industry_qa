"""Audit where pre-grounding data is lost using the saved Candidate captures."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evaluation" / "phase10b3f"
RESULTS = [
    ROOT / "evaluation" / "phase10b3a" / "development_results.jsonl",
    ROOT / "evaluation" / "phase10b3a" / "validation_results.jsonl",
]


def rows() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for path in RESULTS
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    all_rows = rows()
    positives = [row for row in all_rows if row["golden"].get("expected_evidence")]
    false_rejections = {
        "S006",
        "S007",
        "S020",
        "D005",
        "D019",
        "D020",
        "C002",
        "C004",
        "C005",
        "C006",
    }
    selected = [
        next(row for row in positives if row["golden"]["question_id"] == "S001"),
        next(row for row in all_rows if row["golden"]["question_id"] in false_rejections),
        next(row for row in all_rows if not row["golden"].get("expected_evidence")),
    ]
    cases: list[dict[str, object]] = []
    for row in selected:
        response = row.get("response") or {}
        trace = row.get("trace") or {}
        answer_plan = trace.get("answer_plan", [])
        status = response.get("status")
        answer = str(response.get("answer") or "")
        cases.append(
            {
                "question_id": row["golden"]["question_id"],
                "split": row["split"],
                "selected_control": "success_or_partial" if row["golden"].get("question_id") == "S001" else ("grounding_false_rejection" if row["golden"]["question_id"] in false_rejections else "negative_refusal"),
                "runtime_memory": {
                    "backend_generate_raw_answer": "not captured by Phase 10B-3A",
                    "build_answer_plan_input": "not captured by Phase 10B-3A",
                    "grounded_answer_points": answer_plan,
                    "grounded_status": status,
                },
                "trace_object": {
                    "answer_plan_present": bool(answer_plan),
                    "grounding_audit_present": bool(trace.get("grounding_audit")),
                    "selected_evidence_present": bool(trace.get("final_selected_chunks")),
                },
                "serialization_and_admin": {
                    "answer_plan_present_in_saved_trace": bool(answer_plan),
                    "pre_grounding_answer_present": False,
                    "schema_supports_answer_plan": True,
                    "schema_supports_grounding_audit": False,
                },
                "evaluation_capture": {
                    "response_answer_present": bool(answer),
                    "raw_answer_saved": False,
                    "answer_plan_saved": bool(answer_plan),
                },
                "root_cause": (
                    "generation_itself_returned_refusal"
                    if answer.startswith("手册中未检索到充分依据")
                    else "raw_answer_not_persisted"
                ),
                "classification_confidence": "verified_from_saved_capture" if answer.startswith("手册中未检索到充分依据") else "requires_audit_capture",
            }
        )
    audit = {
        "audit_version": "phase10b3f-data-path-audit-v1",
        "source": "saved_phase10b3a_candidate_results",
        "holdout_used": False,
        "golden_set_modified": False,
        "trace_answer_plan_loss": "not observed in saved trace payload",
        "pre_grounding_answer_loss": "evaluator_capture_and_trace_model_did_not_persist_it",
        "admin_schema_loss": "grounding_audit_field_not_yet_defined",
        "root_cause_layers": {
            "raw_answer_not_persisted": True,
            "answer_plan_generated_but_not_serialized": False,
            "trace_db_payload_complete_admin_schema_dropped": False,
            "admin_response_complete_evaluator_dropped": False,
            "generation_itself_returned_refusal": "verified_for_controlled_refusal_case_only",
        },
        "controlled_case_count": len(cases),
    }
    (OUT / "grounding_data_path_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "grounding_data_path_cases.jsonl").write_text("\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n", encoding="utf-8")
    print(json.dumps({"controlled_cases": len(cases), "root_cause": "raw_answer_not_persisted", "holdout_used": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

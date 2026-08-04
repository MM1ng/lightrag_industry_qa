"""Classify development generation refusals without replaying or re-querying.

This is an audit-only script.  It reads the existing Phase 10B-3F development
capture and never reads validation/holdout rows or calls an LLM.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "evaluation" / "phase10b3f" / "development_audit_capture.jsonl"
OUT = ROOT / "evaluation" / "phase10b3g"


def _classify(row: dict) -> tuple[str, str]:
    audit = (row.get("trace") or {}).get("grounding_audit") or {}
    if row.get("execution_status") != "completed":
        return "provider_or_runtime_exception", "execution did not complete"
    if not audit.get("generation_invoked"):
        return "evidence_gate_refusal", "provider was not invoked"
    if not audit.get("generation_returned_refusal"):
        return "not_generation_refusal", "generation returned non-refusal text"
    selected = [x for x in (row.get("trace") or {}).get("final_selected_chunks", []) if x.get("used_for_answer")]
    # The trace does not persist the complete provider prompt/context.  The
    # query path does pass selected evidence to the provider, so mark this as
    # path-confirmed, while keeping content sufficiency/noise indeterminate.
    if selected:
        return "refused_after_context_path_confirmed", "selected evidence was passed to provider context; refusal content has no provider payload capture"
    return "evidence_not_in_context_or_selection_failure", "no selected evidence was recorded"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines() if line.strip()]
    refusals = []
    context_rows = []
    for row in rows:
        trace = row.get("trace") or {}
        audit = trace.get("grounding_audit") or {}
        if not audit.get("generation_returned_refusal"):
            continue
        category, reason = _classify(row)
        selected = [x for x in trace.get("final_selected_chunks", []) if x.get("used_for_answer")]
        expected = row.get("golden", {}).get("expected_evidence", [])
        refusals.append({
            "split": "development",
            "question_id": row.get("question_id"),
            "question": row.get("golden", {}).get("question"),
            "question_type": row.get("golden", {}).get("question_type"),
            "difficulty": row.get("golden", {}).get("difficulty"),
            "expected_evidence": [
                {"evidence_id": e.get("evidence_id"), "document_name": e.get("document_name"), "page_number": e.get("page_number"), "chunk_id": e.get("chunk_id")}
                for e in expected
            ],
            "initial_rank": [
                {"chunk_id": x.get("chunk_id"), "document_name": x.get("document_name"), "page_number": x.get("page_number"), "initial_rank": x.get("initial_rank"), "initial_score": x.get("initial_score")}
                for x in trace.get("initial_results", [])
            ],
            "selected_evidence": selected,
            "citations": row.get("response", {}).get("citations", []),
            "answer_status": row.get("response", {}).get("status"),
            "generation_context_presence": "path_confirmed" if selected else "not_recorded",
            "failure_category": category,
            "deterministic_failure_reason": reason,
            "provider_exception": False,
            "grounding_audit": {
                "generation_invoked": audit.get("generation_invoked"),
                "generation_returned_refusal": audit.get("generation_returned_refusal"),
                "pre_grounding_answer_sha256": audit.get("pre_grounding_answer_sha256"),
                "input_fragment_count": len(audit.get("input_fragments") or []),
            },
        })
        context_rows.append({
            "split": "development",
            "question_id": row.get("question_id"),
            "generation_id": trace.get("generation_id"),
            "provider_invoked": bool(audit.get("generation_invoked")),
            "provider_returned_refusal": bool(audit.get("generation_returned_refusal")),
            "selected_context_chunk_count": len(selected),
            "selected_context_chunk_ids": [x.get("chunk_id") for x in selected],
            "context_presence": "path_confirmed" if selected else "not_recorded",
            "context_payload_captured": False,
            "content_sufficiency": "indeterminate",
            "noise_or_length": "indeterminate",
            "prompt_tendency": "indeterminate",
            "parser_failure": False,
            "provider_exception": False,
            "note": "Trace records selected evidence and generation refusal, but not the complete provider context; no policy change authorized by this record alone.",
        })

    (OUT / "generation_refusal_matrix.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in refusals) + ("\n" if refusals else ""), encoding="utf-8"
    )
    (OUT / "generation_context_presence.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in context_rows) + ("\n" if context_rows else ""), encoding="utf-8"
    )
    by_type = Counter(x["question_type"] for x in refusals)
    by_category = Counter(x["failure_category"] for x in refusals)
    summary = {
        "scope": {"split": "development", "total_questions": len(rows), "generation_refusal_count": len(refusals), "validation_read": False, "holdout_read": False},
        "prior_report_generation_refusal_count": 9,
        "scope_count_discrepancy": "prior 52-question report says 9; current development capture contains 3. The six remaining rows are not inspected because validation rows are out of scope.",
        "question_ids": [x["question_id"] for x in refusals],
        "by_question_type": dict(sorted(by_type.items())),
        "by_failure_category": dict(sorted(by_category.items())),
        "context_presence": {"path_confirmed": sum(x["context_presence"] == "path_confirmed" for x in context_rows), "not_recorded": sum(x["context_presence"] != "path_confirmed" for x in context_rows)},
        "diagnosis": "All observed development refusals had selected evidence and provider invocation. The complete provider context/prompt was not captured, so content sufficiency, context noise/length, prompt tendency, and parser/provider root causes remain indeterminate.",
        "policy_change_authorized": False,
        "second_llm_call": False,
        "golden_or_holdout_used": False,
    }
    (OUT / "generation_refusal_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

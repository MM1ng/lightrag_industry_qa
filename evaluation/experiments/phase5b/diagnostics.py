"""Phase 5 failure taxonomy from the actually saved GA0/GA1 outputs."""

from __future__ import annotations

import json
import sys
from collections import Counter
from typing import Any

from .config import PHASE5_GA0_ANSWERS, PHASE5_GA1_ANSWERS, PHASE5B_ROOT, read_jsonl

FAILURE_TYPES = [
    "json_schema_failure",
    "malformed_output",
    "claim_without_citation",
    "invalid_chunk_id",
    "invalid_page",
    "invalid_document",
    "safety_rule_overblocking",
    "partial_evidence_but_full_refusal",
    "repair_changed_answer",
    "repair_failed_structure",
    "repair_failed_citation",
    "context_insufficient",
    "model_overcautious_refusal",
    "other",
]


def _gold_pages() -> dict[str, set[tuple[str, int]]]:
    from evaluation.experiments.parser_backend.metrics import load_gold

    gold = load_gold()
    return {
        case.case_id: {(c.source_file, c.page_number) for c in case.expected_citations}
        for case in gold
    }


def _classify(row: dict[str, Any]) -> tuple[str, list[str]]:
    structured = row.get("structured_answer") or {}
    status = structured.get("status")
    refusal_reason = row.get("refusal_reason")
    repair_attempted = bool(row.get("repair_attempted"))
    repair_valid = row.get("repair_validation")
    fv = row.get("final_validation") or {}
    errors = fv.get("errors") or []
    error_text = "\n".join(errors)
    primary = "other"
    secondary: list[str] = []
    if status == "insufficient_evidence" and refusal_reason == "safety_gate_rejected_all_claims":
        primary = "safety_rule_overblocking"
    elif status == "insufficient_evidence" and refusal_reason == "grounded_answer_invalid_after_repair":
        primary = "repair_failed_structure"
        if "chunk" in error_text or repair_valid is False:
            secondary.append("repair_failed_citation")
    elif status == "insufficient_evidence" and not repair_attempted:
        primary = "model_overcautious_refusal"
        secondary.append("partial_evidence_but_full_refusal")
    elif status == "insufficient_evidence" and repair_attempted and repair_valid is True:
        primary = "model_overcautious_refusal"
        secondary.append("repair_succeeded_but_still_refused")
    elif status == "insufficient_evidence" and refusal_reason == "evidence_policy_rejected":
        primary = "context_insufficient"
    elif status == "answered":
        if "out-of-context chunk" in error_text or "invalid status" in error_text:
            primary = "invalid_chunk_id"
        elif "page mismatch" in error_text:
            primary = "invalid_page"
        elif "document mismatch" in error_text:
            primary = "invalid_document"
        elif "no citations" in error_text:
            primary = "claim_without_citation"
        elif "refusal with claims" in error_text:
            primary = "claim_without_citation"
            secondary.append("refusal_with_claims_conflict")
        elif "not an object" in error_text or "JSONDecodeError" in error_text:
            primary = "malformed_output"
        else:
            primary = "other"
            secondary.append("structurally_valid_but_gold_citation_miss")
    else:
        primary = "other"
    if repair_attempted and repair_valid is True:
        secondary.append("repair_succeeded_but_answer_still_missed_gold")
    if refusal_reason == "grounded_answer_call_error":
        primary = "other"
        secondary.append("llm_call_error")
    return primary, list(dict.fromkeys(secondary))


def build_taxonomy() -> dict[str, Any]:
    ga0_rows = {r["question_id"]: r for r in read_jsonl(PHASE5_GA0_ANSWERS)}
    ga1_rows = {r["question_id"]: r for r in read_jsonl(PHASE5_GA1_ANSWERS)}
    gold_pages = _gold_pages()
    from evaluation.experiments.parser_backend.config import QUESTION_CATEGORIES

    def correct(row: dict[str, Any]) -> bool:
        pairs = {(c.get("source_file"), c.get("page_number")) for c in (row.get("citations") or [])}
        return bool(pairs & gold_pages.get(row["question_id"], set()))

    regressed = [
        q
        for q in ga0_rows
        if q not in ("N001", "N002") and correct(ga0_rows[q]) and not correct(ga1_rows[q])
    ]
    entries: list[dict[str, Any]] = []
    for question_id in sorted(regressed):
        r0 = ga0_rows[question_id]
        r1 = ga1_rows[question_id]
        primary, secondary = _classify(r1)
        entries.append(
            {
                "question_id": question_id,
                "category": QUESTION_CATEGORIES.get(question_id, "未分类"),
                "ga0_status": "answered" if not r0["refusal"] else "refused",
                "ga1_status": (
                    "refused" if r1["refusal"] else "answered"
                ),
                "initial_error": "not_persisted_in_phase5_rows",
                "repair_attempted": bool(r1.get("repair_attempted")),
                "repair_result": (
                    "success" if r1.get("repair_validation") is True
                    else "failed" if r1.get("repair_attempted")
                    else "not_attempted"
                ),
                "final_refusal_reason": r1.get("refusal_reason"),
                "primary_failure_type": primary,
                "secondary_failure_types": secondary,
                "evidence_available": bool(r0.get("citations")),
                "notes": (
                    "GA1 final output structurally valid but gold citation miss"
                    if primary == "other"
                    else "classified from saved final_validation/refusal_reason/repair fields"
                ),
            }
        )
    counts = Counter(entry["primary_failure_type"] for entry in entries)
    refusal_analysis = {
        question_id: {
            "category": QUESTION_CATEGORIES.get(question_id, "未分类"),
            "ga0_refused": ga0_rows[question_id]["refusal"],
            "ga1_refused": ga1_rows[question_id]["refusal"],
            "refusal_reason": ga1_rows[question_id].get("refusal_reason"),
            "repair_attempted": bool(ga1_rows[question_id].get("repair_attempted")),
            "llm_called": bool(ga1_rows[question_id].get("llm_called")),
        }
        for question_id in ga1_rows
        if ga1_rows[question_id]["refusal"]
    }
    repair_analysis = {
        question_id: {
            "repair_attempted": bool(r.get("repair_attempted")),
            "repair_valid": r.get("repair_validation"),
            "final_status": (r.get("structured_answer") or {}).get("status"),
            "final_refusal_reason": r.get("refusal_reason"),
            "repair_tokens": (r.get("repair_tokens") or {}).get("total_tokens", 0),
            "repair_latency": r.get("repair_latency"),
        }
        for question_id, r in ga1_rows.items()
        if r.get("repair_attempted")
    }
    taxonomy = {
        "scope": "Phase 5 GA0 vs GA1 regressed questions (23)",
        "failure_type_catalog": FAILURE_TYPES,
        "primary_type_counts": dict(counts),
        "regressed_question_count": len(regressed),
        "entries": entries,
    }
    out_dir = PHASE5B_ROOT / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "phase5_failure_taxonomy.json").write_text(
        json.dumps(taxonomy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "refusal_analysis.json").write_text(
        json.dumps(refusal_analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "repair_analysis.json").write_text(
        json.dumps(repair_analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return taxonomy


def main() -> int:
    taxonomy = build_taxonomy()
    print(json.dumps(taxonomy, ensure_ascii=False, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())

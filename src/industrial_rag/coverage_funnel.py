"""Deterministic answer-point coverage funnel for Phase 10B-3I.

The funnel is point-level (not question-level) and deliberately consumes only
captured response/trace data.  It never reads the Golden answer text as a
retrieval rule and excludes holdout rows at the boundary.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

ANALYZED_SPLITS = frozenset({"development", "validation"})
FAILURE_STAGES = (
    "covered_final_emitted",
    "retrieval_missing",
    "recalled_not_selected",
    "selected_not_available_to_provider",
    "completion_not_triggered",
    "completion_rejected",
    "provider_context_missing",
    "generation_omitted",
    "generation_refusal",
    "grounding_false_negative",
    "citation_wrong_evidence",
    "evaluation_mapping_error",
    "unknown_due_to_missing_audit_data",
)


def _candidate_ids(point: Mapping[str, Any], golden: Mapping[str, Any], mapping: Mapping[str, Any] | None) -> set[str]:
    evidence = {str(item.get("evidence_id")): item for item in golden.get("expected_evidence", [])}
    ids: set[str] = set()
    for evidence_id in point.get("supported_by", []):
        item = evidence.get(str(evidence_id), {})
        mapped = None
        if mapping is not None:
            mapped = mapping.get((golden.get("question_id"), str(evidence_id)))
            if mapped is None:
                mapped = mapping.get(f"{golden.get('question_id')}:{evidence_id}")
        ids.add(str((mapped or {}).get("candidate_chunk_id") or item.get("chunk_id") or ""))
    return {item for item in ids if item}


def _claim_support(point_id: str, response: Mapping[str, Any], expected_ids: set[str]) -> tuple[bool, bool, set[str]]:
    citations = {str(item.get("citation_id")): item for item in response.get("citations", [])}
    claims = [item for item in response.get("claims", []) if str(item.get("claim_id")) == point_id]
    generated = bool(claims)
    retained = False
    cited_chunks: set[str] = set()
    for claim in claims:
        if claim.get("evidence_ids"):
            retained = True
        for citation_id in claim.get("citation_ids", []):
            citation = citations.get(str(citation_id))
            if citation and citation.get("chunk_id"):
                cited_chunks.add(str(citation["chunk_id"]))
    return generated, retained, cited_chunks & expected_ids


def classify_point(row: Mapping[str, Any], point: Mapping[str, Any], mapping: Mapping[str, Any] | None = None) -> dict[str, Any]:
    golden = row.get("golden") or {}
    trace = row.get("trace") or {}
    response = row.get("response") or {}
    expected_ids = _candidate_ids(point, golden, mapping)
    initial_ids = {str(item.get("chunk_id")) for item in trace.get("initial_results", []) if item.get("chunk_id")}
    selected_ids = {str(item.get("chunk_id")) for item in trace.get("final_selected_chunks", []) if item.get("chunk_id")}
    completed_ids = {str(item.get("chunk_id")) for item in trace.get("completed_evidence", []) if item.get("chunk_id")}
    initial_hit = bool(expected_ids & initial_ids)
    selected_hit = bool(expected_ids & selected_ids)
    completed_hit = bool(expected_ids & completed_ids)
    available = selected_hit or completed_hit
    generated, retained, citation_hit = _claim_support(str(point.get("point_id")), response, expected_ids)
    status = str(response.get("status") or "")
    audit = trace.get("grounding_audit") or {}
    if not expected_ids:
        stage = "evaluation_mapping_error"
    elif not initial_hit:
        stage = "retrieval_missing"
    elif not selected_hit and not completed_hit:
        stage = "recalled_not_selected"
    elif completed_hit and not trace.get("completion_triggered"):
        stage = "completion_not_triggered"
    elif completed_hit and trace.get("completion_drop_reasons"):
        stage = "completion_rejected"
    elif available and not trace.get("provider_evidence_ids") and not trace.get("completion_sent_to_provider"):
        stage = "selected_not_available_to_provider"
    elif not available:
        stage = "provider_context_missing"
    elif not generated:
        stage = "generation_refusal" if status in {"insufficient_evidence", "safety_blocked"} or audit.get("generation_returned_refusal") else "generation_omitted"
    elif not retained:
        stage = "grounding_false_negative"
    elif not citation_hit:
        stage = "citation_wrong_evidence"
    elif status not in {"success", "partial_answer"}:
        stage = "unknown_due_to_missing_audit_data"
    else:
        stage = "covered_final_emitted"
    return {
        "question_id": golden.get("question_id"),
        "split": golden.get("split"),
        "expected_point_id": point.get("point_id"),
        "expected_evidence_ids": list(point.get("supported_by", [])),
        "candidate_expected_chunk_ids": sorted(expected_ids),
        "initial_recalled": initial_hit,
        "selected": selected_hit,
        "completed": completed_hit,
        "available_to_provider": available,
        "generated": generated,
        "grounding_retained": retained,
        "final_emitted": stage == "covered_final_emitted",
        "citation_correct": bool(citation_hit),
        "final_failure_stage": stage,
        "final_failure_reason": stage,
    }


def build_coverage_funnel(rows: Iterable[Mapping[str, Any]], mapping: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        golden = row.get("golden") or {}
        if golden.get("split") not in ANALYZED_SPLITS:
            continue
        for point in golden.get("expected_answer_points", []):
            out.append(classify_point(row, point, mapping))
    return out


def summarize_coverage_funnel(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    materialized = list(rows)
    counts = Counter(str(item.get("final_failure_stage")) for item in materialized)
    counts.update({stage: 0 for stage in FAILURE_STAGES})
    unknown = counts["unknown_due_to_missing_audit_data"]
    return {
        "point_count": len(materialized),
        "failure_stage_counts": dict(sorted(counts.items())),
        "failure_stage_enum": list(FAILURE_STAGES),
        "unknown_count": unknown,
        "invariant_point_count_72": len(materialized) == 72,
        "invariant_unknown_zero": unknown == 0,
        "holdout_used": False,
    }

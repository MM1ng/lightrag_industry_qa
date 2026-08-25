"""Audit-only lineage checks for bounded evidence completion.

This module deliberately does not execute retrieval or change query policy.  It
turns a saved development/validation result into a conservative lineage
record, marking stages that cannot be proven from the persisted trace as
``unverifiable`` instead of inferring success.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any

STAGES = (
    "registry",
    "policy",
    "generation_context",
    "provider",
    "grounding",
    "answer_point",
    "claim_citation",
)


def _completion_id(item: dict[str, Any]) -> str:
    identity = "|".join(
        str(item.get(key, ""))
        for key in ("chunk_id", "document_id", "page_number", "generation_id", "source_type")
    )
    return "CE-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _stage(status: str, reason: str) -> dict[str, str]:
    return {"status": status, "reason": reason}


def audit_record(record: dict[str, Any], *, registry: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return one conservative lineage row for every completed-evidence item."""
    trace = record.get("trace") or {}
    response = record.get("response") or {}
    audit = trace.get("grounding_audit") or {}
    items = trace.get("completed_evidence") or trace.get("completion_candidates") or []
    public_evidence = response.get("evidence") or []
    claims = response.get("claims") or []
    answer_points = trace.get("answer_plan") or []
    candidate_chunks = {
        str(c)
        for point in audit.get("point_decisions") or []
        for c in point.get("candidate_chunk_ids") or []
    }
    rows: list[dict[str, Any]] = []
    for item in items:
        chunk_id = str(item.get("chunk_id", ""))
        generation_id = item.get("generation_id")
        trace_generation = trace.get("generation_id") or response.get("generation_id")
        registry_entry = (registry or {}).get(chunk_id)
        registry_status = "verified" if registry_entry else "unverifiable"
        registry_reason = (
            "matched supplied Context Registry entry"
            if registry_entry
            else "Context Registry payload was not included with saved result"
        )
        policy_status = "present" if item.get("completion_reason") and item.get("source_type") else "missing"
        policy_reason = (
            "completion_reason and bounded source_type persisted"
            if policy_status == "present"
            else "completion metadata is incomplete"
        )
        generation_status = "metadata_only" if trace.get("completion_applied") else "missing"
        generation_reason = (
            "completion marked applied but context text/provenance is absent from trace"
            if generation_status == "metadata_only"
            else "trace does not mark completion as applied"
        )
        provider_status = "unverifiable"
        provider_reason = "provider input/output boundary is not persisted in this result"
        grounding_status = "linked" if chunk_id in candidate_chunks else "not_linked"
        grounding_reason = (
            "chunk appears in grounding candidate_chunk_ids"
            if grounding_status == "linked"
            else "no grounding point references completed chunk"
        )
        point_refs = [
            str(point.get("point_id"))
            for point in answer_points
            if chunk_id in (point.get("evidence_ids") or [])
        ]
        answer_status = "linked" if point_refs else "not_linked"
        answer_reason = (
            "answer plan references completion identity"
            if point_refs
            else "AnswerPoint evidence_ids contain public E* identities only"
        )
        citation_refs = [
            str(e.get("citation_id"))
            for e in public_evidence
            if e.get("chunk_id") == chunk_id or e.get("evidence_id") == chunk_id
        ]
        claim_refs = [
            str(c.get("claim_id"))
            for c in claims
            if any(cid in (c.get("evidence_ids") or []) for cid in citation_refs)
        ]
        citation_status = "linked" if citation_refs or claim_refs else "not_linked"
        rows.append(
            {
                "completion_id": _completion_id(item),
                "split": record.get("split"),
                "question_id": record.get("question_id"),
                "request_id": response.get("request_id") or trace.get("request_id"),
                "trace_id": trace.get("trace_id"),
                "chunk_id": chunk_id,
                "document_id": item.get("document_id"),
                "document_name": item.get("document_name"),
                "page_number": item.get("page_number"),
                "completion_generation_id": generation_id,
                "trace_generation_id": trace_generation,
                "generation_match": generation_id == trace_generation,
                "source_type": item.get("source_type"),
                "context_role": item.get("context_role"),
                "used_for_answer": bool(item.get("used_for_answer")),
                "cited_in_answer": bool(item.get("cited_in_answer")),
                "answer_point_ids": point_refs,
                "claim_ids": claim_refs,
                "citation_ids": citation_refs,
                "stages": {
                    "registry": _stage(registry_status, registry_reason),
                    "policy": _stage(policy_status, policy_reason),
                    "generation_context": _stage(generation_status, generation_reason),
                    "provider": _stage(provider_status, provider_reason),
                    "grounding": _stage(grounding_status, grounding_reason),
                    "answer_point": _stage(answer_status, answer_reason),
                    "claim_citation": _stage(citation_status, "public evidence/citation identity match" if citation_status == "linked" else "no public evidence or claim maps to completion"),
                },
                "drop_reasons": [
                    reason
                    for reason, condition in (
                        ("generation_id_mismatch", generation_id != trace_generation),
                        ("not_referenced_by_grounding", grounding_status != "linked"),
                        ("not_referenced_by_answer_point", answer_status != "linked"),
                        ("not_referenced_by_claim_or_citation", citation_status != "linked"),
                        ("used_for_answer_false", not bool(item.get("used_for_answer"))),
                    )
                    if condition
                ],
            }
        )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter()
    for row in rows:
        for stage, value in row["stages"].items():
            counts[f"{stage}:{value['status']}"] += 1
    return {
        "record_count": len({(r.get("split"), r.get("question_id")) for r in rows}),
        "completion_count": len(rows),
        "expected_completion_count": 104,
        "completion_count_matches_expected": len(rows) == 104,
        "stage_counts": dict(sorted(counts.items())),
        "generation_mismatch_count": sum(not r["generation_match"] for r in rows),
        "used_for_answer_count": sum(r["used_for_answer"] for r in rows),
        "cited_in_answer_count": sum(r["cited_in_answer"] for r in rows),
        "drop_reason_counts": dict(Counter(reason for r in rows for reason in r["drop_reasons"])),
        "holdout_used": False,
        "audit_is_conservative": True,
    }

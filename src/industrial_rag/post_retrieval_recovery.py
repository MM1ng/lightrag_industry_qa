"""Bounded, deterministic post-retrieval coverage recovery diagnostics.

This module is intentionally side-effect free.  It does not issue another
retrieval request, call a model, change the grounding threshold, or use the
golden set as a runtime rule.  It evaluates the evidence already present in a
query trace and returns a bounded plan for an offline replay or a later
single-variable experiment.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from industrial_rag.conditional_completion import plan_conditional_completion
from industrial_rag.evidence_completion import ContextRecord

RecoveryKind = Literal[
    "none",
    "recalled_not_selected",
    "generation_omitted",
    "generation_refusal",
    "grounding_false_negative",
]


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    """A safe, auditable decision for one observed coverage failure."""

    kind: RecoveryKind
    action: str
    eligible: bool
    reason: str
    candidate_chunk_ids: tuple[str, ...] = ()
    accepted_chunk_ids: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "action": self.action,
            "eligible": self.eligible,
            "reason": self.reason,
            "candidate_chunk_ids": list(self.candidate_chunk_ids),
            "accepted_chunk_ids": list(self.accepted_chunk_ids),
            "missing_requirements": list(self.missing_requirements),
        }


def _records(items: Sequence[Mapping[str, Any]], *, generation_id: str | None = None) -> list[ContextRecord]:
    """Convert trace-shaped evidence into bounded registry records."""
    result: list[ContextRecord] = []
    for item in items:
        chunk_id = str(item.get("chunk_id") or "")
        if not chunk_id:
            continue
        result.append(
            ContextRecord(
                knowledge_base_id=str(item.get("knowledge_base_id") or ""),
                generation_id=str(item.get("generation_id") or generation_id or ""),
                document_id=str(item.get("document_id") or ""),
                document_name=str(item.get("document_name") or ""),
                chunk_id=chunk_id,
                text=str(item.get("text") or item.get("excerpt") or ""),
                page_start=int(item.get("page_number") or item.get("page_start") or 0),
                section_path=tuple(str(value) for value in (item.get("section_path") or ())),
                parent_chunk_id=item.get("parent_chunk_id"),
                previous_chunk_id=item.get("previous_chunk_id"),
                next_chunk_id=item.get("next_chunk_id"),
                table_id=item.get("table_id"),
                table_header_chunk_id=item.get("table_header_chunk_id"),
            )
        )
    return result


def evaluate_post_retrieval_recovery(
    *,
    question_type: str,
    selected: Sequence[Mapping[str, Any]],
    available_candidates: Sequence[Mapping[str, Any]] = (),
    registry: Mapping[str, ContextRecord] | None = None,
    coverage_requirements: Sequence[str] | None = None,
    provider_evidence_ids: Sequence[str] = (),
    generated_answer_point_ids: Sequence[str] = (),
    grounding_removed_point_ids: Sequence[str] = (),
    generation_status: str | None = None,
    negative_query: bool = False,
    max_recovery_candidates: int = 2,
) -> RecoveryDecision:
    """Classify one trace and produce a deterministic bounded recovery plan.

    ``available_candidates`` is restricted to already-retrieved candidates;
    it is never a prompt to perform supplemental retrieval.  ``registry`` is
    read-only and must belong to the same generation as ``selected``.
    """
    if max_recovery_candidates < 0:
        raise ValueError("max_recovery_candidates must be non-negative")
    selected_records = _records(selected)
    candidate_records = _records(available_candidates)
    all_records = {item.chunk_id: item for item in selected_records}
    all_records.update({item.chunk_id: item for item in candidate_records})
    context_registry = dict(registry or {})
    context_registry.update(all_records)

    plan = plan_conditional_completion(
        question_type,
        selected_records,
        context_registry,
        is_negative=negative_query,
        coverage_requirements=tuple(coverage_requirements) if coverage_requirements else None,
        max_completion=max_recovery_candidates,
    )
    selected_ids = {item.chunk_id for item in selected_records}
    recalled_not_selected = tuple(
        item.chunk_id
        for item in candidate_records
        if item.chunk_id not in selected_ids and item.generation_id == (selected_records[0].generation_id if selected_records else item.generation_id)
    )

    # A removed point with a provider-supported candidate is a diagnostic, not
    # permission to weaken grounding globally.
    if grounding_removed_point_ids and provider_evidence_ids:
        return RecoveryDecision(
            "grounding_false_negative",
            "grounding_review_replay",
            True,
            "provider_context_contains_evidence_but_grounding_removed_points",
            missing_requirements=plan.missing,
        )
    if not generated_answer_point_ids and generation_status in {"insufficient_evidence", "safety_blocked"}:
        return RecoveryDecision(
            "generation_refusal",
            "replay_with_same_context",
            bool(provider_evidence_ids),
            "provider_context_present" if provider_evidence_ids else "provider_context_missing",
            candidate_chunk_ids=recalled_not_selected[:max_recovery_candidates],
            accepted_chunk_ids=plan.accepted_chunk_ids,
            missing_requirements=plan.missing,
        )
    if not generated_answer_point_ids and provider_evidence_ids:
        return RecoveryDecision(
            "generation_omitted",
            "replay_with_same_context",
            True,
            "provider_context_present_without_answer_points",
            candidate_chunk_ids=recalled_not_selected[:max_recovery_candidates],
            accepted_chunk_ids=plan.accepted_chunk_ids,
            missing_requirements=plan.missing,
        )
    if recalled_not_selected and plan.accepted_chunk_ids:
        return RecoveryDecision(
            "recalled_not_selected",
            "bounded_context_selection_replay",
            True,
            "existing_candidates_add_missing_coverage",
            candidate_chunk_ids=recalled_not_selected[:max_recovery_candidates],
            accepted_chunk_ids=plan.accepted_chunk_ids,
            missing_requirements=plan.missing,
        )
    return RecoveryDecision("none", "no_action", False, "no_eligible_post_retrieval_failure", missing_requirements=plan.missing)


__all__ = ["RecoveryDecision", "evaluate_post_retrieval_recovery"]

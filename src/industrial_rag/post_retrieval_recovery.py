"""Bounded, deterministic post-retrieval coverage recovery diagnostics.

This module is intentionally side-effect free.  It does not issue another
retrieval request, call a model, change the grounding threshold, or use the
golden set as a runtime rule.  It evaluates the evidence already present in a
query trace and returns a bounded plan for an offline replay or a later
single-variable experiment.
"""

from __future__ import annotations

import re
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

_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
_NEGATION_TERMS = ("不得", "不能", "禁止", "严禁", "不可", "不应", "无需", "无须", "不要")


def _normalise(value: Any) -> str:
    return "".join(str(value or "").casefold().split())


def _values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(str(item) for item in value if str(item).strip())
    return ()


def _numbers(value: str) -> set[str]:
    return {item.replace(" ", "") for item in _NUMBER_RE.findall(value)}


def _negated(value: str) -> bool:
    return any(term in value for term in _NEGATION_TERMS)


def has_exact_false_negative_support(
    point: Mapping[str, Any], evidence: Mapping[str, Any],
) -> bool:
    """Require field-level support before labelling a grounding removal false-negative.

    This deliberately rejects inference from loose token overlap.  Every
    declared object, parameter, condition, model, numeric value and unit must
    occur in the same already-provider-visible evidence text; negation must
    agree.  It is a diagnostic predicate only and never relaxes grounding.
    """
    text = str(point.get("text") or point.get("content") or "")
    body = str(evidence.get("text") or evidence.get("excerpt") or "")
    if not text.strip() or not body.strip():
        return False
    point_negated = bool(point.get("negated")) or _negated(text)
    if point_negated != _negated(body):
        return False
    normalised_body = _normalise(body)
    for key in ("object", "parameter", "model"):
        field = _normalise(point.get(key))
        if field and field not in normalised_body:
            return False
    for condition in _values(point.get("conditions", point.get("condition"))):
        if _normalise(condition) not in normalised_body:
            return False
    required_numbers = _numbers(text) | {
        _normalise(value) for value in _values(point.get("numeric_values", point.get("values")))
    }
    if required_numbers and not required_numbers.issubset(_numbers(body)):
        return False
    for unit in _values(point.get("units", point.get("unit"))):
        if _normalise(unit) not in normalised_body:
            return False
    return True


def _has_exact_removed_point_support(
    removed_points: Sequence[Mapping[str, Any]],
    evidence: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]],
    provider_evidence_ids: Sequence[str],
) -> bool:
    if isinstance(evidence, Mapping):
        registry = {str(key): value for key, value in evidence.items() if isinstance(value, Mapping)}
    else:
        registry = {
            str(item.get("evidence_id") or item.get("chunk_id") or ""): item
            for item in evidence
            if isinstance(item, Mapping)
        }
    provider_ids = set(provider_evidence_ids)
    for point in removed_points:
        point_ids = _values(point.get("evidence_ids", point.get("evidence_id")))
        # A diagnostic must name an evidence item actually delivered to the
        # provider.  No implicit candidate scan is permitted.
        for evidence_id in point_ids:
            item = registry.get(evidence_id)
            if item is not None and evidence_id in provider_ids and has_exact_false_negative_support(point, item):
                return True
    return False


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
    grounding_removed_points: Sequence[Mapping[str, Any]] = (),
    grounding_evidence_registry: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]] = (),
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

    # A false-negative requires exact, provider-visible support.  IDs alone
    # are intentionally insufficient: they cannot prove object/parameter,
    # numeric, unit, condition, model, or negation agreement.
    if (
        grounding_removed_point_ids
        and provider_evidence_ids
        and not negative_query
        and _has_exact_removed_point_support(
            grounding_removed_points, grounding_evidence_registry, provider_evidence_ids
        )
    ):
        return RecoveryDecision(
            "grounding_false_negative",
            "grounding_review_replay",
            True,
            "exact_provider_evidence_supports_removed_point",
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


__all__ = [
    "RecoveryDecision",
    "evaluate_post_retrieval_recovery",
    "has_exact_false_negative_support",
]

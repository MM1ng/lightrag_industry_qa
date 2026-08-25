"""Deterministic validation for evidence-bound answer points.

This module deliberately does not call an LLM.  It validates the provider's
structured output against the request generation and the server-side evidence
registry, retaining valid points independently of invalid ones.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from .evidence_answer_schema import (
    EvidenceRef,
    PointValidation,
    StructuredAnswerPoint,
    SupportValidation,
)
from .structured_generation_parser import parse_structured_answer

_TOKEN_RE = re.compile(r"[A-Za-z0-9_./%°℃+-]+|[\u4e00-\u9fff]{2,}")
_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
_NEGATIVE = ("不得", "不能", "禁止", "严禁", "不可", "不应", "无需", "无须", "不要")
_GENERIC = frozenset(("根据", "手册", "设备", "内容", "相关", "如下", "进行", "需要"))


def _tokens(text: str) -> set[str]:
    return {t.casefold() for t in _TOKEN_RE.findall(text) if t not in _GENERIC}


def _negated(text: str) -> bool:
    return any(word in text for word in _NEGATIVE)


def _numbers(text: str) -> set[str]:
    return {item.replace(" ", "") for item in _NUMBER_RE.findall(text)}


def _supports_text(point: StructuredAnswerPoint, evidence: EvidenceRef) -> bool:
    claim = point.text
    body = evidence.text
    if not body.strip():
        return False
    # A prohibition must not be supported by a permissive sentence (and vice
    # versa).  This prevents token-overlap false positives around "不".
    if _negated(claim) != _negated(body):
        return False
    claim_tokens = _tokens(" ".join(filter(None, (claim, point.object, point.parameter, point.model))))
    body_tokens = _tokens(body)
    if claim_tokens and not claim_tokens.intersection(body_tokens):
        return False
    # Structured fields are commitments, not hints.  A point that names an
    # object/parameter/model must name one that occurs in this evidence.
    lowered = body.casefold().replace(" ", "")
    for field in (point.object, point.parameter, point.model):
        normalised = (field or "").casefold().replace(" ", "")
        if normalised and normalised not in lowered:
            return False
    required_numbers = _numbers(claim) | set(point.numeric_values)
    if required_numbers and not required_numbers.issubset(_numbers(body)):
        return False
    if point.units and not all(unit.casefold().replace(" ", "") in lowered for unit in point.units):
        return False
    for condition in point.conditions:
        normalised = condition.casefold().replace(" ", "")
        if normalised and normalised not in lowered:
            return False
    return True


def _registry(values: Mapping[str, EvidenceRef] | Iterable[EvidenceRef] | Mapping[str, Any]) -> dict[str, EvidenceRef]:
    if isinstance(values, Mapping):
        result: dict[str, EvidenceRef] = {}
        for key, value in values.items():
            item = value if isinstance(value, EvidenceRef) else EvidenceRef.from_mapping(value)
            result[str(key)] = item
        return result
    return {item.evidence_id: item for item in values}


def validate_answer_points(
    points: Iterable[StructuredAnswerPoint],
    evidence_registry: Mapping[str, EvidenceRef] | Iterable[EvidenceRef] | Mapping[str, Any],
    *,
    generation_id: str,
    safety_question: bool = False,
) -> SupportValidation:
    """Validate each point independently and remove unknown evidence IDs.

    Parent and context-only entries are accepted only if they carry a real
    child ``citation_id``.  The returned evidence IDs remain registry IDs; the
    caller can use ``EvidenceRef.citation_id`` for the public citation.
    """

    registry = _registry(evidence_registry)
    results: list[PointValidation] = []
    retained: list[StructuredAnswerPoint] = []
    invalid: list[str] = []
    for point in points:
        valid_ids: list[str] = []
        reasons: list[str] = []
        for evidence_id in dict.fromkeys(point.evidence_ids):
            evidence = registry.get(evidence_id)
            if evidence is None:
                reasons.append("unknown_evidence_id")
                continue
            if evidence.generation_id != generation_id:
                reasons.append("wrong_generation")
                continue
            if (evidence.context_role == "context_only" or not evidence.is_child) and not evidence.citation_id:
                reasons.append("parent_without_child_citation")
                continue
            if _supports_text(point, evidence):
                valid_ids.append(evidence_id)
            else:
                reasons.append("evidence_does_not_support_point")
        clean = StructuredAnswerPoint(
            point_id=point.point_id,
            text=point.text,
            evidence_ids=tuple(valid_ids),
            object=point.object,
            parameter=point.parameter,
            numeric_values=point.numeric_values,
            units=point.units,
            conditions=point.conditions,
            model=point.model,
            negated=point.negated,
            step_index=point.step_index,
            step_relation=point.step_relation,
        )
        ok = bool(valid_ids)
        results.append(PointValidation(clean, ok, tuple(valid_ids), ";".join(dict.fromkeys(reasons)) or None))
        if ok:
            retained.append(clean)
        else:
            invalid.append(point.point_id)
    if retained and invalid:
        status = "partial_answer"
    elif retained:
        status = "success"
    elif safety_question and results:
        status = "safety_blocked"
    else:
        status = "insufficient_evidence"
    return SupportValidation(tuple(retained), tuple(results), status, tuple(invalid))


def validate_structured_answer(
    answer: str,
    points: Iterable[StructuredAnswerPoint],
    evidence_registry: Mapping[str, EvidenceRef] | Iterable[EvidenceRef] | Mapping[str, Any],
    *,
    generation_id: str,
    safety_question: bool = False,
) -> tuple[str, SupportValidation]:
    """Return a safe status and answer assembled only from retained points."""

    validation = validate_answer_points(
        points, evidence_registry, generation_id=generation_id, safety_question=safety_question
    )
    if not validation.points:
        return ("", validation)
    retained_text = "\n".join(point.text for point in validation.points)
    return (retained_text or answer, validation)


def resolve_partial_generation(
    provider_payload: Any,
    *,
    fallback_answer: str,
    evidence_registry: Mapping[str, EvidenceRef] | Iterable[EvidenceRef] | Mapping[str, Any],
    generation_id: str,
    safety_question: bool = False,
) -> tuple[str, SupportValidation | None, str | None]:
    """Apply structured partial-answer validation with a one-call-safe fallback.

    The provider payload is parsed once.  If its schema is malformed, the
    existing answer is returned untouched and no validation/retry path is
    attempted.  On a valid schema each supported point is retained
    independently by :func:`validate_answer_points`.
    """
    parsed = parse_structured_answer(provider_payload, fallback_answer=fallback_answer)
    if parsed.parse_error:
        return fallback_answer, None, parsed.parse_error
    answer, validation = validate_structured_answer(
        parsed.answer,
        parsed.points,
        evidence_registry,
        generation_id=generation_id,
        safety_question=safety_question,
    )
    return answer, validation, None


__all__ = [
    "resolve_partial_generation",
    "validate_answer_points",
    "validate_structured_answer",
]


"""Deterministically match a public claim to registry evidence.

The matcher is intentionally offline-only.  It accepts no model output beyond
the claim mapping and never promotes parent/context records into public
citations.  A claim is supported when at least one of its declared evidence
IDs is a same-generation public child citation whose text passes the existing
deterministic support policy.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from industrial_rag.evidence_answer_schema import EvidenceRef, StructuredAnswerPoint
from industrial_rag.structured_generation_policy import validate_answer_points


@dataclass(frozen=True, slots=True)
class ClaimSupportResult:
    """The independently resolved public support for one claim."""

    claim: dict[str, Any]
    supported: bool
    valid_evidence_ids: tuple[str, ...]
    invalid_evidence_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]


def _as_id(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _dedupe_ids(values: Iterable[Any]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = _as_id(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _registry(values: Mapping[str, EvidenceRef] | Mapping[str, Mapping[str, Any]]) -> dict[str, EvidenceRef]:
    result: dict[str, EvidenceRef] = {}
    for key, value in values.items():
        if isinstance(value, EvidenceRef):
            result[str(key)] = value
            continue
        raw = dict(value)
        if "is_child" in raw:
            raw["is_child"] = _as_bool(raw["is_child"])
        result[str(key)] = EvidenceRef.from_mapping(raw)
    return result


def _point_from_claim(claim: Mapping[str, Any], evidence_id: str) -> StructuredAnswerPoint:
    return StructuredAnswerPoint(
        point_id=_as_id(claim.get("claim_id")) or "claim",
        text=str(claim.get("text") or ""),
        evidence_ids=(evidence_id,),
        object=_as_id(claim.get("object")) or None,
        parameter=_as_id(claim.get("parameter")) or None,
        numeric_values=_dedupe_ids(claim.get("numeric_values", ())),
        units=_dedupe_ids(claim.get("units", ())),
        conditions=_dedupe_ids(claim.get("conditions", ())),
        model=_as_id(claim.get("model")) or None,
        negated=bool(claim.get("negated", False)),
    )


_CJK_TERM_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_GENERIC_CJK_TERMS = frozenset(("根据", "手册", "设备", "内容", "相关", "如下", "进行", "需要"))


def _has_claim_subject_overlap(claim: Mapping[str, Any], evidence: EvidenceRef) -> bool:
    """Require a real Chinese subject match when the claim has one.

    Numbers and units alone cannot establish semantic support: otherwise two
    unrelated statements that both mention ``5 MPa`` would be linked.
    """

    claim_terms = {
        term
        for term in _CJK_TERM_RE.findall(str(claim.get("text") or ""))
        if term not in _GENERIC_CJK_TERMS
    }
    if not claim_terms:
        return True
    evidence_terms = set(_CJK_TERM_RE.findall(evidence.text))
    return bool(claim_terms.intersection(evidence_terms))


def match_claim_support(
    claim: Mapping[str, Any],
    evidence_registry: Mapping[str, EvidenceRef] | Mapping[str, Mapping[str, Any]],
    *,
    expected_generation_id: str,
) -> ClaimSupportResult:
    """Resolve supported evidence IDs without creating a public citation.

    Registry identity is authoritative.  Unknown IDs, cross-generation rows,
    parents/context-only rows, and entries without an actual child citation are
    invalid.  Remaining records must also pass the deterministic semantic
    support policy, so a claim that has one bad and one good evidence ID keeps
    only the independently supported ID.
    """

    registry = _registry(evidence_registry)
    valid: list[str] = []
    invalid: list[str] = []
    reasons: list[str] = []
    for evidence_id in _dedupe_ids(claim.get("evidence_ids", ())):
        evidence = registry.get(evidence_id)
        if evidence is None or evidence.evidence_id != evidence_id:
            invalid.append(evidence_id)
            reasons.append("unknown_evidence_id")
            continue
        if evidence.generation_id != expected_generation_id:
            invalid.append(evidence_id)
            reasons.append("wrong_generation")
            continue
        if not evidence.is_child or evidence.context_role == "context_only":
            invalid.append(evidence_id)
            reasons.append("parent_not_public_citation")
            continue
        if not _as_id(evidence.citation_id):
            invalid.append(evidence_id)
            reasons.append("missing_public_citation")
            continue
        validation = validate_answer_points(
            (_point_from_claim(claim, evidence_id),),
            {evidence_id: evidence},
            generation_id=expected_generation_id,
        )
        if validation.points and _has_claim_subject_overlap(claim, evidence):
            valid.append(evidence_id)
        else:
            invalid.append(evidence_id)
            reasons.append("evidence_does_not_support_claim")

    output = dict(claim)
    output["evidence_ids"] = valid
    return ClaimSupportResult(
        claim=output,
        supported=bool(valid),
        valid_evidence_ids=tuple(valid),
        invalid_evidence_ids=tuple(invalid),
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


__all__ = ["ClaimSupportResult", "match_claim_support"]

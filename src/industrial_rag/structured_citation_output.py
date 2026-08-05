"""Request-local structured citation contracts.

This module is deliberately limited to deterministic source and requirement
identity.  It never asks a model whether a source semantically entails text.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from industrial_rag.evidence_answer_schema import EvidenceRef

StructuredStatus = Literal["success", "partial_answer", "insufficient_evidence"]
FallbackMode = Literal[
    "fallback_to_j0_postprocessing", "safe_failure_no_second_generation"
]


def _sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceEntry:
    source_id: str
    evidence: EvidenceRef
    content_sha256: str

    def trace_payload(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "evidence_id": self.evidence.evidence_id,
            "chunk_id": self.evidence.chunk_id,
            "generation_id": self.evidence.generation_id,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True, slots=True)
class SourceRegistry:
    entries: tuple[SourceEntry, ...]

    @classmethod
    def from_evidence(cls, evidence: tuple[EvidenceRef, ...]) -> SourceRegistry:
        entries = tuple(
            SourceEntry(
                source_id=f"S{index}",
                evidence=item,
                content_sha256=hashlib.sha256(item.text.encode("utf-8")).hexdigest(),
            )
            for index, item in enumerate(evidence, 1)
            if item.is_child and bool(item.citation_id) and bool(item.text.strip())
        )
        return cls(entries)

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(entry.source_id for entry in self.entries)

    @property
    def sha256(self) -> str:
        return _sha256([entry.trace_payload() for entry in self.entries])

    def resolve(self, source_id: str) -> EvidenceRef | None:
        for entry in self.entries:
            if entry.source_id == source_id:
                return entry.evidence
        return None


@dataclass(frozen=True, slots=True)
class RequirementEntry:
    requirement_id: str
    label: str


@dataclass(frozen=True, slots=True)
class RequirementRegistry:
    entries: tuple[RequirementEntry, ...]

    @classmethod
    def from_requirements(cls, requirements: tuple[str, ...]) -> RequirementRegistry:
        return cls(
            tuple(
                RequirementEntry(f"R{index}", label)
                for index, label in enumerate(requirements, 1)
                if label.strip()
            )
        )

    @property
    def requirement_ids(self) -> tuple[str, ...]:
        return tuple(entry.requirement_id for entry in self.entries)

    @property
    def sha256(self) -> str:
        return _sha256(
            [
                {"requirement_id": entry.requirement_id, "label": entry.label}
                for entry in self.entries
            ]
        )


class ProviderAnswerPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: StrictStr = Field(min_length=1)
    source_ids: list[StrictStr]


class ProviderStructuredOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: StructuredStatus | None = None
    answer_points: list[ProviderAnswerPoint]
    unresolved_requirement_ids: list[StrictStr] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StructuredCitationPoint:
    text: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StructuredCitationDecision:
    valid: bool
    status: StructuredStatus
    answer_points: tuple[StructuredCitationPoint, ...]
    unresolved_requirement_ids: tuple[str, ...]
    fallback_mode: FallbackMode | None = None
    fallback_reason: str | None = None
    raw_response_sha256: str = ""
    parsed_output_sha256: str | None = None


def render_public_citation_numbers(
    points: tuple[StructuredCitationPoint, ...],
) -> tuple[tuple[int, ...], ...]:
    """Allocate public citation numbers by first answer appearance."""

    assigned: dict[str, int] = {}
    rendered: list[tuple[int, ...]] = []
    for point in points:
        numbers: list[int] = []
        for source_id in point.source_ids:
            if source_id not in assigned:
                assigned[source_id] = len(assigned) + 1
            numbers.append(assigned[source_id])
        rendered.append(tuple(numbers))
    return tuple(rendered)


def _safe_failure(payload: str, reason: str) -> StructuredCitationDecision:
    return StructuredCitationDecision(
        valid=False,
        status="insufficient_evidence",
        answer_points=(),
        unresolved_requirement_ids=(),
        fallback_mode="safe_failure_no_second_generation",
        fallback_reason=reason,
        raw_response_sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    )


def _citation_fallback(
    *,
    points: tuple[StructuredCitationPoint, ...],
    unresolved: tuple[str, ...],
    payload: str,
    parsed_sha: str,
    reasons: list[str],
) -> StructuredCitationDecision:
    return StructuredCitationDecision(
        valid=False,
        status="partial_answer" if unresolved else "success",
        answer_points=points,
        unresolved_requirement_ids=unresolved,
        fallback_mode="fallback_to_j0_postprocessing",
        fallback_reason=";".join(dict.fromkeys(reasons)),
        raw_response_sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        parsed_output_sha256=parsed_sha,
    )


def validate_structured_citation_output(
    payload: str,
    registry: SourceRegistry,
    requirements: RequirementRegistry,
    generation_id: str,
) -> StructuredCitationDecision:
    """Validate the minimum output contract and derive its consistent status."""

    raw_sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    try:
        parsed = ProviderStructuredOutput.model_validate_json(payload)
    except ValidationError as error:
        return _safe_failure(payload, f"core_schema_invalid:{error.errors()[0]['type']}")
    points = tuple(
        StructuredCitationPoint(point.text, tuple(point.source_ids))
        for point in parsed.answer_points
    )
    unresolved = tuple(parsed.unresolved_requirement_ids)
    parsed_sha = _sha256(parsed.model_dump(mode="json"))
    reasons: list[str] = []
    for point in points:
        if len(point.source_ids) > 2:
            reasons.append("too_many_source_ids")
        if len(set(point.source_ids)) != len(point.source_ids):
            reasons.append("duplicate_source_id")
        for source_id in point.source_ids:
            source = registry.resolve(source_id)
            if source is None:
                reasons.append("unknown_source_id")
            elif source.generation_id != generation_id:
                reasons.append("wrong_generation")
            elif not source.is_child or not source.citation_id or not source.text.strip():
                reasons.append("parent_without_child_mapping")
    if len(set(unresolved)) != len(unresolved):
        reasons.append("duplicate_requirement_id")
    if any(requirement_id not in requirements.requirement_ids for requirement_id in unresolved):
        reasons.append("unknown_requirement_id")
    if reasons:
        return _citation_fallback(
            points=points,
            unresolved=unresolved,
            payload=payload,
            parsed_sha=parsed_sha,
            reasons=reasons,
        )
    status: StructuredStatus
    if not points:
        status = "insufficient_evidence"
    elif unresolved:
        status = "partial_answer"
    else:
        status = "success"
    return StructuredCitationDecision(
        valid=True,
        status=status,
        answer_points=points,
        unresolved_requirement_ids=unresolved,
        raw_response_sha256=raw_sha,
        parsed_output_sha256=parsed_sha,
    )

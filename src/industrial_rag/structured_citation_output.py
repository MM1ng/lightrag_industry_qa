"""Request-local structured citation contracts.

This module is deliberately limited to deterministic source and requirement
identity.  It never asks a model whether a source semantically entails text.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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

    text: str
    source_ids: list[str]


class ProviderStructuredOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_points: list[ProviderAnswerPoint]
    unresolved_requirement_ids: list[str] = Field(default_factory=list)


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


def validate_structured_citation_output(
    payload: str,
    registry: SourceRegistry,
    requirements: RequirementRegistry,
    generation_id: str,
) -> StructuredCitationDecision:
    """Validate the minimum output contract and derive its consistent status."""

    raw_sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    parsed = ProviderStructuredOutput.model_validate_json(payload)
    points = tuple(
        StructuredCitationPoint(point.text, tuple(point.source_ids))
        for point in parsed.answer_points
    )
    unresolved = tuple(parsed.unresolved_requirement_ids)
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
        parsed_output_sha256=_sha256(parsed.model_dump(mode="json")),
    )

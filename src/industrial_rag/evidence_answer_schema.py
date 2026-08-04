"""Small, dependency-free schema for evidence-bound generated answers.

The schema is intentionally separate from the runtime query models.  It can be
used at the provider boundary or in replay tools without changing the public
``QueryResponse`` contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EvidenceSource = Literal[
    "initial",
    "parent_context",
    "adjacent",
    "table_header",
    "table_body",
    "multi_evidence_completion",
]
ContextRole = Literal["primary", "supporting", "context_only"]


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """A registry entry that may support one or more answer points.

    ``citation_id`` is the public child-chunk citation.  Parent/adjacent
    context can be used for reasoning, but may only be exposed as a citation
    when this field points at a real child chunk.
    """

    evidence_id: str
    chunk_id: str
    generation_id: str
    document_id: str = ""
    document_name: str = ""
    citation_id: str | None = None
    source_type: EvidenceSource = "initial"
    context_role: ContextRole = "primary"
    parent_chunk_id: str | None = None
    text: str = ""
    is_child: bool = True

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> EvidenceRef:
        return cls(
            evidence_id=str(value.get("evidence_id", "")),
            chunk_id=str(value.get("chunk_id", "")),
            generation_id=str(value.get("generation_id", "")),
            document_id=str(value.get("document_id", "")),
            document_name=str(value.get("document_name", "")),
            citation_id=value.get("citation_id"),
            source_type=value.get("source_type", "initial"),
            context_role=value.get("context_role", "primary"),
            parent_chunk_id=value.get("parent_chunk_id"),
            text=str(value.get("text", value.get("excerpt", ""))),
            is_child=bool(value.get("is_child", value.get("citation_id") is not None)),
        )


@dataclass(frozen=True, slots=True)
class StructuredAnswerPoint:
    point_id: str
    text: str
    evidence_ids: tuple[str, ...] = ()
    object: str | None = None
    parameter: str | None = None
    numeric_values: tuple[str, ...] = ()
    units: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    model: str | None = None
    negated: bool = False
    step_index: int | None = None
    step_relation: str | None = None

    @property
    def content(self) -> str:
        """Compatibility name used by the existing ``AnswerPoint`` model."""

        return self.text

    def to_payload(self) -> dict[str, Any]:
        return {
            "point_id": self.point_id,
            "text": self.text,
            "evidence_ids": list(self.evidence_ids),
            "object": self.object,
            "parameter": self.parameter,
            "numeric_values": list(self.numeric_values),
            "units": list(self.units),
            "conditions": list(self.conditions),
            "model": self.model,
            "negated": self.negated,
            "step_index": self.step_index,
            "step_relation": self.step_relation,
        }


@dataclass(frozen=True, slots=True)
class StructuredAnswer:
    answer: str
    points: tuple[StructuredAnswerPoint, ...] = ()
    status: Literal["success", "partial_answer", "insufficient_evidence", "safety_blocked"] = "success"
    parse_error: str | None = None

    @property
    def answer_points(self) -> tuple[StructuredAnswerPoint, ...]:
        return self.points


@dataclass(frozen=True, slots=True)
class PointValidation:
    point: StructuredAnswerPoint
    valid: bool
    valid_evidence_ids: tuple[str, ...]
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SupportValidation:
    points: tuple[StructuredAnswerPoint, ...]
    point_results: tuple[PointValidation, ...]
    status: Literal["success", "partial_answer", "insufficient_evidence", "safety_blocked"]
    invalid_point_ids: tuple[str, ...] = field(default_factory=tuple)

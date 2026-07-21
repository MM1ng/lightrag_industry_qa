"""Validated domain contracts shared by workflows, tools, persistence, and API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Final, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from industrial_energy_agent.domain.enums import (
    EvidenceGrade,
    ReviewStatus,
    ReviewType,
    RiskLevel,
    SourceType,
    WorkOrderStatus,
)
from industrial_energy_agent.domain.errors import (
    contains_sensitive_or_internal_text,
    is_sensitive_field_name,
)

INDUSTRIAL_SAFETY_DISCLAIMER: Final[
    Literal[
        "本系统仅用于工业运维辅助分析\N{FULLWIDTH COMMA}"
        "不能替代现场规程、持证人员判断或正式操作票。"
    ]
] = "本系统仅用于工业运维辅助分析\N{FULLWIDTH COMMA}不能替代现场规程、持证人员判断或正式操作票。"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class _DomainModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        hide_input_in_errors=True,
    )


class _CitationBase(_DomainModel):
    citation_id: str = Field(min_length=1, max_length=256)
    excerpt: str | None = Field(default=None, min_length=1, max_length=2000)


class ManualCitation(_CitationBase):
    """A physical-page citation resolved through the local ingestion manifest."""

    source_type: Literal[SourceType.MANUAL] = SourceType.MANUAL
    source_file: str | None = Field(default=None, min_length=1, max_length=512)
    document_title: str = Field(min_length=1, max_length=512)
    page_number: int = Field(ge=1)
    section_title: str | None = Field(default=None, min_length=1, max_length=512)
    chunk_id: str = Field(min_length=1, max_length=512)

    dataset: None = None
    cycle_id: None = None
    artifact_version: None = None
    features: None = None
    units: None = None
    entity_id: None = None
    case_id: None = None
    data_type: None = None


class SensorCitation(_CitationBase):
    """A citation to deterministic cycle-level sensor artifacts."""

    source_type: Literal[SourceType.SENSOR] = SourceType.SENSOR
    source_file: None = None
    document_title: None = None
    page_number: None = None
    section_title: None = None
    chunk_id: None = None

    dataset: str = Field(min_length=1, max_length=256)
    cycle_id: int = Field(ge=1)
    artifact_version: str = Field(min_length=1, max_length=256)
    features: dict[str, float] = Field(min_length=1, max_length=176)
    units: dict[str, str] = Field(min_length=1, max_length=176)

    entity_id: None = None
    case_id: None = None
    data_type: None = None

    @model_validator(mode="after")
    def feature_units_match(self) -> Self:
        if self.features.keys() != self.units.keys():
            raise ValueError("features and units must contain identical keys")
        if any(not name.strip() for name in self.features):
            raise ValueError("feature names must be non-empty")
        if any(not unit.strip() for unit in self.units.values()):
            raise ValueError("feature units must be non-empty")
        return self


class SyntheticCitation(_CitationBase):
    """A visibly marked citation to generated demonstration data."""

    source_type: Literal[SourceType.SYNTHETIC_CASE] = SourceType.SYNTHETIC_CASE
    source_file: str | None = Field(default=None, min_length=1, max_length=512)
    document_title: None = None
    page_number: None = None
    section_title: None = None
    chunk_id: None = None
    dataset: None = None
    cycle_id: None = None
    artifact_version: None = None
    features: None = None
    units: None = None

    entity_id: str | None = Field(default=None, min_length=1, max_length=256)
    case_id: str | None = Field(default=None, min_length=1, max_length=256)
    data_type: Literal["synthetic_demo"] = "synthetic_demo"

    @model_validator(mode="after")
    def stable_identifier_is_present(self) -> Self:
        if self.entity_id is None and self.case_id is None:
            raise ValueError("a synthetic citation requires entity_id or case_id")
        return self


Citation: TypeAlias = Annotated[
    ManualCitation | SensorCitation | SyntheticCitation,
    Field(discriminator="source_type"),
]


TraceScalar: TypeAlias = str | int | float | bool | None


class TraceEvent(_DomainModel):
    """A bounded observability record that deliberately excludes hidden reasoning."""

    request_id: str = Field(min_length=1, max_length=128)
    node: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=128)
    status: Literal["success", "failure", "skipped"]
    duration_ms: float = Field(ge=0)
    tool: str | None = Field(default=None, min_length=1, max_length=128)
    evidence_count: int = Field(default=0, ge=0)
    parameter_summary: dict[str, TraceScalar] = Field(default_factory=dict, max_length=10)
    error_code: str | None = Field(
        default=None,
        min_length=2,
        max_length=64,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )

    @model_validator(mode="after")
    def trace_is_sanitized_and_consistent(self) -> Self:
        prohibited_exact_keys = {"prompt", "reasoning", "chain_of_thought", "raw_sensor_data"}
        for key, value in self.parameter_summary.items():
            if len(key) > 64:
                raise ValueError("parameter name exceeds 64 characters")
            if key.casefold() in prohibited_exact_keys or is_sensitive_field_name(key):
                raise ValueError("parameter_summary contains a prohibited key")
            if isinstance(value, str):
                if len(value) > 256:
                    raise ValueError("parameter string exceeds 256 characters")
                if contains_sensitive_or_internal_text(value):
                    raise ValueError("parameter_summary contains a prohibited value")
        if self.status == "failure" and self.error_code is None:
            raise ValueError("failure trace events require an error_code")
        if self.status != "failure" and self.error_code is not None:
            raise ValueError("only failure trace events may carry an error_code")
        return self


class CandidateCause(_DomainModel):
    """A relative ordering signal, explicitly not an industrial fault probability."""

    cause: str = Field(min_length=1, max_length=500)
    ranking_score: float = Field(ge=0, le=1)
    supporting_citation_ids: list[str] = Field(default_factory=list, max_length=50)


class DiagnosisRecord(_DomainModel):
    """A diagnosis that keeps observations, evidence, inference, and unknowns separate."""

    diagnosis_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    equipment: str = Field(min_length=1, max_length=500)
    observed_anomalies: list[str]
    manual_evidence: list[ManualCitation]
    sensor_evidence: list[SensorCitation]
    synthetic_case_evidence: list[SyntheticCitation]
    candidate_causes: list[CandidateCause]
    recommended_checks: list[str]
    risk_level: RiskLevel
    approval_required: bool
    evidence_grade: EvidenceGrade
    limitations: list[str]
    unknowns: list[str]
    disclaimer: Literal[
        "本系统仅用于工业运维辅助分析\N{FULLWIDTH COMMA}"
        "不能替代现场规程、持证人员判断或正式操作票。"
    ] = INDUSTRIAL_SAFETY_DISCLAIMER


class WorkOrderDraft(_DomainModel):
    """A reviewable document that can never represent an executed industrial action."""

    work_order_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    diagnosis_id: str = Field(min_length=1, max_length=128)
    equipment: str = Field(min_length=1, max_length=500)
    symptom: str = Field(min_length=1, max_length=2000)
    candidate_causes: list[str] = Field(min_length=1)
    checks: list[str] = Field(min_length=1)
    safety_items: list[str] = Field(min_length=1)
    status: Literal[WorkOrderStatus.DRAFT] = WorkOrderStatus.DRAFT
    approval_status: ReviewStatus = ReviewStatus.PENDING_REVIEW
    executed: Literal[False] = False
    created_at: datetime = Field(default_factory=_utc_now)


class _ReviewBase(_DomainModel):
    review_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=256)
    status: ReviewStatus = ReviewStatus.PENDING_REVIEW
    decision: str | None = Field(default=None, min_length=1, max_length=2000)
    reviewer_id: str | None = Field(default=None, min_length=1, max_length=128)
    created_at: datetime = Field(default_factory=_utc_now)
    reviewed_at: datetime | None = None

    @model_validator(mode="after")
    def audit_fields_match_status(self) -> Self:
        audit_fields = (self.decision, self.reviewer_id, self.reviewed_at)
        if self.status is ReviewStatus.PENDING_REVIEW and any(
            value is not None for value in audit_fields
        ):
            raise ValueError("pending reviews cannot contain a human decision")
        if self.status is not ReviewStatus.PENDING_REVIEW and any(
            value is None for value in audit_fields
        ):
            raise ValueError("terminal reviews require decision, reviewer_id, and reviewed_at")
        return self


class RiskReview(_ReviewBase):
    """Human review of a restricted non-work-order response."""

    review_type: Literal[ReviewType.RISK_REVIEW] = ReviewType.RISK_REVIEW
    conversation_id: str = Field(min_length=1, max_length=128)
    risk_category: str = Field(min_length=1, max_length=128)
    restricted_answer_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class WorkOrderReview(_ReviewBase):
    """Human review linked to a real persisted work-order draft ID."""

    review_type: Literal[ReviewType.WORK_ORDER_REVIEW] = ReviewType.WORK_ORDER_REVIEW
    work_order_id: str = Field(min_length=1, max_length=128)


ReviewRecord: TypeAlias = Annotated[
    RiskReview | WorkOrderReview,
    Field(discriminator="review_type"),
]

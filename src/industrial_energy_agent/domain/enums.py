"""Stable wire enums for the EnergyOps domain."""

from enum import StrEnum


class Intent(StrEnum):
    """Supported public intents plus the internal low-confidence route."""

    EQUIPMENT_QA = "equipment_qa"
    OPERATION_PROCEDURE = "operation_procedure"
    SAFETY_QUERY = "safety_query"
    SENSOR_QUERY = "sensor_query"
    FAULT_DIAGNOSIS = "fault_diagnosis"
    WORK_ORDER_DRAFT = "work_order_draft"
    UNKNOWN = "unknown"


class SourceType(StrEnum):
    """Evidence provenance used as the citation discriminator."""

    MANUAL = "manual"
    SENSOR = "sensor"
    SYNTHETIC_CASE = "synthetic_case"


class RiskLevel(StrEnum):
    """Deterministic industrial-safety risk levels."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ActionMode(StrEnum):
    """Safety-relevant user action grammar."""

    INFORMATIONAL = "informational"
    PROCEDURE_REQUEST = "procedure_request"
    DRAFT_REQUEST = "draft_request"
    OPERATION_COMMAND = "operation_command"
    PROHIBITED_BYPASS = "prohibited_bypass"


class EvidenceGrade(StrEnum):
    """Completeness of the evidence available to a diagnosis."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class ReviewType(StrEnum):
    """Review records with deliberately separate targets and schemas."""

    RISK_REVIEW = "risk_review"
    WORK_ORDER_REVIEW = "work_order_review"


class ReviewStatus(StrEnum):
    """Human review lifecycle; review never represents equipment approval."""

    PENDING_REVIEW = "PENDING_REVIEW"
    REVIEWED = "REVIEWED"
    REJECTED = "REJECTED"


class WorkOrderStatus(StrEnum):
    """A work order is a draft for the whole MVP lifecycle."""

    DRAFT = "DRAFT"


class IngestJobStatus(StrEnum):
    """Persisted ingestion and reconciliation lifecycle."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RECONCILE_REQUIRED = "RECONCILE_REQUIRED"

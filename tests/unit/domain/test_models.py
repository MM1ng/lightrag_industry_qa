from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from industrial_energy_agent.domain import (
    INDUSTRIAL_SAFETY_DISCLAIMER,
    ActionMode,
    CandidateCause,
    Citation,
    DiagnosisRecord,
    EvidenceGrade,
    IngestJobStatus,
    Intent,
    ManualCitation,
    ReviewRecord,
    ReviewStatus,
    ReviewType,
    RiskLevel,
    RiskReview,
    SensorCitation,
    SourceType,
    StructuredError,
    SyntheticCitation,
    TraceEvent,
    WorkOrderDraft,
    WorkOrderReview,
    WorkOrderStatus,
)
from industrial_energy_agent.domain.errors import PUBLIC_INTERNAL_ERROR_MESSAGE


def _manual_citation(**overrides: object) -> ManualCitation:
    payload: dict[str, object] = {
        "citation_id": "manual:2196:p12:c3",
        "source_file": "2196-ANSI-Manual-Chinese.pdf",
        "document_title": "2196 ANSI 泵安装、运行与维护手册",
        "page_number": 12,
        "section_title": None,
        "chunk_id": "manual-2196-p0012-c003-a1b2c3d4",
        "excerpt": "启动前检查润滑和联轴器防护装置。",
    }
    payload.update(overrides)
    return ManualCitation.model_validate(payload)


def _sensor_citation(**overrides: object) -> SensorCitation:
    payload: dict[str, object] = {
        "citation_id": "sensor:1200:PS1__mean",
        "dataset": "UCI hydraulic_systems",
        "cycle_id": 1200,
        "artifact_version": "sha256:abc",
        "features": {"PS1__mean": 160.0},
        "units": {"PS1__mean": "bar"},
    }
    payload.update(overrides)
    return SensorCitation.model_validate(payload)


def _work_order_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "work_order_id": "wo-001",
        "request_id": "req-001",
        "conversation_id": "conv-001",
        "diagnosis_id": "diag-001",
        "equipment": "离心泵 P-001",
        "symptom": "出口压力下降",
        "candidate_causes": ["入口过滤器堵塞"],
        "checks": ["由具备资质的人员核对入口过滤器状态"],
        "safety_items": ["遵循现场隔离和正式操作票"],
    }
    payload.update(overrides)
    return payload


def test_contract_enums_use_the_documented_wire_values() -> None:
    assert {item.value for item in Intent} == {
        "equipment_qa",
        "operation_procedure",
        "safety_query",
        "sensor_query",
        "fault_diagnosis",
        "work_order_draft",
        "unknown",
    }
    assert {item.value for item in SourceType} == {"manual", "sensor", "synthetic_case"}
    assert {item.value for item in RiskLevel} == {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert {item.value for item in ActionMode} == {
        "informational",
        "procedure_request",
        "draft_request",
        "operation_command",
        "prohibited_bypass",
    }
    assert {item.value for item in EvidenceGrade} == {
        "COMPLETE",
        "PARTIAL",
        "INSUFFICIENT",
    }
    assert {item.value for item in ReviewType} == {"risk_review", "work_order_review"}
    assert {item.value for item in ReviewStatus} == {
        "PENDING_REVIEW",
        "REVIEWED",
        "REJECTED",
    }
    assert {item.value for item in WorkOrderStatus} == {"DRAFT"}
    assert {item.value for item in IngestJobStatus} == {
        "PENDING",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "RECONCILE_REQUIRED",
    }


def test_citation_union_discriminates_all_three_source_types() -> None:
    adapter: TypeAdapter[Citation] = TypeAdapter(Citation)

    manual = adapter.validate_python(_manual_citation().model_dump(mode="json"))
    sensor = adapter.validate_python(_sensor_citation().model_dump(mode="json"))
    synthetic = adapter.validate_python(
        {
            "citation_id": "case:case-001",
            "source_type": "synthetic_case",
            "source_file": "fault_cases.json",
            "case_id": "case-001",
            "data_type": "synthetic_demo",
        }
    )

    assert isinstance(manual, ManualCitation)
    assert isinstance(sensor, SensorCitation)
    assert isinstance(synthetic, SyntheticCitation)
    assert manual.source_type is SourceType.MANUAL
    assert sensor.source_type is SourceType.SENSOR
    assert synthetic.source_type is SourceType.SYNTHETIC_CASE


@pytest.mark.parametrize(
    ("citation", "inapplicable_fields"),
    [
        (
            _manual_citation(),
            {
                "dataset",
                "cycle_id",
                "artifact_version",
                "features",
                "units",
                "entity_id",
                "case_id",
                "data_type",
            },
        ),
        (
            _sensor_citation(),
            {
                "source_file",
                "document_title",
                "page_number",
                "section_title",
                "chunk_id",
                "entity_id",
                "case_id",
                "data_type",
            },
        ),
        (
            SyntheticCitation(
                citation_id="entity:equipment-001",
                source_file="equipment_master.csv",
                entity_id="equipment-001",
            ),
            {
                "document_title",
                "page_number",
                "section_title",
                "chunk_id",
                "dataset",
                "cycle_id",
                "artifact_version",
                "features",
                "units",
            },
        ),
    ],
)
def test_citation_serialization_keeps_inapplicable_fields_explicitly_null(
    citation: ManualCitation | SensorCitation | SyntheticCitation,
    inapplicable_fields: set[str],
) -> None:
    payload = citation.model_dump(mode="json")

    assert inapplicable_fields <= payload.keys()
    assert all(payload[field] is None for field in inapplicable_fields)


@pytest.mark.parametrize(
    "payload",
    [
        {"features": {}},
        {"units": {}},
        {"units": {"PS2__mean": "bar"}},
        {"page_number": 12},
    ],
)
def test_sensor_citation_requires_artifact_features_matching_units_and_no_manual_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _sensor_citation(**payload)


def test_synthetic_citation_requires_stable_id_and_synthetic_marker() -> None:
    with pytest.raises(ValidationError):
        SyntheticCitation(citation_id="case:missing-id")

    with pytest.raises(ValidationError):
        SyntheticCitation.model_validate(
            {
                "citation_id": "case:case-001",
                "case_id": "case-001",
                "data_type": "real_enterprise",
            }
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "REVIEWED"},
        {"executed": True},
    ],
)
def test_work_order_is_always_a_non_executed_draft(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        WorkOrderDraft.model_validate(_work_order_payload(**overrides))


def test_work_order_review_changes_only_approval_status_not_draft_or_execution() -> None:
    work_order = WorkOrderDraft.model_validate(_work_order_payload(approval_status="REVIEWED"))

    assert work_order.status is WorkOrderStatus.DRAFT
    assert work_order.approval_status is ReviewStatus.REVIEWED
    assert work_order.executed is False


def test_review_union_keeps_risk_and_work_order_records_distinct() -> None:
    adapter: TypeAdapter[ReviewRecord] = TypeAdapter(ReviewRecord)
    now = datetime.now(UTC)

    risk = adapter.validate_python(
        {
            "review_id": "review-risk-001",
            "review_type": "risk_review",
            "request_id": "req-001",
            "conversation_id": "conv-001",
            "risk_category": "operation_command",
            "restricted_answer_hash": "sha256:" + hashlib.sha256(b"restricted-answer").hexdigest(),
            "idempotency_key": "risk:req-001",
            "created_at": now,
        }
    )
    work_order = adapter.validate_python(
        {
            "review_id": "review-wo-001",
            "review_type": "work_order_review",
            "work_order_id": "wo-001",
            "request_id": "req-001",
            "idempotency_key": "work-order:wo-001",
            "created_at": now,
        }
    )

    assert isinstance(risk, RiskReview)
    assert risk.status is ReviewStatus.PENDING_REVIEW
    assert isinstance(work_order, WorkOrderReview)
    assert work_order.status is ReviewStatus.PENDING_REVIEW

    with pytest.raises(ValidationError):
        RiskReview.model_validate(
            {
                **risk.model_dump(),
                "work_order_id": "fake-work-order",
            }
        )

    with pytest.raises(ValidationError):
        WorkOrderReview.model_validate(
            {
                **work_order.model_dump(),
                "review_type": "risk_review",
            }
        )


def test_terminal_review_status_requires_complete_human_audit_fields() -> None:
    with pytest.raises(ValidationError):
        WorkOrderReview(
            review_id="review-wo-001",
            work_order_id="wo-001",
            request_id="req-001",
            idempotency_key="work-order:wo-001",
            status=ReviewStatus.REVIEWED,
        )

    reviewed = WorkOrderReview(
        review_id="review-wo-001",
        work_order_id="wo-001",
        request_id="req-001",
        idempotency_key="work-order:wo-001",
        status=ReviewStatus.REVIEWED,
        decision="reviewed",
        reviewer_id="operator-001",
        reviewed_at=datetime.now(UTC),
    )

    assert reviewed.status is ReviewStatus.REVIEWED


def test_structured_error_has_only_safe_public_fields_and_sanitizes_internal_details() -> None:
    error = StructuredError(
        code="INTERNAL_ERROR",
        message='Traceback (most recent call last): File "D:\\private\\service.py", line 9',
        retryable=False,
        request_id="req-001",
    )

    payload = error.model_dump(mode="json")
    rendered = error.model_dump_json()

    assert set(payload) == {"code", "message", "retryable", "request_id"}
    assert error.message == PUBLIC_INTERNAL_ERROR_MESSAGE
    assert "Traceback" not in rendered
    assert "D:\\private" not in rendered
    assert "service.py" not in rendered

    with pytest.raises(ValidationError):
        StructuredError.model_validate(
            {
                **payload,
                "stack_trace": "never expose this",
            }
        )


@pytest.mark.parametrize(
    "unsafe_message",
    [
        "LIGHTRAG_API_KEY=test-only-secret",
        "SERVICE_TOKEN=test-only-secret",
        "LANGFUSE_SECRET_KEY=test-only-secret",
        "x-api-key: test-only-secret",
        "password=test-only-secret",
        "Authorization: Bearer test-only-secret",
        "https://workspace-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        r"internal file C:\private\service.py",
        "internal file /private/project/service.py",
        "first line\nprivate detail",
    ],
)
def test_structured_error_replaces_every_sensitive_or_internal_message(
    unsafe_message: str,
) -> None:
    error = StructuredError(
        code="INTERNAL_ERROR",
        message=unsafe_message,
        retryable=False,
        request_id="req-001",
    )

    assert error.message == PUBLIC_INTERNAL_ERROR_MESSAGE


def test_structured_error_validation_never_echoes_invalid_secret_input() -> None:
    secret = "SERVICE_TOKEN=test-only-never-echo"

    with pytest.raises(ValidationError) as captured:
        StructuredError(
            code="INTERNAL_ERROR",
            message=secret + ("x" * 600),
            retryable=False,
            request_id="req-001",
        )

    assert secret not in str(captured.value)


def test_trace_is_a_bounded_observability_event_not_hidden_reasoning() -> None:
    event = TraceEvent(
        request_id="req-001",
        node="retrieve_manual",
        action="search_manual_knowledge",
        status="success",
        duration_ms=12.5,
        evidence_count=2,
        parameter_summary={"query_length": 12, "mode": "hybrid"},
    )

    payload = event.model_dump(mode="json")

    assert payload["evidence_count"] == 2
    assert "prompt" not in payload
    assert "reasoning" not in payload

    with pytest.raises(ValidationError):
        TraceEvent(
            request_id="req-001",
            node="retrieve_manual",
            action="search_manual_knowledge",
            status="failure",
            duration_ms=12.5,
        )

    with pytest.raises(ValidationError):
        TraceEvent(
            request_id="req-001",
            node="retrieve_manual",
            action="search_manual_knowledge",
            status="success",
            duration_ms=12.5,
            parameter_summary={"api_key": "never expose"},
        )


@pytest.mark.parametrize(
    "sensitive_key",
    [
        "x-api-key",
        "llm_api_key",
        "service_token",
        "langfuse_secret_key",
        "authorization_header",
        "password",
        "access_token",
    ],
)
def test_trace_rejects_normalized_sensitive_parameter_keys(sensitive_key: str) -> None:
    with pytest.raises(ValidationError, match="prohibited key"):
        TraceEvent(
            request_id="req-001",
            node="provider",
            action="call",
            status="success",
            duration_ms=1,
            parameter_summary={sensitive_key: "test-only-secret"},
        )


@pytest.mark.parametrize(
    "sensitive_value",
    [
        "Bearer test-only-secret",
        "sk-testonly",
        "https://workspace-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        r"C:\private\service.py",
        "/private/project/service.py",
        "first line\nprivate detail",
    ],
)
def test_trace_rejects_sensitive_or_internal_string_values(sensitive_value: str) -> None:
    with pytest.raises(ValidationError, match="prohibited value"):
        TraceEvent(
            request_id="req-001",
            node="provider",
            action="call",
            status="success",
            duration_ms=1,
            parameter_summary={"safe_summary": sensitive_value},
        )


def test_trace_bounds_parameter_names_and_string_values_but_allows_token_count() -> None:
    event = TraceEvent(
        request_id="req-001",
        node="provider",
        action="call",
        status="success",
        duration_ms=1,
        parameter_summary={"token_count": 128},
    )
    assert event.parameter_summary == {"token_count": 128}

    with pytest.raises(ValidationError, match="parameter name"):
        TraceEvent(
            request_id="req-001",
            node="provider",
            action="call",
            status="success",
            duration_ms=1,
            parameter_summary={"x" * 65: "safe"},
        )

    with pytest.raises(ValidationError, match="parameter string"):
        TraceEvent(
            request_id="req-001",
            node="provider",
            action="call",
            status="success",
            duration_ms=1,
            parameter_summary={"safe_summary": "x" * 257},
        )


@pytest.mark.parametrize(
    "invalid_hash",
    [
        "restricted answer text",
        "sha256:restricted-answer",
        "sha256:" + ("a" * 63),
        "sha256:" + ("g" * 64),
    ],
)
def test_risk_review_requires_a_real_sha256_answer_hash(invalid_hash: str) -> None:
    payload = {
        "review_id": "review-risk-001",
        "request_id": "req-001",
        "conversation_id": "conv-001",
        "risk_category": "operation_command",
        "restricted_answer_hash": invalid_hash,
        "idempotency_key": "risk:req-001",
    }

    with pytest.raises(ValidationError):
        RiskReview.model_validate(payload)

    payload["restricted_answer_hash"] = "sha256:" + hashlib.sha256(b"restricted answer").hexdigest()
    assert RiskReview.model_validate(payload).restricted_answer_hash.startswith("sha256:")


def test_diagnosis_separates_observation_evidence_inference_checks_and_unknowns() -> None:
    diagnosis = DiagnosisRecord(
        diagnosis_id="diag-001",
        request_id="req-001",
        conversation_id="conv-001",
        equipment="离心泵 P-001",
        observed_anomalies=["用户观察: 出口压力下降"],
        manual_evidence=[_manual_citation()],
        sensor_evidence=[_sensor_citation()],
        synthetic_case_evidence=[],
        candidate_causes=[
            CandidateCause(
                cause="入口过滤器堵塞",
                ranking_score=0.75,
                supporting_citation_ids=["manual:2196:p12:c3", "sensor:1200:PS1__mean"],
            )
        ],
        recommended_checks=["由具备资质的人员核对过滤器状态"],
        risk_level=RiskLevel.MEDIUM,
        approval_required=False,
        evidence_grade=EvidenceGrade.COMPLETE,
        limitations=["UCI 数据与手册设备不是同一真实资产"],
        unknowns=["未获得现场实时数据"],
    )

    assert diagnosis.disclaimer == INDUSTRIAL_SAFETY_DISCLAIMER
    assert diagnosis.candidate_causes[0].ranking_score == pytest.approx(0.75)

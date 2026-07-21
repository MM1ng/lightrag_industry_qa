from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from industrial_energy_agent.domain.enums import EvidenceGrade, ReviewStatus, RiskLevel
from industrial_energy_agent.domain.models import CandidateCause, DiagnosisRecord, WorkOrderDraft
from industrial_energy_agent.domain.safety_rules import work_order_review_fingerprint
from industrial_energy_agent.persistence.database import Database
from industrial_energy_agent.persistence.review_repository import ReviewRepository
from industrial_energy_agent.persistence.session_repository import SessionRepository
from industrial_energy_agent.persistence.work_order_repository import WorkOrderRepository
from industrial_energy_agent.tools.common import SafeStructuredTool
from industrial_energy_agent.tools.work_order_tools import (
    CreateWorkOrderDraftInput,
    CreateWorkOrderDraftResult,
    build_create_work_order_draft_tool,
)


def _diagnosis() -> DiagnosisRecord:
    return DiagnosisRecord(
        diagnosis_id="diag-tool-1",
        request_id="req-diagnosis",
        conversation_id="conv-tool-1",
        equipment="PUMP-001",
        observed_anomalies=["出口压力下降"],
        manual_evidence=[],
        sensor_evidence=[],
        synthetic_case_evidence=[],
        candidate_causes=[CandidateCause(cause="入口条件异常", ranking_score=0.7)],
        recommended_checks=["核对入口条件"],
        risk_level=RiskLevel.MEDIUM,
        approval_required=True,
        evidence_grade=EvidenceGrade.PARTIAL,
        limitations=[],
        unknowns=[],
    )


def _real_tool(
    tmp_path: Path,
) -> tuple[SafeStructuredTool, WorkOrderRepository, Database]:
    database = Database(tmp_path / "work-order-tool.sqlite")
    database.initialize()
    sessions = SessionRepository(database)
    sessions.ensure_session("conv-tool-1")
    sessions.save_diagnosis(_diagnosis())
    work_orders = WorkOrderRepository(database)
    tool = build_create_work_order_draft_tool(
        sessions,
        work_orders,
        conversation_id="conv-tool-1",
    )
    return tool, work_orders, database


@pytest.fixture
def repositories(tmp_path: Path) -> tuple[SessionRepository, WorkOrderRepository]:
    database = Database(tmp_path / "tools.sqlite")
    database.initialize()
    sessions = SessionRepository(database)
    sessions.ensure_session("conv-tool-1")
    sessions.save_diagnosis(_diagnosis())
    return sessions, WorkOrderRepository(database)


class FailingWorkOrderRepository:
    def create(self, draft: WorkOrderDraft) -> WorkOrderDraft:
        raise RuntimeError('Traceback File "D:\\private\\orders.py" SERVICE_TOKEN=never-show')


def test_work_order_tool_never_sets_execution_fields(
    repositories: tuple[SessionRepository, WorkOrderRepository],
) -> None:
    sessions, work_orders = repositories
    tool = build_create_work_order_draft_tool(
        sessions,
        work_orders,
        conversation_id="conv-tool-1",
    )

    result = tool.invoke({"diagnosis_id": "diag-tool-1", "request_id": "req-work-order-success"})

    parsed = CreateWorkOrderDraftResult.model_validate(result)
    assert parsed.root.ok is True
    assert result["work_order"]["status"] == "DRAFT"
    assert result["work_order"]["approval_status"] == "PENDING_REVIEW"
    assert result["work_order"]["executed"] is False
    assert work_orders.get(result["work_order"]["work_order_id"]) is not None
    assert tool.name == "create_work_order_draft"
    assert tool.args_schema is CreateWorkOrderDraftInput


class CrossConversationDiagnosisRepository:
    def get_diagnosis(
        self,
        diagnosis_id: str,
        *,
        conversation_id: str,
    ) -> DiagnosisRecord:
        return _diagnosis().model_copy(update={"conversation_id": "conv-attacker"})


class RecordingWorkOrderRepository:
    def __init__(self) -> None:
        self.created: list[WorkOrderDraft] = []

    def create(self, draft: WorkOrderDraft) -> WorkOrderDraft:
        self.created.append(draft)
        return draft


class CommitThenLoseResponseRepository:
    def __init__(self, inner: WorkOrderRepository) -> None:
        self.inner = inner
        self.persisted_ids: set[str] = set()
        self.fail_after_first_commit = True

    def create(self, draft: WorkOrderDraft) -> WorkOrderDraft:
        persisted = self.inner.create(draft)
        self.persisted_ids.add(persisted.work_order_id)
        if self.fail_after_first_commit:
            self.fail_after_first_commit = False
            raise RuntimeError("response lost after commit")
        return persisted


def test_work_order_tool_returns_not_found_for_unpersisted_diagnosis(
    repositories: tuple[SessionRepository, WorkOrderRepository],
) -> None:
    sessions, work_orders = repositories
    tool = build_create_work_order_draft_tool(
        sessions,
        work_orders,
        conversation_id="conv-tool-1",
    )

    result = tool.invoke({"diagnosis_id": "missing", "request_id": "req-wo-missing"})

    assert result["ok"] is False
    assert result["error"]["code"] == "DIAGNOSIS_NOT_FOUND"
    assert "work_order" not in result


def test_work_order_tool_returns_structured_invalid_input(
    repositories: tuple[SessionRepository, WorkOrderRepository],
) -> None:
    sessions, work_orders = repositories
    tool = build_create_work_order_draft_tool(
        sessions,
        work_orders,
        conversation_id="conv-tool-1",
    )

    result = tool.invoke({"diagnosis_id": "   ", "request_id": "req-wo-invalid"})

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"


def test_work_order_tool_returns_structured_error_for_unusable_diagnosis(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "invalid-diagnosis.sqlite")
    database.initialize()
    sessions = SessionRepository(database)
    sessions.ensure_session("conv-tool-1")
    diagnosis = _diagnosis().model_copy(update={"observed_anomalies": ["x" * 2_001]})
    sessions.save_diagnosis(diagnosis)
    tool = build_create_work_order_draft_tool(
        sessions,
        WorkOrderRepository(database),
        conversation_id="conv-tool-1",
    )

    result = tool.invoke({"diagnosis_id": "diag-tool-1", "request_id": "req-wo-unusable"})

    assert result["ok"] is False
    assert result["error"]["code"] == "DIAGNOSIS_INCOMPLETE"
    assert "work_order" not in result


def test_work_order_tool_does_not_fake_success_when_persistence_fails(
    repositories: tuple[SessionRepository, WorkOrderRepository],
) -> None:
    sessions, _ = repositories
    tool = build_create_work_order_draft_tool(
        sessions,
        FailingWorkOrderRepository(),
        conversation_id="conv-tool-1",
    )

    result = tool.invoke({"diagnosis_id": "diag-tool-1", "request_id": "req-wo-dependency"})
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is False
    assert result["error"]["code"] == "WORK_ORDER_DEPENDENCY_ERROR"
    assert "work_order" not in result
    assert "Traceback" not in rendered
    assert "never-show" not in rendered
    assert "orders.py" not in rendered


def test_work_order_tool_rejects_cross_conversation_repository_result() -> None:
    work_orders = RecordingWorkOrderRepository()
    tool = build_create_work_order_draft_tool(
        CrossConversationDiagnosisRepository(),
        work_orders,
        conversation_id="conv-tool-1",
    )

    result = tool.invoke({"diagnosis_id": "diag-tool-1", "request_id": "req-wo-cross-conversation"})

    assert result["ok"] is False
    assert result["error"]["code"] == "DIAGNOSIS_NOT_FOUND"
    assert work_orders.created == []


def test_work_order_retry_after_ambiguous_commit_reuses_one_persisted_draft(
    repositories: tuple[SessionRepository, WorkOrderRepository],
) -> None:
    sessions, persisted = repositories
    ambiguous = CommitThenLoseResponseRepository(persisted)
    tool = build_create_work_order_draft_tool(
        sessions,
        ambiguous,
        conversation_id="conv-tool-1",
    )
    payload = {"diagnosis_id": "diag-tool-1"}

    first = tool.invoke(payload)
    second = tool.invoke(payload)

    assert first["ok"] is False
    assert first["error"]["code"] == "WORK_ORDER_DEPENDENCY_ERROR"
    assert second["ok"] is True
    assert len(ambiguous.persisted_ids) == 1
    assert second["work_order"]["work_order_id"] in ambiguous.persisted_ids
    assert second["work_order"]["status"] == "DRAFT"
    assert second["work_order"]["approval_status"] == "PENDING_REVIEW"
    assert second["work_order"]["executed"] is False


def test_work_order_same_idempotency_identity_rejects_different_payload(
    repositories: tuple[SessionRepository, WorkOrderRepository],
) -> None:
    sessions, work_orders = repositories
    tool = build_create_work_order_draft_tool(
        sessions,
        work_orders,
        conversation_id="conv-tool-1",
    )
    base = {"diagnosis_id": "diag-tool-1"}

    first = tool.invoke({**base, "safety_items": ["确认隔离边界"]})
    conflict = tool.invoke({**base, "safety_items": ["不同的安全事项"]})

    assert first["ok"] is True
    assert conflict["ok"] is False
    assert conflict["error"]["code"] == "WORK_ORDER_CONFLICT"
    assert conflict["error"]["retryable"] is False
    assert work_orders.get(first["work_order"]["work_order_id"]) is not None


@pytest.mark.parametrize("review_status", [ReviewStatus.REVIEWED, ReviewStatus.REJECTED])
def test_work_order_retry_after_human_review_returns_existing_lifecycle_state(
    tmp_path: Path,
    review_status: ReviewStatus,
) -> None:
    database = Database(tmp_path / "reviewed-retry.sqlite")
    database.initialize()
    sessions = SessionRepository(database)
    sessions.ensure_session("conv-tool-1")
    sessions.save_diagnosis(_diagnosis())
    work_orders = WorkOrderRepository(database)
    tool = build_create_work_order_draft_tool(
        sessions,
        work_orders,
        conversation_id="conv-tool-1",
    )
    payload = {"diagnosis_id": "diag-tool-1"}

    first = tool.invoke(payload)
    reviews = ReviewRepository(database)
    persisted_draft = work_orders.get(first["work_order"]["work_order_id"])
    assert persisted_draft is not None
    pending = reviews.create_work_order_review(
        work_order_id=first["work_order"]["work_order_id"],
        request_id="req-review-existing-draft",
        idempotency_key="review-existing-draft",
        expected_fingerprint=work_order_review_fingerprint(persisted_draft),
    )
    reviews.decide_work_order_review(
        pending.review_id,
        status=review_status,
        decision="已完成离线人工复核",
        reviewer_id="reviewer-1",
    )

    retry = tool.invoke(payload)

    assert retry["ok"] is True
    assert retry["work_order"]["work_order_id"] == first["work_order"]["work_order_id"]
    assert retry["work_order"]["approval_status"] == review_status.value
    assert retry["work_order"]["executed"] is False


def test_work_order_default_request_id_is_business_idempotent(tmp_path: Path) -> None:
    tool, work_orders, database = _real_tool(tmp_path)

    first = tool.invoke({"diagnosis_id": "diag-tool-1"})
    retry = tool.invoke({"diagnosis_id": "diag-tool-1"})

    assert first["ok"] is True
    assert retry["ok"] is True
    assert retry["work_order"] == first["work_order"]
    assert work_orders.get(first["work_order"]["work_order_id"]) is not None
    with database.connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0]
    assert count == 1


def test_work_order_explicit_request_id_is_audit_only(tmp_path: Path) -> None:
    tool, _, database = _real_tool(tmp_path)

    first = tool.invoke({"diagnosis_id": "diag-tool-1", "request_id": "req-first-audit"})
    retry = tool.invoke({"diagnosis_id": "diag-tool-1", "request_id": "req-retry-audit"})

    assert first["ok"] is True
    assert retry["ok"] is True
    assert retry["work_order"]["work_order_id"] == first["work_order"]["work_order_id"]
    assert retry["work_order"]["request_id"] == "req-first-audit"
    with database.connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0]
    assert count == 1


def test_work_order_default_request_id_is_idempotent_across_eight_concurrent_calls(
    tmp_path: Path,
) -> None:
    tool, _, database = _real_tool(tmp_path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda _: tool.invoke({"diagnosis_id": "diag-tool-1"}),
                range(8),
            )
        )

    assert all(result["ok"] is True for result in results)
    assert len({result["work_order"]["work_order_id"] for result in results}) == 1
    assert all(result["work_order"]["status"] == "DRAFT" for result in results)
    assert all(result["work_order"]["executed"] is False for result in results)
    with database.connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0]
    assert count == 1

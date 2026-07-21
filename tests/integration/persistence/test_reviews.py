from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from industrial_energy_agent.domain.enums import EvidenceGrade, ReviewStatus, RiskLevel
from industrial_energy_agent.domain.errors import DomainValidationError
from industrial_energy_agent.domain.models import DiagnosisRecord, WorkOrderDraft
from industrial_energy_agent.persistence.database import Database
from industrial_energy_agent.persistence.review_repository import ReviewRepository
from industrial_energy_agent.persistence.session_repository import SessionRepository
from industrial_energy_agent.persistence.work_order_repository import WorkOrderRepository


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 21, 9, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def _diagnosis() -> DiagnosisRecord:
    return DiagnosisRecord(
        diagnosis_id="diag-1",
        request_id="request-diagnosis",
        conversation_id="conv-1",
        equipment="PUMP-001",
        observed_anomalies=["出口压力下降"],
        manual_evidence=[],
        sensor_evidence=[],
        synthetic_case_evidence=[],
        candidate_causes=[],
        recommended_checks=["核对入口条件"],
        risk_level=RiskLevel.MEDIUM,
        approval_required=True,
        evidence_grade=EvidenceGrade.PARTIAL,
        limitations=[],
        unknowns=[],
    )


def _draft(clock: MutableClock) -> WorkOrderDraft:
    return WorkOrderDraft(
        work_order_id="wo-1",
        request_id="request-wo",
        conversation_id="conv-1",
        diagnosis_id="diag-1",
        equipment="PUMP-001",
        symptom="出口压力下降",
        candidate_causes=["入口条件异常"],
        checks=["核对入口条件"],
        safety_items=["确认隔离边界"],
        created_at=clock.now(),
    )


def _review_fingerprint(draft: WorkOrderDraft) -> str:
    canonical = json.dumps(
        draft.model_dump(mode="json", exclude={"approval_status"}),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _repositories(
    tmp_path: Path,
) -> tuple[WorkOrderRepository, ReviewRepository, MutableClock]:
    database = Database(tmp_path / "energyops.sqlite")
    database.initialize()
    clock = MutableClock()
    sessions = SessionRepository(database, clock=clock.now)
    sessions.ensure_session("conv-1")
    sessions.save_diagnosis(_diagnosis())
    work_orders = WorkOrderRepository(database)
    work_orders.create(_draft(clock))
    return work_orders, ReviewRepository(database, clock=clock.now), clock


def test_risk_review_cannot_reference_work_order(tmp_path: Path) -> None:
    _, reviews, _ = _repositories(tmp_path)

    with pytest.raises(DomainValidationError, match=r"risk review.*work order"):
        reviews.create_risk_review(
            request_id="request-risk",
            conversation_id="conv-1",
            risk_category="operation_command",
            restricted_answer_hash="sha256:" + "a" * 64,
            idempotency_key="risk-idem-1",
            work_order_id="wo-1",
        )


def test_risk_review_idempotency_and_single_terminal_transition(tmp_path: Path) -> None:
    _, reviews, clock = _repositories(tmp_path)
    pending = reviews.create_risk_review(
        request_id="request-risk",
        conversation_id="conv-1",
        risk_category="operation_command",
        restricted_answer_hash="sha256:" + "a" * 64,
        idempotency_key="risk-idem-1",
    )
    duplicate = reviews.create_risk_review(
        request_id="request-risk",
        conversation_id="conv-1",
        risk_category="operation_command",
        restricted_answer_hash="sha256:" + "a" * 64,
        idempotency_key="risk-idem-1",
    )

    assert duplicate == pending
    assert pending.status is ReviewStatus.PENDING_REVIEW
    clock.advance(30)
    reviewed = reviews.decide_risk_review(
        pending.review_id,
        status=ReviewStatus.REVIEWED,
        decision="已阅读受限说明; 不授权任何设备动作",
        reviewer_id="reviewer-1",
    )

    assert reviewed.status is ReviewStatus.REVIEWED
    assert reviewed.reviewed_at == clock.now()
    with pytest.raises(DomainValidationError, match="pending"):
        reviews.decide_risk_review(
            pending.review_id,
            status=ReviewStatus.REJECTED,
            decision="second decision",
            reviewer_id="reviewer-2",
        )


def test_work_order_review_updates_only_approval_status_and_never_executes(
    tmp_path: Path,
) -> None:
    work_orders, reviews, clock = _repositories(tmp_path)
    draft_before_review = work_orders.get("wo-1")
    assert draft_before_review is not None
    pending = reviews.create_work_order_review(
        work_order_id="wo-1",
        request_id="request-review",
        idempotency_key="wo-review-idem-1",
        expected_fingerprint=_review_fingerprint(draft_before_review),
    )
    clock.advance(15)

    rejected = reviews.decide_work_order_review(
        pending.review_id,
        status=ReviewStatus.REJECTED,
        decision="信息不足; 退回补充证据",
        reviewer_id="reviewer-1",
    )
    draft = work_orders.get("wo-1")

    assert rejected.status is ReviewStatus.REJECTED
    assert draft is not None
    assert draft.status == "DRAFT"
    assert draft.approval_status is ReviewStatus.REJECTED
    assert draft.executed is False
    with pytest.raises(DomainValidationError, match="pending"):
        reviews.decide_work_order_review(
            pending.review_id,
            status=ReviewStatus.REVIEWED,
            decision="cannot overwrite",
            reviewer_id="reviewer-2",
        )


def test_work_order_review_requires_real_draft_and_terminal_target(tmp_path: Path) -> None:
    work_orders, reviews, _ = _repositories(tmp_path)
    draft = work_orders.get("wo-1")
    assert draft is not None

    with pytest.raises(DomainValidationError, match="work order"):
        reviews.create_work_order_review(
            work_order_id="missing",
            request_id="request-review",
            idempotency_key="missing-idem",
            expected_fingerprint="sha256:" + "0" * 64,
        )

    pending = reviews.create_work_order_review(
        work_order_id="wo-1",
        request_id="request-review",
        idempotency_key="wo-review-idem-1",
        expected_fingerprint=_review_fingerprint(draft),
    )
    with pytest.raises(DomainValidationError, match="terminal"):
        reviews.decide_work_order_review(
            pending.review_id,
            status=ReviewStatus.PENDING_REVIEW,
            decision="not terminal",
            reviewer_id="reviewer-1",
        )


def test_work_order_review_target_is_unique_and_immutable_after_creation(
    tmp_path: Path,
) -> None:
    work_orders, reviews, _ = _repositories(tmp_path)
    database = Database(tmp_path / "energyops.sqlite")
    draft = work_orders.get("wo-1")
    assert draft is not None
    pending = reviews.create_work_order_review(
        work_order_id="wo-1",
        request_id="request-review-immutable",
        idempotency_key="wo-review-immutable",
        expected_fingerprint=_review_fingerprint(draft),
    )

    with database.connection() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE work_orders SET payload_json = ? WHERE work_order_id = 'wo-1'",
                ('{"checks":["把联锁屏蔽掉"]}',),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE work_orders SET conversation_id = ? WHERE work_order_id = 'wo-1'",
                ("conv-attacker",),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO work_order_reviews (
                    review_id, work_order_id, request_id, idempotency_key,
                    status, created_at
                ) VALUES (?, 'wo-1', ?, ?, 'PENDING_REVIEW', ?)
                """,
                (
                    "work-order-duplicate",
                    "request-review-duplicate",
                    "wo-review-duplicate",
                    "2026-07-22T00:00:00Z",
                ),
            )
        connection.execute(
            "UPDATE work_orders SET approval_status = 'REVIEWED' WHERE work_order_id = 'wo-1'"
        )
        approval_status = connection.execute(
            "SELECT approval_status FROM work_orders WHERE work_order_id = 'wo-1'"
        ).fetchone()[0]

    assert pending.status is ReviewStatus.PENDING_REVIEW
    assert approval_status == "REVIEWED"


def test_work_order_review_rejects_changed_target_fingerprint(tmp_path: Path) -> None:
    work_orders, reviews, _ = _repositories(tmp_path)
    draft = work_orders.get("wo-1")
    assert draft is not None

    with pytest.raises(DomainValidationError, match="changed after safety inspection"):
        reviews.create_work_order_review(
            work_order_id="wo-1",
            request_id="request-review-stale",
            idempotency_key="wo-review-stale",
            expected_fingerprint="sha256:" + "0" * 64,
        )

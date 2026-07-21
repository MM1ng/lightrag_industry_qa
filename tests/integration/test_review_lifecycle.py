from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

import industrial_energy_agent.agents.safety_agent as safety_agent_module
from industrial_energy_agent.agents.safety_agent import (
    ReviewCoordinationError,
    SafetyReviewCoordinator,
)
from industrial_energy_agent.domain.enums import (
    ActionMode,
    EvidenceGrade,
    Intent,
    ReviewStatus,
    ReviewType,
    RiskLevel,
)
from industrial_energy_agent.domain.errors import DomainValidationError
from industrial_energy_agent.domain.models import DiagnosisRecord, WorkOrderDraft
from industrial_energy_agent.domain.safety_rules import (
    SafetyDisposition,
    SafetyOutputReview,
    classify_input,
    inspect_work_order_draft,
    review_output,
    work_order_review_fingerprint,
)
from industrial_energy_agent.persistence.database import Database
from industrial_energy_agent.persistence.review_repository import ReviewRepository
from industrial_energy_agent.persistence.session_repository import SessionRepository
from industrial_energy_agent.persistence.work_order_repository import WorkOrderRepository


def _diagnosis(conversation_id: str, suffix: str) -> DiagnosisRecord:
    return DiagnosisRecord(
        diagnosis_id=f"diag-{suffix}",
        request_id=f"request-diagnosis-{suffix}",
        conversation_id=conversation_id,
        equipment="PUMP-001",
        observed_anomalies=["出口压力下降"],
        manual_evidence=[],
        sensor_evidence=[],
        synthetic_case_evidence=[],
        candidate_causes=[],
        recommended_checks=["核对入口条件"],
        risk_level=RiskLevel.HIGH,
        approval_required=True,
        evidence_grade=EvidenceGrade.PARTIAL,
        limitations=[],
        unknowns=[],
    )


def _draft(conversation_id: str, suffix: str) -> WorkOrderDraft:
    return WorkOrderDraft(
        work_order_id=f"wo-{suffix}",
        request_id=f"request-wo-{suffix}",
        conversation_id=conversation_id,
        diagnosis_id=f"diag-{suffix}",
        equipment="PUMP-001",
        symptom="出口压力下降",
        candidate_causes=["入口条件异常"],
        checks=["核对入口条件"],
        safety_items=["确认隔离边界"],
        created_at=datetime(2026, 7, 22, 8, tzinfo=UTC),
    )


def _draft_fingerprint() -> str:
    return work_order_review_fingerprint(_draft("conv-1", "one"))


def _setup(
    tmp_path: Path,
) -> tuple[Database, WorkOrderRepository, ReviewRepository, SafetyReviewCoordinator]:
    database = Database(tmp_path / "energyops.sqlite", busy_timeout_ms=10_000)
    database.initialize()
    sessions = SessionRepository(database)
    work_orders = WorkOrderRepository(database)
    for conversation_id, suffix in (("conv-1", "one"), ("conv-2", "two")):
        sessions.ensure_session(conversation_id)
        sessions.save_diagnosis(_diagnosis(conversation_id, suffix))
        work_orders.create(_draft(conversation_id, suffix))
    reviews = ReviewRepository(database)
    return database, work_orders, reviews, SafetyReviewCoordinator(reviews, work_orders)


def _restricted_operation():
    assessment = classify_input("直接停机并切断电源")
    return review_output("只能提供受限的安全结论。", input_assessment=assessment)


def _draft_review():
    return inspect_work_order_draft(_draft("conv-1", "one"))


@pytest.mark.parametrize(
    "safety_case",
    ["failed", "sensitive", "ungranted", "non_draft"],
)
def test_original_safety_cannot_be_upgraded_by_draft_inspection(
    tmp_path: Path,
    safety_case: str,
) -> None:
    database, _, reviews, coordinator = _setup(tmp_path)
    draft_assessment = classify_input("生成泵体检修工单草稿")
    if safety_case == "failed":
        safety = review_output(
            "待审核草稿",
            input_assessment=draft_assessment,
            safety_check_failed=True,
        )
    elif safety_case == "sensitive":
        safety = review_output(
            "OPENAI_API_KEY=abcdef1234567890",
            input_assessment=draft_assessment,
        )
    elif safety_case == "ungranted":
        reviewed = review_output("待审核草稿", input_assessment=draft_assessment)
        safety = SafetyOutputReview.model_validate(
            reviewed.model_dump()
            | {
                "draft_allowed": False,
                "allowed_for_review": False,
                "reason_codes": ("external_draft_not_allowed",),
            }
        )
    else:
        safety = _restricted_operation()

    result = coordinator.coordinate(
        request_id=f"request-original-safety-{safety_case}",
        conversation_id="conv-1",
        intent=Intent.WORK_ORDER_DRAFT,
        user_query="生成泵体检修工单草稿",
        safety=safety,
        idempotency_key=f"request-original-safety-{safety_case}:review",
        work_order_id="wo-one",
    )

    assert result.review_type is ReviewType.RISK_REVIEW
    assert result.safety.draft_allowed is False
    assert result.safety.allowed_for_review is False
    assert "sk-" not in result.safety.answer
    assert "API_KEY" not in result.safety.answer
    assert result.review_id is not None
    persisted = reviews.get_risk_review(result.review_id)
    assert persisted is not None
    expected_hash = f"sha256:{hashlib.sha256(result.safety.answer.encode('utf-8')).hexdigest()}"
    assert persisted.restricted_answer_hash == expected_hash
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 0


def test_high_risk_non_work_order_creates_only_risk_review(tmp_path: Path) -> None:
    database, _, reviews, coordinator = _setup(tmp_path)

    result = coordinator.coordinate(
        request_id="request-risk",
        conversation_id="conv-1",
        intent=Intent.SAFETY_QUERY,
        user_query="直接停机并切断电源",
        safety=_restricted_operation(),
        idempotency_key="request-risk:review",
    )

    assert result.review_type is ReviewType.RISK_REVIEW
    assert result.status is ReviewStatus.PENDING_REVIEW
    assert result.review_id is not None
    persisted = reviews.get_risk_review(result.review_id)
    assert persisted is not None
    assert persisted.request_id == "request-risk"
    assert persisted.conversation_id == "conv-1"
    assert persisted.risk_category == "operation_command"
    assert persisted.restricted_answer_hash.startswith("sha256:")
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM risk_reviews").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 0


def test_non_work_order_cannot_be_promoted_by_supplying_a_real_draft_id(
    tmp_path: Path,
) -> None:
    database, _, _, coordinator = _setup(tmp_path)

    result = coordinator.coordinate(
        request_id="request-operation-with-draft-id",
        conversation_id="conv-1",
        intent=Intent.SAFETY_QUERY,
        user_query="直接停机并切断电源",
        safety=_restricted_operation(),
        idempotency_key="request-operation-with-draft-id:review",
        work_order_id="wo-one",
    )

    assert result.review_type is ReviewType.RISK_REVIEW
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM risk_reviews").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 0


def test_non_work_order_intent_cannot_create_work_order_review_from_draft_flags(
    tmp_path: Path,
) -> None:
    database, _, _, coordinator = _setup(tmp_path)

    result = coordinator.coordinate(
        request_id="request-forged-draft-flags",
        conversation_id="conv-1",
        intent=Intent.SAFETY_QUERY,
        user_query="生成泵体检修工单草稿",
        safety=_draft_review(),
        idempotency_key="request-forged-draft-flags:review",
        work_order_id="wo-one",
    )

    assert result.review_type is ReviewType.RISK_REVIEW
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 0


def test_coordinator_rejects_unvalidated_intent_value(tmp_path: Path) -> None:
    database, _, _, coordinator = _setup(tmp_path)

    with pytest.raises(ReviewCoordinationError, match="valid intent"):
        coordinator.coordinate(
            request_id="request-invalid-intent",
            conversation_id="conv-1",
            intent="work_order_draft",  # type: ignore[arg-type]
            user_query="生成泵体检修工单草稿",
            safety=_draft_review(),
            idempotency_key="request-invalid-intent:review",
            work_order_id="wo-one",
        )

    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM risk_reviews").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 0


def test_informational_answer_creates_no_review(tmp_path: Path) -> None:
    database, _, _, coordinator = _setup(tmp_path)
    assessment = classify_input("为什么检修前要断电\N{FULLWIDTH QUESTION MARK}")
    safety = review_output("断电用于控制危险能量。[手册第 2 页]", input_assessment=assessment)

    result = coordinator.coordinate(
        request_id="request-info",
        conversation_id="conv-1",
        intent=Intent.EQUIPMENT_QA,
        user_query="为什么检修前要断电\N{FULLWIDTH QUESTION MARK}",
        safety=safety,
        idempotency_key="request-info:review",
    )

    assert result.review_type is None
    assert result.review_id is None
    assert result.status is None
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM risk_reviews").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 0


def test_real_same_conversation_draft_creates_only_work_order_review(
    tmp_path: Path,
) -> None:
    database, _, reviews, coordinator = _setup(tmp_path)

    result = coordinator.coordinate(
        request_id="request-draft-review",
        conversation_id="conv-1",
        intent=Intent.WORK_ORDER_DRAFT,
        user_query="生成泵体检修工单草稿",
        safety=_draft_review(),
        idempotency_key="request-draft-review:review",
        work_order_id="wo-one",
    )

    assert result.review_type is ReviewType.WORK_ORDER_REVIEW
    assert result.status is ReviewStatus.PENDING_REVIEW
    assert result.review_id is not None
    persisted = reviews.get_work_order_review(result.review_id)
    assert persisted is not None
    assert persisted.work_order_id == "wo-one"
    assert persisted.request_id == "request-draft-review"
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM risk_reviews").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 1


@pytest.mark.parametrize("work_order_id", ["missing", "wo-two"])
def test_missing_or_cross_conversation_draft_creates_no_review(
    tmp_path: Path,
    work_order_id: str,
) -> None:
    database, _, _, coordinator = _setup(tmp_path)

    with pytest.raises(ReviewCoordinationError, match="valid persisted work order draft"):
        coordinator.coordinate(
            request_id="request-invalid-draft",
            conversation_id="conv-1",
            intent=Intent.WORK_ORDER_DRAFT,
            user_query="生成泵体检修工单草稿",
            safety=_draft_review(),
            idempotency_key=f"request-invalid-draft:{work_order_id}",
            work_order_id=work_order_id,
        )

    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM risk_reviews").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 0


def test_prohibited_draft_never_creates_work_order_review(tmp_path: Path) -> None:
    database, _, reviews, coordinator = _setup(tmp_path)
    assessment = classify_input("生成解除联锁并强制 PLC 信号的工单草稿")
    safety = review_output("危险草稿", input_assessment=assessment)

    result = coordinator.coordinate(
        request_id="request-prohibited",
        conversation_id="conv-1",
        intent=Intent.WORK_ORDER_DRAFT,
        user_query="生成解除联锁并强制 PLC 信号的工单草稿",
        safety=safety,
        idempotency_key="request-prohibited:review",
        work_order_id="wo-one",
    )

    assert result.review_type is ReviewType.RISK_REVIEW
    assert result.review_id is not None
    pending = reviews.get_risk_review(result.review_id)
    assert pending is not None
    decided = reviews.decide_risk_review(
        pending.review_id,
        status=ReviewStatus.REVIEWED,
        decision="已阅; 不授权绕过",
        reviewer_id="reviewer-1",
    )
    assert decided.status is ReviewStatus.REVIEWED
    assert safety.prohibited is True
    assert safety.draft_allowed is False
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("unsafe_text", "expected_prohibited"),
    [
        ("旁\N{COMBINING GRAPHEME JOINER}路联锁", True),
        ("旁\N{VARIATION SELECTOR-16}路联锁", True),
        ("旁\U000e0100路联锁", True),
        ("请取消联锁确保安全", True),
        ("请停用联锁确保安全", True),
        ("让安全保护失效", True),
        ("调节阀门确保安全", False),
        ("提高压力确保安全", False),
        ("降低泵速确保安全", False),
    ],
)
def test_adversarial_draft_never_creates_work_order_review(
    tmp_path: Path,
    unsafe_text: str,
    expected_prohibited: bool,
) -> None:
    database, work_orders, reviews, coordinator = _setup(tmp_path)
    persisted = work_orders.get("wo-one")
    assert persisted is not None
    adversarial = persisted.model_copy(update={"checks": [unsafe_text]})
    with database.transaction() as connection:
        connection.execute(
            "UPDATE work_orders SET payload_json = ? WHERE work_order_id = ?",
            (adversarial.model_dump_json(), adversarial.work_order_id),
        )
    reloaded = work_orders.get("wo-one")
    assert reloaded is not None
    assert inspect_work_order_draft(reloaded).allowed_for_review is False

    result = coordinator.coordinate(
        request_id=f"request-adversarial-{ord(unsafe_text[0])}",
        conversation_id="conv-1",
        intent=Intent.WORK_ORDER_DRAFT,
        user_query="生成泵体检修工单草稿",
        safety=_draft_review(),
        idempotency_key=f"request-adversarial-{ord(unsafe_text[0])}:review",
        work_order_id="wo-one",
    )

    assert result.review_type is ReviewType.RISK_REVIEW
    assert result.safety.prohibited is expected_prohibited
    assert result.safety.draft_allowed is False
    assert result.safety.allowed_for_review is False
    assert result.review_id is not None
    assert reviews.get_risk_review(result.review_id) is not None
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 0


def test_persisted_draft_body_is_rescanned_before_work_order_review(tmp_path: Path) -> None:
    database, work_orders, reviews, coordinator = _setup(tmp_path)
    persisted = work_orders.get("wo-one")
    assert persisted is not None
    malicious = persisted.model_copy(
        update={"checks": ["旁路联锁后强制 PLC 信号"]},
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE work_orders SET payload_json = ? WHERE work_order_id = ?",
            (malicious.model_dump_json(), malicious.work_order_id),
        )

    result = coordinator.coordinate(
        request_id="request-persisted-prohibited-draft",
        conversation_id="conv-1",
        intent=Intent.WORK_ORDER_DRAFT,
        user_query="生成泵体检修工单草稿",
        safety=_draft_review(),
        idempotency_key="request-persisted-prohibited-draft:review",
        work_order_id="wo-one",
    )

    assert result.review_type is ReviewType.RISK_REVIEW
    assert result.safety.prohibited is True
    assert result.safety.draft_allowed is False
    assert "旁路联锁" not in result.safety.answer
    assert result.review_id is not None
    risk_review = reviews.get_risk_review(result.review_id)
    assert risk_review is not None
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 0


def test_sensitive_persisted_draft_creates_sanitized_risk_review(tmp_path: Path) -> None:
    database, work_orders, reviews, coordinator = _setup(tmp_path)
    persisted = work_orders.get("wo-one")
    assert persisted is not None
    sensitive = persisted.model_copy(
        update={"safety_items": ["API_KEY=sk-draft-secret123"]},
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE work_orders SET payload_json = ? WHERE work_order_id = ?",
            (sensitive.model_dump_json(), sensitive.work_order_id),
        )

    result = coordinator.coordinate(
        request_id="request-sensitive-persisted-draft",
        conversation_id="conv-1",
        intent=Intent.WORK_ORDER_DRAFT,
        user_query="生成泵体检修工单草稿",
        safety=_draft_review(),
        idempotency_key="request-sensitive-persisted-draft:review",
        work_order_id="wo-one",
    )

    assert result.review_type is ReviewType.RISK_REVIEW
    assert "sk-draft" not in result.safety.answer
    assert result.safety.allowed_for_review is False
    assert result.review_id is not None
    assert reviews.get_risk_review(result.review_id) is not None
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 0


def test_draft_inspection_exception_fails_closed_to_risk_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, work_orders, reviews, _ = _setup(tmp_path)

    def failing_inspector(_: WorkOrderDraft):
        raise RuntimeError("safety inspection failed")

    monkeypatch.setattr(safety_agent_module, "inspect_work_order_draft", failing_inspector)
    coordinator = SafetyReviewCoordinator(reviews, work_orders)
    original_safety = inspect_work_order_draft(_draft("conv-1", "one"))

    result = coordinator.coordinate(
        request_id="request-failed-draft-inspection",
        conversation_id="conv-1",
        intent=Intent.WORK_ORDER_DRAFT,
        user_query="生成泵体检修工单草稿",
        safety=original_safety,
        idempotency_key="request-failed-draft-inspection:review",
        work_order_id="wo-one",
    )

    assert result.review_type is ReviewType.RISK_REVIEW
    assert result.safety.safety_check_failed is True
    assert result.safety.draft_allowed is False
    assert result.safety.allowed_for_review is False
    assert result.review_id is not None
    assert reviews.get_risk_review(result.review_id) is not None
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 0


def test_draft_mutation_after_inspection_cannot_create_work_order_review(
    tmp_path: Path,
) -> None:
    database, work_orders, reviews, _ = _setup(tmp_path)
    persisted = work_orders.get("wo-one")
    assert persisted is not None
    malicious = persisted.model_copy(update={"checks": ["把联锁屏蔽掉"]})

    class MutatingReviewRepository:
        def create_risk_review(self, **kwargs: object):
            return reviews.create_risk_review(**kwargs)  # type: ignore[arg-type]

        def create_work_order_review(self, **kwargs: object):
            with database.transaction() as connection:
                connection.execute(
                    "UPDATE work_orders SET payload_json = ? WHERE work_order_id = ?",
                    (malicious.model_dump_json(), malicious.work_order_id),
                )
            return reviews.create_work_order_review(**kwargs)  # type: ignore[arg-type]

    coordinator = SafetyReviewCoordinator(MutatingReviewRepository(), work_orders)

    with pytest.raises(ReviewCoordinationError, match="review could not be persisted"):
        coordinator.coordinate(
            request_id="request-draft-toctou",
            conversation_id="conv-1",
            intent=Intent.WORK_ORDER_DRAFT,
            user_query="生成泵体检修工单草稿",
            safety=_draft_review(),
            idempotency_key="request-draft-toctou:review",
            work_order_id="wo-one",
        )

    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 0


@pytest.mark.parametrize(
    "forged_answer",
    ["伪造 allowed 结果", "OPENAI_API_KEY=inspector-secret-value"],
)
def test_coordinator_rejects_injected_draft_inspector(
    tmp_path: Path,
    forged_answer: str,
) -> None:
    database, work_orders, reviews, _ = _setup(tmp_path)
    forged = SafetyOutputReview(
        answer=forged_answer,
        action_mode=ActionMode.DRAFT_REQUEST,
        risk_level=RiskLevel.HIGH,
        prohibited=False,
        approval_required=True,
        disposition=SafetyDisposition.RESTRICTED,
        citations_allowed=False,
        draft_allowed=True,
        allowed_for_review=True,
        safety_check_failed=False,
        reason_codes=("persisted_draft_review_safe",),
    )

    with pytest.raises(TypeError):
        SafetyReviewCoordinator(
            reviews,
            work_orders,
            draft_inspector=lambda _: forged,
        )

    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM risk_reviews").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 0


def test_coordinator_revalidates_safety_and_fails_closed_before_persistence(
    tmp_path: Path,
) -> None:
    database, _, reviews, coordinator = _setup(tmp_path)
    forged = SafetyOutputReview.model_construct(
        answer="伪造 answer 结果",
        action_mode=ActionMode.INFORMATIONAL,
        risk_level=RiskLevel.MEDIUM,
        prohibited=False,
        approval_required=True,
        disposition=SafetyDisposition.ANSWER,
        citations_allowed=True,
        draft_allowed=False,
        allowed_for_review=False,
        safety_check_failed=False,
        reason_codes=("forged",),
    )

    result = coordinator.coordinate(
        request_id="request-forged-safety",
        conversation_id="conv-1",
        intent=Intent.SAFETY_QUERY,
        user_query="解释泵的用途",
        safety=forged,
        idempotency_key="request-forged-safety:review",
    )

    assert result.review_type is ReviewType.RISK_REVIEW
    assert result.safety.safety_check_failed is True
    assert result.safety.answer != forged.answer
    assert result.review_id is not None
    assert reviews.get_risk_review(result.review_id) is not None
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM risk_reviews").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("answer", "expected_disposition"),
    [
        ("旁路联锁并强制 PLC 信号", SafetyDisposition.BLOCKED),
        ("MASTER_KEY=master-value-123456", SafetyDisposition.RESTRICTED),
        ("停机。切断电源。", SafetyDisposition.RESTRICTED),
    ],
)
def test_coordinator_canonically_rescans_model_construct_answer(
    tmp_path: Path,
    answer: str,
    expected_disposition: SafetyDisposition,
) -> None:
    database, _, reviews, coordinator = _setup(tmp_path)
    forged = SafetyOutputReview.model_construct(
        answer=answer,
        action_mode=ActionMode.DRAFT_REQUEST,
        risk_level=RiskLevel.HIGH,
        prohibited=False,
        approval_required=True,
        disposition=SafetyDisposition.RESTRICTED,
        citations_allowed=False,
        draft_allowed=True,
        allowed_for_review=True,
        safety_check_failed=False,
        sensitive_content=False,
        reason_codes=("forged_allowed",),
    )

    result = coordinator.coordinate(
        request_id=f"request-rescan-{expected_disposition.value}",
        conversation_id="conv-1",
        intent=Intent.WORK_ORDER_DRAFT,
        user_query="生成泵体检修工单草稿",
        safety=forged,
        idempotency_key=f"request-rescan-{expected_disposition.value}:review",
        work_order_id="wo-one",
    )

    assert result.review_type is ReviewType.RISK_REVIEW
    assert result.safety.disposition is expected_disposition
    assert result.safety.answer != answer
    assert result.review_id is not None
    persisted = reviews.get_risk_review(result.review_id)
    assert persisted is not None
    assert persisted.restricted_answer_hash == (
        f"sha256:{hashlib.sha256(result.safety.answer.encode('utf-8')).hexdigest()}"
    )
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 0


@pytest.mark.parametrize(
    "user_query",
    [
        "为什么检修前要断电\N{FULLWIDTH QUESTION MARK}",
        "直接停机并切断电源",
        "看看这个",
        "",
    ],
)
def test_forged_allowed_flags_cannot_override_canonical_input_classification(
    tmp_path: Path,
    user_query: str,
) -> None:
    database, _, _, coordinator = _setup(tmp_path)
    forged = SafetyOutputReview.model_construct(
        answer="待审核草稿",
        action_mode=ActionMode.DRAFT_REQUEST,
        risk_level=RiskLevel.HIGH,
        prohibited=False,
        approval_required=True,
        disposition=SafetyDisposition.RESTRICTED,
        citations_allowed=False,
        draft_allowed=True,
        allowed_for_review=True,
        safety_check_failed=False,
        sensitive_content=False,
        reason_codes=("forged_allowed",),
    )

    result = coordinator.coordinate(
        request_id=f"request-forged-query-{len(user_query)}",
        conversation_id="conv-1",
        intent=Intent.WORK_ORDER_DRAFT,
        user_query=user_query,
        safety=forged,
        idempotency_key=f"request-forged-query-{len(user_query)}:review",
        work_order_id="wo-one",
    )

    assert result.review_type is not ReviewType.WORK_ORDER_REVIEW
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 0


@pytest.mark.parametrize(
    "case",
    [
        "citations",
        "draft_flags",
        "risk_and_restriction",
        "prohibited",
        "sensitive",
        "failed",
    ],
)
def test_external_safety_fields_are_merged_with_only_tighten_semantics(
    tmp_path: Path,
    case: str,
) -> None:
    database, _, _, coordinator = _setup(tmp_path)
    informational = classify_input("解释液压泵的用途")
    draft = classify_input("生成泵体检修工单草稿")
    user_query = "解释液压泵的用途"
    intent = Intent.EQUIPMENT_QA
    work_order_id = None

    if case == "citations":
        reviewed = review_output("液压泵用于传递液压能。", input_assessment=informational)
        external = SafetyOutputReview.model_validate(
            reviewed.model_dump() | {"citations_allowed": False}
        )
    elif case == "draft_flags":
        reviewed = review_output("待审核草稿", input_assessment=draft)
        external = SafetyOutputReview.model_validate(
            reviewed.model_dump() | {"draft_allowed": False, "allowed_for_review": False}
        )
        user_query = "生成泵体检修工单草稿"
        intent = Intent.WORK_ORDER_DRAFT
        work_order_id = "wo-one"
    elif case == "risk_and_restriction":
        external = _restricted_operation()
    elif case == "prohibited":
        external = SafetyOutputReview(
            answer="attacker controlled answer",
            action_mode=ActionMode.PROHIBITED_BYPASS,
            risk_level=RiskLevel.CRITICAL,
            prohibited=True,
            approval_required=True,
            disposition=SafetyDisposition.BLOCKED,
            citations_allowed=False,
            draft_allowed=False,
        )
    elif case == "sensitive":
        external = SafetyOutputReview(
            answer="attacker controlled answer",
            action_mode=ActionMode.INFORMATIONAL,
            risk_level=RiskLevel.HIGH,
            prohibited=False,
            approval_required=True,
            disposition=SafetyDisposition.RESTRICTED,
            citations_allowed=False,
            draft_allowed=False,
            sensitive_content=True,
        )
    else:
        external = SafetyOutputReview(
            answer="attacker controlled answer",
            action_mode=ActionMode.INFORMATIONAL,
            risk_level=RiskLevel.HIGH,
            prohibited=False,
            approval_required=True,
            disposition=SafetyDisposition.RESTRICTED,
            citations_allowed=False,
            draft_allowed=False,
            safety_check_failed=True,
        )

    result = coordinator.coordinate(
        request_id=f"request-only-tighten-{case}",
        conversation_id="conv-1",
        intent=intent,
        user_query=user_query,
        safety=external,
        idempotency_key=f"request-only-tighten-{case}:review",
        work_order_id=work_order_id,
    )

    risk_rank = {
        RiskLevel.LOW: 0,
        RiskLevel.MEDIUM: 1,
        RiskLevel.HIGH: 2,
        RiskLevel.CRITICAL: 3,
    }
    assert risk_rank[result.safety.risk_level] >= risk_rank[external.risk_level]
    assert result.safety.approval_required or not external.approval_required
    assert result.safety.prohibited is external.prohibited
    assert result.safety.sensitive_content is external.sensitive_content
    assert result.safety.safety_check_failed is external.safety_check_failed
    assert result.safety.citations_allowed is (
        reviewed.citations_allowed and external.citations_allowed
        if case in {"citations", "draft_flags"}
        else False
    )
    assert result.safety.draft_allowed is (
        case == "draft_flags" and reviewed.draft_allowed and external.draft_allowed
    )
    assert result.safety.allowed_for_review is (
        case == "draft_flags" and reviewed.allowed_for_review and external.allowed_for_review
    )
    if external.disposition is not SafetyDisposition.ANSWER:
        assert result.safety.disposition is not SafetyDisposition.ANSWER
    if case == "risk_and_restriction":
        assert result.safety.action_mode is ActionMode.OPERATION_COMMAND
    if case in {"prohibited", "sensitive", "failed"}:
        assert result.safety.answer != external.answer
    if case == "draft_flags":
        assert result.review_type is ReviewType.RISK_REVIEW
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 0


def test_coordinator_is_idempotent_and_hashes_exact_restricted_answer(tmp_path: Path) -> None:
    database, _, reviews, coordinator = _setup(tmp_path)
    kwargs = {
        "request_id": "request-idempotent",
        "conversation_id": "conv-1",
        "intent": Intent.SAFETY_QUERY,
        "user_query": "直接停机并切断电源",
        "safety": _restricted_operation(),
        "idempotency_key": "request-idempotent:review",
    }

    first = coordinator.coordinate(**kwargs)
    retry = coordinator.coordinate(**kwargs)

    assert retry == first
    assert first.review_id is not None
    persisted = reviews.get_risk_review(first.review_id)
    assert persisted is not None
    assert persisted.restricted_answer_hash == (
        "sha256:5f2750be84665739ce77b27b2591a59e856cf5b9655b2423ae98caac1feed270"
    )
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM risk_reviews").fetchone()[0] == 1


def test_risk_review_hash_is_bound_to_validated_safety_answer(tmp_path: Path) -> None:
    _, _, reviews, coordinator = _setup(tmp_path)
    safety = _restricted_operation()

    result = coordinator.coordinate(
        request_id="request-bound-answer-hash",
        conversation_id="conv-1",
        intent=Intent.SAFETY_QUERY,
        user_query="直接停机并切断电源",
        safety=safety,
        idempotency_key="request-bound-answer-hash:review",
    )

    assert result.review_id is not None
    persisted = reviews.get_risk_review(result.review_id)
    assert persisted is not None
    expected = f"sha256:{hashlib.sha256(result.safety.answer.encode('utf-8')).hexdigest()}"
    assert persisted.restricted_answer_hash == expected


def test_coordinator_does_not_accept_independent_restricted_answer(tmp_path: Path) -> None:
    _, _, _, coordinator = _setup(tmp_path)

    with pytest.raises(TypeError):
        coordinator.coordinate(
            request_id="request-injected-answer",
            conversation_id="conv-1",
            intent=Intent.SAFETY_QUERY,
            user_query="直接停机并切断电源",
            safety=_restricted_operation(),
            restricted_answer="API_KEY=sk-attacker-controlled",
            idempotency_key="request-injected-answer:review",
        )


def test_concurrent_risk_review_retries_return_one_record(tmp_path: Path) -> None:
    database, _, _, coordinator = _setup(tmp_path)

    def create(_: int):
        return coordinator.coordinate(
            request_id="request-concurrent",
            conversation_id="conv-1",
            intent=Intent.SAFETY_QUERY,
            user_query="直接停机并切断电源",
            safety=_restricted_operation(),
            idempotency_key="request-concurrent:review",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(create, range(8)))

    assert len({result.review_id for result in results}) == 1
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM risk_reviews").fetchone()[0] == 1


@pytest.mark.parametrize("first_type", [ReviewType.RISK_REVIEW, ReviewType.WORK_ORDER_REVIEW])
def test_idempotency_key_is_mutually_exclusive_across_review_types(
    tmp_path: Path,
    first_type: ReviewType,
) -> None:
    database, _, reviews, _ = _setup(tmp_path)
    key = "global-review-key"
    if first_type is ReviewType.RISK_REVIEW:
        reviews.create_risk_review(
            request_id="request-global-key",
            conversation_id="conv-1",
            risk_category="operation_command",
            restricted_answer_hash="sha256:" + "a" * 64,
            idempotency_key=key,
        )
        with pytest.raises(DomainValidationError, match="review types"):
            reviews.create_work_order_review(
                work_order_id="wo-one",
                request_id="request-global-key",
                idempotency_key=key,
                expected_fingerprint=_draft_fingerprint(),
            )
    else:
        reviews.create_work_order_review(
            work_order_id="wo-one",
            request_id="request-global-key",
            idempotency_key=key,
            expected_fingerprint=_draft_fingerprint(),
        )
        with pytest.raises(DomainValidationError, match="review types"):
            reviews.create_risk_review(
                request_id="request-global-key",
                conversation_id="conv-1",
                risk_category="operation_command",
                restricted_answer_hash="sha256:" + "a" * 64,
                idempotency_key=key,
            )

    with database.connection() as connection:
        total = connection.execute(
            "SELECT (SELECT COUNT(*) FROM risk_reviews) + (SELECT COUNT(*) FROM work_order_reviews)"
        ).fetchone()[0]
    assert total == 1


def test_concurrent_cross_type_creation_allows_exactly_one_review(tmp_path: Path) -> None:
    database, _, reviews, _ = _setup(tmp_path)
    key = "concurrent-global-review-key"

    def create_risk() -> str:
        try:
            reviews.create_risk_review(
                request_id="request-concurrent-global",
                conversation_id="conv-1",
                risk_category="operation_command",
                restricted_answer_hash="sha256:" + "b" * 64,
                idempotency_key=key,
            )
        except DomainValidationError:
            return "conflict"
        return "risk"

    def create_work_order() -> str:
        try:
            reviews.create_work_order_review(
                work_order_id="wo-one",
                request_id="request-concurrent-global",
                idempotency_key=key,
                expected_fingerprint=_draft_fingerprint(),
            )
        except DomainValidationError:
            return "conflict"
        return "work_order"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create_risk), executor.submit(create_work_order)]
        results = [future.result() for future in futures]

    assert results.count("conflict") == 1
    assert sum(result in {"risk", "work_order"} for result in results) == 1
    with database.connection() as connection:
        total = connection.execute(
            "SELECT (SELECT COUNT(*) FROM risk_reviews) + (SELECT COUNT(*) FROM work_order_reviews)"
        ).fetchone()[0]
    assert total == 1


def test_work_order_has_only_one_review_for_its_entire_lifecycle(tmp_path: Path) -> None:
    database, _, reviews, _ = _setup(tmp_path)
    first = reviews.create_work_order_review(
        work_order_id="wo-one",
        request_id="request-first-review",
        idempotency_key="first-work-order-review-key",
        expected_fingerprint=_draft_fingerprint(),
    )

    with pytest.raises(DomainValidationError, match="already has a review"):
        reviews.create_work_order_review(
            work_order_id="wo-one",
            request_id="request-second-review",
            idempotency_key="second-work-order-review-key",
            expected_fingerprint=_draft_fingerprint(),
        )

    retry = reviews.create_work_order_review(
        work_order_id="wo-one",
        request_id="request-first-review",
        idempotency_key="first-work-order-review-key",
        expected_fingerprint=_draft_fingerprint(),
    )
    assert retry == first
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 1


def test_concurrent_different_keys_create_only_one_review_per_draft(tmp_path: Path) -> None:
    database, _, reviews, _ = _setup(tmp_path)

    def create(index: int) -> str:
        try:
            reviews.create_work_order_review(
                work_order_id="wo-one",
                request_id=f"request-concurrent-work-{index}",
                idempotency_key=f"concurrent-work-review-{index}",
                expected_fingerprint=_draft_fingerprint(),
            )
        except DomainValidationError:
            return "conflict"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, range(2)))

    assert results.count("created") == 1
    assert results.count("conflict") == 1
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_order_reviews").fetchone()[0] == 1


def test_dirty_duplicate_pending_review_cannot_overwrite_terminal_draft(
    tmp_path: Path,
) -> None:
    database, work_orders, reviews, _ = _setup(tmp_path)
    created_at = datetime(2026, 7, 22, 9, tzinfo=UTC).isoformat()
    with database.transaction() as connection:
        connection.execute("DROP INDEX ux_work_order_reviews_work_order_id")
        connection.executemany(
            """
            INSERT INTO work_order_reviews (
                review_id, work_order_id, request_id, idempotency_key, status, created_at
            ) VALUES (?, 'wo-one', ?, ?, 'PENDING_REVIEW', ?)
            """,
            (
                ("dirty-review-one", "dirty-request-one", "dirty-key-one", created_at),
                ("dirty-review-two", "dirty-request-two", "dirty-key-two", created_at),
            ),
        )

    reviews.decide_work_order_review(
        "dirty-review-one",
        status=ReviewStatus.REVIEWED,
        decision="first terminal decision",
        reviewer_id="reviewer-one",
    )
    with pytest.raises(DomainValidationError, match="invariant"):
        reviews.decide_work_order_review(
            "dirty-review-two",
            status=ReviewStatus.REJECTED,
            decision="must not overwrite",
            reviewer_id="reviewer-two",
        )

    draft = work_orders.get("wo-one")
    second_review = reviews.get_work_order_review("dirty-review-two")
    assert draft is not None
    assert draft.approval_status is ReviewStatus.REVIEWED
    assert second_review is not None
    assert second_review.status is ReviewStatus.PENDING_REVIEW


class FailingReviewRepository:
    def create_risk_review(self, **_: object):
        raise RuntimeError("database unavailable")

    def create_work_order_review(self, **_: object):
        raise RuntimeError("database unavailable")


def test_persistence_failure_raises_controlled_error_without_fake_review_id(
    tmp_path: Path,
) -> None:
    _, work_orders, _, _ = _setup(tmp_path)
    coordinator = SafetyReviewCoordinator(FailingReviewRepository(), work_orders)

    with pytest.raises(ReviewCoordinationError, match="review could not be persisted"):
        coordinator.coordinate(
            request_id="request-failure",
            conversation_id="conv-1",
            intent=Intent.SAFETY_QUERY,
            user_query="直接停机并切断电源",
            safety=_restricted_operation(),
            idempotency_key="request-failure:review",
        )


@pytest.mark.parametrize("decision_status", [ReviewStatus.REVIEWED, ReviewStatus.REJECTED])
def test_work_order_decision_changes_only_approval_status(
    tmp_path: Path,
    decision_status: ReviewStatus,
) -> None:
    _, work_orders, reviews, coordinator = _setup(tmp_path)
    before = work_orders.get("wo-one")
    assert before is not None
    result = coordinator.coordinate(
        request_id=f"request-decision-{decision_status.value}",
        conversation_id="conv-1",
        intent=Intent.WORK_ORDER_DRAFT,
        user_query="生成泵体检修工单草稿",
        safety=_draft_review(),
        idempotency_key=f"request-decision:{decision_status.value}",
        work_order_id="wo-one",
    )
    assert result.review_id is not None

    reviews.decide_work_order_review(
        result.review_id,
        status=decision_status,
        decision="人工决定",
        reviewer_id="reviewer-1",
    )
    after = work_orders.get("wo-one")

    assert after is not None
    assert after.approval_status is decision_status
    assert after.status == "DRAFT"
    assert after.executed is False
    assert after.model_dump(exclude={"approval_status"}) == before.model_dump(
        exclude={"approval_status"}
    )
    with pytest.raises(DomainValidationError, match="pending"):
        reviews.decide_work_order_review(
            result.review_id,
            status=(
                ReviewStatus.REJECTED
                if decision_status is ReviewStatus.REVIEWED
                else ReviewStatus.REVIEWED
            ),
            decision="反向决定",
            reviewer_id="reviewer-2",
        )


def test_review_decision_rejects_pending_and_blank_audit_fields(tmp_path: Path) -> None:
    _, _, reviews, coordinator = _setup(tmp_path)
    result = coordinator.coordinate(
        request_id="request-audit-validation",
        conversation_id="conv-1",
        intent=Intent.SAFETY_QUERY,
        user_query="直接停机并切断电源",
        safety=_restricted_operation(),
        idempotency_key="request-audit-validation:review",
    )
    assert result.review_id is not None

    with pytest.raises(DomainValidationError, match="terminal"):
        reviews.decide_risk_review(
            result.review_id,
            status=ReviewStatus.PENDING_REVIEW,
            decision="保持 pending",
            reviewer_id="reviewer-1",
        )
    with pytest.raises(DomainValidationError, match=r"decision.*reviewer"):
        reviews.decide_risk_review(
            result.review_id,
            status=ReviewStatus.REVIEWED,
            decision=" ",
            reviewer_id=" ",
        )
    pending = reviews.get_risk_review(result.review_id)
    assert pending is not None
    assert pending.status is ReviewStatus.PENDING_REVIEW


def test_work_order_review_rejects_non_pending_draft(tmp_path: Path) -> None:
    database, _, reviews, _ = _setup(tmp_path)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE work_orders SET approval_status = 'REVIEWED' WHERE work_order_id = 'wo-one'"
        )

    with pytest.raises(DomainValidationError, match="valid pending work order draft"):
        reviews.create_work_order_review(
            work_order_id="wo-one",
            request_id="request-invalid-state",
            idempotency_key="request-invalid-state:review",
            expected_fingerprint=_draft_fingerprint(),
        )

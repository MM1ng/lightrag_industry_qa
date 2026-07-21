"""Safety-review coordination without equipment execution or model calls."""

from __future__ import annotations

import hashlib
from typing import Protocol

from pydantic import BaseModel, ConfigDict, model_validator

from industrial_energy_agent.domain.enums import (
    ActionMode,
    Intent,
    ReviewStatus,
    ReviewType,
    RiskLevel,
    WorkOrderStatus,
)
from industrial_energy_agent.domain.errors import DomainValidationError
from industrial_energy_agent.domain.models import RiskReview, WorkOrderDraft, WorkOrderReview
from industrial_energy_agent.domain.safety_rules import (
    SafetyAssessment,
    SafetyDisposition,
    SafetyOutputReview,
    classify_input,
    fail_closed_draft_inspection,
    inspect_work_order_draft,
    merge_safety_output_reviews,
    review_output,
    work_order_review_fingerprint,
)


class ReviewCoordinationError(DomainValidationError):
    """A review could not be safely selected or durably persisted."""


class ReviewRepositoryBoundary(Protocol):
    def create_risk_review(
        self,
        *,
        request_id: str,
        conversation_id: str,
        risk_category: str,
        restricted_answer_hash: str,
        idempotency_key: str,
    ) -> RiskReview: ...

    def create_work_order_review(
        self,
        *,
        work_order_id: str,
        request_id: str,
        idempotency_key: str,
        expected_fingerprint: str,
    ) -> WorkOrderReview: ...


class WorkOrderRepositoryBoundary(Protocol):
    def get(self, work_order_id: str) -> WorkOrderDraft | None: ...


class ReviewCoordinationResult(BaseModel):
    """Typed result; an absent review never carries a fabricated identifier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    review_type: ReviewType | None = None
    review_id: str | None = None
    status: ReviewStatus | None = None
    safety: SafetyOutputReview

    @model_validator(mode="after")
    def review_fields_are_all_present_or_all_absent(self) -> ReviewCoordinationResult:
        fields = (self.review_type, self.review_id, self.status)
        if any(value is None for value in fields) and any(value is not None for value in fields):
            raise ValueError("review result fields must be all present or all absent")
        return self


def _restricted_answer_hash(answer: str) -> str:
    return f"sha256:{hashlib.sha256(answer.encode('utf-8')).hexdigest()}"


class SafetyReviewCoordinator:
    """Choose exactly one review type, or no review, from a reviewed output."""

    def __init__(
        self,
        reviews: ReviewRepositoryBoundary,
        work_orders: WorkOrderRepositoryBoundary,
    ) -> None:
        self._reviews = reviews
        self._work_orders = work_orders

    def coordinate(
        self,
        *,
        request_id: str,
        conversation_id: str,
        intent: Intent,
        user_query: str,
        safety: SafetyOutputReview,
        idempotency_key: str,
        work_order_id: str | None = None,
    ) -> ReviewCoordinationResult:
        if not all(value.strip() for value in (request_id, conversation_id, idempotency_key)):
            raise ReviewCoordinationError("review coordination fields must be non-empty")
        if not isinstance(intent, Intent):
            raise ReviewCoordinationError("a valid intent is required")

        input_assessment = self._classify_query(user_query)
        try:
            external_safety = SafetyOutputReview.model_validate(safety.model_dump())
        except Exception:
            external_safety = None
        safety = self._canonical_output_review(
            input_assessment=input_assessment,
            external_safety=external_safety,
        )

        if (
            work_order_id is not None
            and intent is Intent.WORK_ORDER_DRAFT
            and input_assessment.action_mode is ActionMode.DRAFT_REQUEST
            and not input_assessment.classification_failed
            and self._allows_draft_review(safety)
            and external_safety is not None
            and self._allows_draft_review(external_safety)
        ):
            draft = self._require_valid_draft(
                work_order_id,
                conversation_id=conversation_id,
                safety=safety,
            )
            try:
                inspected_safety = SafetyOutputReview.model_validate(
                    inspect_work_order_draft(draft).model_dump()
                )
            except Exception:
                inspected_safety = fail_closed_draft_inspection()
            if inspected_safety.allowed_for_review:
                try:
                    work_order_review = self._reviews.create_work_order_review(
                        work_order_id=work_order_id,
                        request_id=request_id,
                        idempotency_key=idempotency_key,
                        expected_fingerprint=work_order_review_fingerprint(draft),
                    )
                except Exception as error:
                    raise ReviewCoordinationError("review could not be persisted") from error
                return ReviewCoordinationResult(
                    review_type=ReviewType.WORK_ORDER_REVIEW,
                    review_id=work_order_review.review_id,
                    status=work_order_review.status,
                    safety=safety,
                )
            safety = inspected_safety

        if safety.approval_required or safety.prohibited:
            try:
                risk_review = self._reviews.create_risk_review(
                    request_id=request_id,
                    conversation_id=conversation_id,
                    risk_category=safety.action_mode.value,
                    restricted_answer_hash=_restricted_answer_hash(safety.answer),
                    idempotency_key=idempotency_key,
                )
            except Exception as error:
                raise ReviewCoordinationError("review could not be persisted") from error
            return ReviewCoordinationResult(
                review_type=ReviewType.RISK_REVIEW,
                review_id=risk_review.review_id,
                status=risk_review.status,
                safety=safety,
            )

        return ReviewCoordinationResult(safety=safety)

    @staticmethod
    def _classify_query(user_query: str) -> SafetyAssessment:
        try:
            return SafetyAssessment.model_validate(classify_input(user_query).model_dump())
        except Exception:
            return SafetyAssessment(
                action_mode=ActionMode.INFORMATIONAL,
                risk_level=RiskLevel.HIGH,
                prohibited=False,
                approval_required=True,
                disposition=SafetyDisposition.RESTRICTED,
                classification_failed=True,
                reason_codes=("classification_failed",),
            )

    @staticmethod
    def _canonical_output_review(
        *,
        input_assessment: SafetyAssessment,
        external_safety: SafetyOutputReview | None,
    ) -> SafetyOutputReview:
        if external_safety is None:
            return review_output(
                "safety output unavailable",
                input_assessment=input_assessment,
                safety_check_failed=True,
            )
        canonical = review_output(
            external_safety.answer,
            input_assessment=input_assessment,
        )
        return merge_safety_output_reviews(canonical, external_safety)

    @staticmethod
    def _allows_draft_review(safety: SafetyOutputReview) -> bool:
        return bool(
            safety.action_mode is ActionMode.DRAFT_REQUEST
            and safety.disposition is SafetyDisposition.RESTRICTED
            and safety.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            and safety.approval_required
            and not safety.prohibited
            and not safety.safety_check_failed
            and not safety.sensitive_content
            and safety.draft_allowed
            and safety.allowed_for_review
        )

    def _require_valid_draft(
        self,
        work_order_id: str,
        *,
        conversation_id: str,
        safety: SafetyOutputReview,
    ) -> WorkOrderDraft:
        try:
            draft = self._work_orders.get(work_order_id)
        except Exception as error:
            raise ReviewCoordinationError("valid persisted work order draft is required") from error
        if (
            safety.action_mode is not ActionMode.DRAFT_REQUEST
            or draft is None
            or draft.conversation_id != conversation_id
            or draft.status is not WorkOrderStatus.DRAFT
            or draft.approval_status is not ReviewStatus.PENDING_REVIEW
            or draft.executed
        ):
            raise ReviewCoordinationError("valid persisted work order draft is required")
        return draft

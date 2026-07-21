"""Separate risk-review and work-order-review lifecycle persistence."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from industrial_energy_agent.domain.enums import ReviewStatus, WorkOrderStatus
from industrial_energy_agent.domain.errors import DomainValidationError
from industrial_energy_agent.domain.models import RiskReview, WorkOrderDraft, WorkOrderReview
from industrial_energy_agent.domain.safety_rules import work_order_review_fingerprint
from industrial_energy_agent.persistence.database import Database


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise DomainValidationError("review timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ReviewRepository:
    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._database = database
        self._clock = clock

    @staticmethod
    def _risk_from_row(row: sqlite3.Row) -> RiskReview:
        return RiskReview.model_validate(dict(row))

    @staticmethod
    def _work_order_from_row(row: sqlite3.Row) -> WorkOrderReview:
        return WorkOrderReview.model_validate(dict(row))

    @staticmethod
    def _review_target_from_row(row: sqlite3.Row) -> WorkOrderDraft:
        try:
            payload = json.loads(row["payload_json"])
            payload_draft = WorkOrderDraft.model_validate(payload)
            column_created_at = datetime.fromisoformat(
                str(row["created_at"]).replace("Z", "+00:00")
            )
        except Exception as error:
            raise DomainValidationError("work order review target is inconsistent") from error
        if (
            payload_draft.work_order_id != row["work_order_id"]
            or payload_draft.request_id != row["request_id"]
            or payload_draft.conversation_id != row["conversation_id"]
            or payload_draft.diagnosis_id != row["diagnosis_id"]
            or payload_draft.status.value != row["status"]
            or payload_draft.executed is not bool(row["executed"])
            or payload_draft.created_at != column_created_at
        ):
            raise DomainValidationError("work order review target is inconsistent")
        return payload_draft.model_copy(
            update={"approval_status": ReviewStatus(row["approval_status"])}
        )

    @classmethod
    def _require_review_target(
        cls,
        row: sqlite3.Row | None,
        *,
        expected_fingerprint: str,
        require_pending: bool,
    ) -> None:
        if row is None:
            raise DomainValidationError(
                "work order review requires a valid pending work order draft"
            )
        draft = cls._review_target_from_row(row)
        if work_order_review_fingerprint(draft) != expected_fingerprint:
            raise DomainValidationError("work order draft changed after safety inspection")
        if (
            draft.status is not WorkOrderStatus.DRAFT
            or draft.executed
            or (require_pending and draft.approval_status is not ReviewStatus.PENDING_REVIEW)
        ):
            raise DomainValidationError(
                "work order review requires a valid pending work order draft"
            )

    def get_risk_review(self, review_id: str) -> RiskReview | None:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM risk_reviews WHERE review_id = ?",
                (review_id,),
            ).fetchone()
        return None if row is None else self._risk_from_row(row)

    def get_work_order_review(self, review_id: str) -> WorkOrderReview | None:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM work_order_reviews WHERE review_id = ?",
                (review_id,),
            ).fetchone()
        return None if row is None else self._work_order_from_row(row)

    def create_risk_review(
        self,
        *,
        request_id: str,
        conversation_id: str,
        risk_category: str,
        restricted_answer_hash: str,
        idempotency_key: str,
        work_order_id: str | None = None,
    ) -> RiskReview:
        if work_order_id is not None:
            raise DomainValidationError("risk review cannot reference a work order")
        review = RiskReview(
            review_id=f"risk-{uuid4()}",
            request_id=request_id,
            conversation_id=conversation_id,
            risk_category=risk_category,
            restricted_answer_hash=restricted_answer_hash,
            idempotency_key=idempotency_key,
            created_at=self._clock(),
        )
        try:
            with self._database.transaction() as connection:
                cross_type = connection.execute(
                    "SELECT 1 FROM work_order_reviews WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if cross_type is not None:
                    raise DomainValidationError("idempotency key conflicts across review types")
                existing = connection.execute(
                    "SELECT * FROM risk_reviews WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    persisted = self._risk_from_row(existing)
                    if (
                        persisted.request_id != request_id
                        or persisted.conversation_id != conversation_id
                        or persisted.risk_category != risk_category
                        or persisted.restricted_answer_hash != restricted_answer_hash
                    ):
                        raise DomainValidationError("risk review idempotency key conflicts")
                    return persisted
                connection.execute(
                    """
                    INSERT INTO risk_reviews (
                        review_id, request_id, conversation_id, risk_category,
                        restricted_answer_hash, idempotency_key, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review.review_id,
                        review.request_id,
                        review.conversation_id,
                        review.risk_category,
                        review.restricted_answer_hash,
                        review.idempotency_key,
                        review.status.value,
                        _timestamp(review.created_at),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise DomainValidationError("risk review could not be persisted") from error
        return review

    def create_work_order_review(
        self,
        *,
        work_order_id: str,
        request_id: str,
        idempotency_key: str,
        expected_fingerprint: str,
    ) -> WorkOrderReview:
        if re.fullmatch(r"sha256:[0-9a-f]{64}", expected_fingerprint) is None:
            raise DomainValidationError("work order review requires a valid target fingerprint")
        review = WorkOrderReview(
            review_id=f"work-order-{uuid4()}",
            work_order_id=work_order_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            created_at=self._clock(),
        )
        try:
            with self._database.transaction() as connection:
                cross_type = connection.execute(
                    "SELECT 1 FROM risk_reviews WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if cross_type is not None:
                    raise DomainValidationError("idempotency key conflicts across review types")
                existing = connection.execute(
                    "SELECT * FROM work_order_reviews WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                target = connection.execute(
                    "SELECT * FROM work_orders WHERE work_order_id = ?",
                    (work_order_id,),
                ).fetchone()
                if existing is not None:
                    persisted = self._work_order_from_row(existing)
                    if (
                        persisted.work_order_id != work_order_id
                        or persisted.request_id != request_id
                    ):
                        raise DomainValidationError("work order review idempotency key conflicts")
                    self._require_review_target(
                        target,
                        expected_fingerprint=expected_fingerprint,
                        require_pending=False,
                    )
                    return persisted
                existing_for_draft = connection.execute(
                    "SELECT 1 FROM work_order_reviews WHERE work_order_id = ?",
                    (work_order_id,),
                ).fetchone()
                if existing_for_draft is not None:
                    raise DomainValidationError("work order already has a review")
                self._require_review_target(
                    target,
                    expected_fingerprint=expected_fingerprint,
                    require_pending=True,
                )
                connection.execute(
                    """
                    INSERT INTO work_order_reviews (
                        review_id, work_order_id, request_id, idempotency_key,
                        status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review.review_id,
                        review.work_order_id,
                        review.request_id,
                        review.idempotency_key,
                        review.status.value,
                        _timestamp(review.created_at),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise DomainValidationError("work order review could not be persisted") from error
        return review

    @staticmethod
    def _require_terminal(status: ReviewStatus) -> None:
        if status not in {ReviewStatus.REVIEWED, ReviewStatus.REJECTED}:
            raise DomainValidationError("review decision requires a terminal status")

    @staticmethod
    def _require_audit_fields(decision: str, reviewer_id: str) -> None:
        if not decision.strip() or not reviewer_id.strip():
            raise DomainValidationError("review decision and reviewer must be non-empty")

    def decide_risk_review(
        self,
        review_id: str,
        *,
        status: ReviewStatus,
        decision: str,
        reviewer_id: str,
    ) -> RiskReview:
        self._require_terminal(status)
        self._require_audit_fields(decision, reviewer_id)
        reviewed_at = self._clock()
        with self._database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE risk_reviews
                SET status = ?, decision = ?, reviewer_id = ?, reviewed_at = ?
                WHERE review_id = ? AND status = 'PENDING_REVIEW'
                """,
                (status.value, decision, reviewer_id, _timestamp(reviewed_at), review_id),
            )
            if cursor.rowcount != 1:
                raise DomainValidationError("risk review must exist and be pending")
        review = self.get_risk_review(review_id)
        if review is None:
            raise RuntimeError("risk review update lost its target")
        return review

    def decide_work_order_review(
        self,
        review_id: str,
        *,
        status: ReviewStatus,
        decision: str,
        reviewer_id: str,
    ) -> WorkOrderReview:
        self._require_terminal(status)
        self._require_audit_fields(decision, reviewer_id)
        reviewed_at = self._clock()
        with self._database.transaction() as connection:
            row = connection.execute(
                """
                SELECT work_order_id FROM work_order_reviews
                WHERE review_id = ? AND status = 'PENDING_REVIEW'
                """,
                (review_id,),
            ).fetchone()
            if row is None:
                raise DomainValidationError("work order review must exist and be pending")
            connection.execute(
                """
                UPDATE work_order_reviews
                SET status = ?, decision = ?, reviewer_id = ?, reviewed_at = ?
                WHERE review_id = ? AND status = 'PENDING_REVIEW'
                """,
                (status.value, decision, reviewer_id, _timestamp(reviewed_at), review_id),
            )
            cursor = connection.execute(
                """
                UPDATE work_orders SET approval_status = ?
                WHERE work_order_id = ? AND status = 'DRAFT' AND executed = 0
                    AND approval_status = 'PENDING_REVIEW'
                """,
                (status.value, row["work_order_id"]),
            )
            if cursor.rowcount != 1:
                raise DomainValidationError("work order draft invariant was violated")
        review = self.get_work_order_review(review_id)
        if review is None:
            raise RuntimeError("work order review update lost its target")
        return review

"""Persistence for non-executing work-order drafts."""

from __future__ import annotations

import json
import sqlite3

from industrial_energy_agent.domain.enums import ReviewStatus
from industrial_energy_agent.domain.errors import DomainValidationError
from industrial_energy_agent.domain.models import WorkOrderDraft
from industrial_energy_agent.persistence.database import Database


class WorkOrderRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, draft: WorkOrderDraft) -> WorkOrderDraft:
        if draft.approval_status is not ReviewStatus.PENDING_REVIEW:
            raise DomainValidationError("new work order must be pending review")
        with self._database.transaction() as connection:
            diagnosis = connection.execute(
                """
                SELECT conversation_id FROM diagnoses WHERE diagnosis_id = ?
                """,
                (draft.diagnosis_id,),
            ).fetchone()
            if diagnosis is None or diagnosis["conversation_id"] != draft.conversation_id:
                raise DomainValidationError(
                    "work order diagnosis must exist in the same conversation"
                )
            try:
                connection.execute(
                    """
                    INSERT INTO work_orders (
                        work_order_id, request_id, conversation_id, diagnosis_id,
                        payload_json, status, approval_status, executed, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        draft.work_order_id,
                        draft.request_id,
                        draft.conversation_id,
                        draft.diagnosis_id,
                        draft.model_dump_json(),
                        draft.status.value,
                        draft.approval_status.value,
                        int(draft.executed),
                        draft.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise DomainValidationError("work order could not be persisted") from error
        persisted = self.get(draft.work_order_id)
        if persisted is None:
            raise RuntimeError("work order insert did not persist a row")
        return persisted

    def get(self, work_order_id: str) -> WorkOrderDraft | None:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT payload_json, status, approval_status, executed
                FROM work_orders WHERE work_order_id = ?
                """,
                (work_order_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        payload.update(
            status=row["status"],
            approval_status=row["approval_status"],
            executed=bool(row["executed"]),
        )
        return WorkOrderDraft.model_validate(payload)

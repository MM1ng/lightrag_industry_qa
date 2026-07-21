"""Conversation-scoped summaries, traces, and diagnosis persistence."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from industrial_energy_agent.domain.errors import DomainValidationError
from industrial_energy_agent.domain.models import DiagnosisRecord, TraceEvent
from industrial_energy_agent.persistence.database import Database


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _to_db_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        raise DomainValidationError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _from_db_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _json_payload(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True, slots=True)
class SessionRecord:
    conversation_id: str
    selected_cycle_id: int | None
    summary: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RequestSummaryRecord:
    request_id: str
    conversation_id: str
    intent: str
    summary: dict[str, Any]
    created_at: datetime


class SessionRepository:
    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._database = database
        self._clock = clock

    def ensure_session(
        self,
        conversation_id: str,
        *,
        summary: Mapping[str, object] | None = None,
    ) -> SessionRecord:
        if not conversation_id.strip():
            raise DomainValidationError("conversation_id must be non-empty")
        timestamp = _to_db_datetime(self._clock())
        summary_json = _json_payload(summary or {})
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO conversation_sessions (
                    conversation_id, selected_cycle_id, summary_json, created_at, updated_at
                ) VALUES (?, NULL, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    summary_json = excluded.summary_json,
                    updated_at = excluded.updated_at
                """,
                (conversation_id, summary_json, timestamp, timestamp),
            )
        record = self.get_session(conversation_id)
        if record is None:
            raise RuntimeError("session upsert did not persist a row")
        return record

    def set_selected_cycle(self, conversation_id: str, cycle_id: int | None) -> None:
        if cycle_id is not None and (
            isinstance(cycle_id, bool)
            or not isinstance(cycle_id, int)
            or not 1 <= cycle_id <= 2_205
        ):
            raise DomainValidationError("selected cycle must be between 1 and 2205")
        with self._database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE conversation_sessions
                SET selected_cycle_id = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (cycle_id, _to_db_datetime(self._clock()), conversation_id),
            )
            if cursor.rowcount != 1:
                raise DomainValidationError("conversation does not exist")

    def get_session(self, conversation_id: str) -> SessionRecord | None:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT conversation_id, selected_cycle_id, summary_json, created_at, updated_at
                FROM conversation_sessions WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        return SessionRecord(
            conversation_id=row["conversation_id"],
            selected_cycle_id=row["selected_cycle_id"],
            summary=json.loads(row["summary_json"]),
            created_at=_from_db_datetime(row["created_at"]),
            updated_at=_from_db_datetime(row["updated_at"]),
        )

    def save_request_summary(
        self,
        *,
        request_id: str,
        conversation_id: str,
        intent: str,
        summary: Mapping[str, object],
    ) -> RequestSummaryRecord:
        timestamp = _to_db_datetime(self._clock())
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO request_summaries (
                    request_id, conversation_id, intent, summary_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (request_id, conversation_id, intent, _json_payload(summary), timestamp),
            )
        record = self.get_request_summary(request_id)
        if record is None:
            raise RuntimeError("request summary insert did not persist a row")
        return record

    def get_request_summary(self, request_id: str) -> RequestSummaryRecord | None:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT request_id, conversation_id, intent, summary_json, created_at
                FROM request_summaries WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        return RequestSummaryRecord(
            request_id=row["request_id"],
            conversation_id=row["conversation_id"],
            intent=row["intent"],
            summary=json.loads(row["summary_json"]),
            created_at=_from_db_datetime(row["created_at"]),
        )

    def append_trace(self, conversation_id: str, event: TraceEvent) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO trace_events (
                    request_id, conversation_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    event.request_id,
                    conversation_id,
                    event.model_dump_json(),
                    _to_db_datetime(self._clock()),
                ),
            )

    def list_traces(self, request_id: str) -> tuple[TraceEvent, ...]:
        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM trace_events
                WHERE request_id = ? ORDER BY event_id
                """,
                (request_id,),
            ).fetchall()
        return tuple(TraceEvent.model_validate_json(row["payload_json"]) for row in rows)

    def save_diagnosis(self, diagnosis: DiagnosisRecord) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO diagnoses (
                    diagnosis_id, request_id, conversation_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    diagnosis.diagnosis_id,
                    diagnosis.request_id,
                    diagnosis.conversation_id,
                    diagnosis.model_dump_json(),
                    _to_db_datetime(self._clock()),
                ),
            )

    def get_diagnosis(
        self,
        diagnosis_id: str,
        *,
        conversation_id: str,
    ) -> DiagnosisRecord | None:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM diagnoses
                WHERE diagnosis_id = ? AND conversation_id = ?
                """,
                (diagnosis_id, conversation_id),
            ).fetchone()
        return None if row is None else DiagnosisRecord.model_validate_json(row["payload_json"])

    def get_latest_diagnosis(self, conversation_id: str) -> DiagnosisRecord | None:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM diagnoses
                WHERE conversation_id = ? ORDER BY rowid DESC LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        return None if row is None else DiagnosisRecord.model_validate_json(row["payload_json"])

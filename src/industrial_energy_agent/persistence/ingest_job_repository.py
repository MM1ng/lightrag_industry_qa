"""Atomic SQLite lease lifecycle for single-node ingestion workers."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from industrial_energy_agent.domain.enums import IngestJobStatus
from industrial_energy_agent.domain.errors import (
    DomainValidationError,
    sanitize_public_error_message,
)
from industrial_energy_agent.persistence.database import Database


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise DomainValidationError("ingest timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _safe_error(value: str) -> str:
    sanitized = sanitize_public_error_message(value)
    return sanitized[:500]


@dataclass(frozen=True, slots=True)
class IngestJob:
    job_id: str
    document_id: str
    idempotency_key: str
    remote_file_source: str
    track_id: str | None
    status: IngestJobStatus
    attempt_count: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    remote_call_started: bool
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class IngestJobRepository:
    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = _utc_now,
        default_lease_seconds: int = 60,
    ) -> None:
        if default_lease_seconds <= 0:
            raise ValueError("default_lease_seconds must be positive")
        self._database = database
        self._clock = clock
        self._default_lease_seconds = default_lease_seconds

    @staticmethod
    def _from_row(row: sqlite3.Row) -> IngestJob:
        return IngestJob(
            job_id=row["job_id"],
            document_id=row["document_id"],
            idempotency_key=row["idempotency_key"],
            remote_file_source=row["remote_file_source"] or f"{row['document_id']}.txt",
            track_id=row["track_id"],
            status=IngestJobStatus(row["status"]),
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            lease_owner=row["lease_owner"],
            lease_expires_at=_parse_timestamp(row["lease_expires_at"]),
            remote_call_started=bool(row["remote_call_started"]),
            last_error=row["last_error"],
            created_at=_parse_timestamp(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_parse_timestamp(row["updated_at"]),  # type: ignore[arg-type]
        )

    def get(self, job_id: str) -> IngestJob | None:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM ingest_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return None if row is None else self._from_row(row)

    def get_next_expired_remote_call(self) -> IngestJob | None:
        now = _timestamp(self._clock())
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM ingest_jobs
                WHERE status = 'RUNNING' AND remote_call_started = 1
                    AND lease_expires_at <= ?
                ORDER BY updated_at, job_id LIMIT 1
                """,
                (now,),
            ).fetchone()
        return None if row is None else self._from_row(row)

    def create_pending(
        self,
        document_id: str,
        idempotency_key: str,
        *,
        remote_file_source: str | None = None,
        max_attempts: int = 3,
    ) -> IngestJob:
        if not document_id.strip() or not idempotency_key.strip():
            raise DomainValidationError("document and idempotency identifiers are required")
        if max_attempts <= 0:
            raise DomainValidationError("max_attempts must be positive")
        resolved_file_source = remote_file_source or f"{document_id}.txt"
        if (
            not resolved_file_source.strip()
            or resolved_file_source in {".", ".."}
            or "/" in resolved_file_source
            or "\\" in resolved_file_source
        ):
            raise DomainValidationError("remote file source must be a non-empty basename")
        with self._database.connection() as connection:
            existing = connection.execute(
                "SELECT * FROM ingest_jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if existing is not None:
            job = self._from_row(existing)
            if (
                job.document_id != document_id
                or job.max_attempts != max_attempts
                or job.remote_file_source != resolved_file_source
            ):
                raise DomainValidationError("ingest idempotency key conflicts")
            return job

        now = _timestamp(self._clock())
        job_id = f"ingest-{uuid4()}"
        try:
            with self._database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO ingest_jobs (
                        job_id, document_id, idempotency_key, remote_file_source, status,
                        attempt_count, max_attempts, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'PENDING', 0, ?, ?, ?)
                    """,
                    (
                        job_id,
                        document_id,
                        idempotency_key,
                        resolved_file_source,
                        max_attempts,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError:
            with self._database.connection() as connection:
                concurrent = connection.execute(
                    "SELECT * FROM ingest_jobs WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
            if concurrent is None:
                raise
            job = self._from_row(concurrent)
            if (
                job.document_id != document_id
                or job.max_attempts != max_attempts
                or job.remote_file_source != resolved_file_source
            ):
                raise DomainValidationError("ingest idempotency key conflicts") from None
            return job
        created = self.get(job_id)
        if created is None:
            raise RuntimeError("ingest job insert did not persist a row")
        return created

    @staticmethod
    def _validate_owner(owner: str) -> None:
        if not owner.strip():
            raise DomainValidationError("lease owner must be non-empty")

    def claim(
        self,
        job_id: str,
        *,
        owner: str,
        lease_until: datetime,
    ) -> IngestJob:
        self._validate_owner(owner)
        now = _timestamp(self._clock())
        lease = _timestamp(lease_until)
        with self._database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE ingest_jobs
                SET status = 'RUNNING', lease_owner = ?, lease_expires_at = ?,
                    attempt_count = attempt_count + 1,
                    remote_call_started = 0, last_error = NULL, updated_at = ?
                WHERE job_id = ? AND attempt_count < max_attempts AND (
                    status = 'PENDING'
                    OR (
                        status = 'RUNNING' AND lease_expires_at <= ?
                        AND remote_call_started = 0
                    )
                )
                """,
                (owner, lease, now, job_id, now),
            )
            if cursor.rowcount != 1:
                raise DomainValidationError("ingest job cannot be claimed")
        job = self.get(job_id)
        if job is None:
            raise RuntimeError("claimed ingest job disappeared")
        return job

    def claim_next(
        self,
        *,
        owner: str,
        lease_until: datetime | None = None,
    ) -> IngestJob | None:
        self._validate_owner(owner)
        now_value = self._clock()
        now = _timestamp(now_value)
        lease = _timestamp(
            lease_until
            if lease_until is not None
            else now_value + timedelta(seconds=self._default_lease_seconds)
        )
        with self._database.transaction() as connection:
            row = connection.execute(
                """
                SELECT job_id FROM ingest_jobs
                WHERE attempt_count < max_attempts AND (
                    status = 'PENDING'
                    OR (
                        status = 'RUNNING' AND lease_expires_at <= ?
                        AND remote_call_started = 0
                    )
                )
                ORDER BY created_at, job_id LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                return None
            cursor = connection.execute(
                """
                UPDATE ingest_jobs
                SET status = 'RUNNING', lease_owner = ?, lease_expires_at = ?,
                    attempt_count = attempt_count + 1,
                    remote_call_started = 0, last_error = NULL, updated_at = ?
                WHERE job_id = ? AND attempt_count < max_attempts AND (
                    status = 'PENDING'
                    OR (
                        status = 'RUNNING' AND lease_expires_at <= ?
                        AND remote_call_started = 0
                    )
                )
                """,
                (owner, lease, now, row["job_id"], now),
            )
            if cursor.rowcount != 1:
                return None
            claimed = connection.execute(
                "SELECT * FROM ingest_jobs WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()
        return None if claimed is None else self._from_row(claimed)

    def heartbeat(
        self,
        job_id: str,
        *,
        owner: str,
        lease_until: datetime,
    ) -> IngestJob:
        self._validate_owner(owner)
        now = _timestamp(self._clock())
        with self._database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE ingest_jobs SET lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND status = 'RUNNING' AND lease_owner = ?
                    AND lease_expires_at > ?
                """,
                (_timestamp(lease_until), now, job_id, owner, now),
            )
            if cursor.rowcount != 1:
                raise DomainValidationError("lease owner does not match or lease expired")
        return self._required(job_id)

    def mark_remote_call_started(self, job_id: str, *, owner: str) -> IngestJob:
        return self._owned_running_update(
            job_id,
            owner,
            "remote_call_started = 1",
            error_message="lease owner does not match or lease expired",
        )

    def mark_remote_accepted(
        self,
        job_id: str,
        *,
        owner: str,
        track_id: str,
    ) -> IngestJob:
        if not track_id.strip():
            raise DomainValidationError("track_id must be non-empty")
        return self._owned_running_update(
            job_id,
            owner,
            "track_id = ?",
            parameters=(track_id,),
            error_message="lease owner does not match or lease expired",
        )

    def mark_succeeded(self, job_id: str, *, owner: str) -> IngestJob:
        return self._owned_running_update(
            job_id,
            owner,
            "status = 'SUCCEEDED', lease_owner = NULL, lease_expires_at = NULL",
            error_message="lease owner does not match or lease expired",
        )

    def mark_failed(self, job_id: str, *, owner: str, error: str) -> IngestJob:
        return self._owned_running_update(
            job_id,
            owner,
            "status = 'FAILED', lease_owner = NULL, lease_expires_at = NULL, last_error = ?",
            parameters=(_safe_error(error),),
            error_message="lease owner does not match or lease expired",
        )

    def _owned_running_update(
        self,
        job_id: str,
        owner: str,
        assignment: str,
        *,
        parameters: tuple[object, ...] = (),
        error_message: str,
    ) -> IngestJob:
        self._validate_owner(owner)
        now = _timestamp(self._clock())
        with self._database.transaction() as connection:
            cursor = connection.execute(
                f"""
                UPDATE ingest_jobs SET {assignment}, updated_at = ?
                WHERE job_id = ? AND status = 'RUNNING' AND lease_owner = ?
                    AND lease_expires_at > ?
                """,
                (*parameters, now, job_id, owner, now),
            )
            if cursor.rowcount != 1:
                raise DomainValidationError(error_message)
        return self._required(job_id)

    def mark_reconcile_required(
        self,
        job_id: str,
        error: str,
        *,
        owner: str | None = None,
    ) -> IngestJob:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT status, lease_owner FROM ingest_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None or row["status"] == IngestJobStatus.SUCCEEDED.value:
                raise DomainValidationError("ingest job cannot require reconciliation")
            if row["status"] == IngestJobStatus.RUNNING.value and row["lease_owner"] != owner:
                raise DomainValidationError("lease owner does not match running job")
            connection.execute(
                """
                UPDATE ingest_jobs
                SET status = 'RECONCILE_REQUIRED', lease_owner = NULL,
                    lease_expires_at = NULL, last_error = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (_safe_error(error), _timestamp(self._clock()), job_id),
            )
        return self._required(job_id)

    def mark_reconciled_succeeded(self, job_id: str) -> IngestJob:
        now = _timestamp(self._clock())
        with self._database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE ingest_jobs
                SET status = 'SUCCEEDED', lease_owner = NULL,
                    lease_expires_at = NULL, last_error = NULL, updated_at = ?
                WHERE job_id = ? AND remote_call_started = 1 AND track_id IS NOT NULL
                    AND (
                        status = 'RECONCILE_REQUIRED'
                        OR (status = 'RUNNING' AND lease_expires_at <= ?)
                    )
                """,
                (now, job_id, now),
            )
            if cursor.rowcount != 1:
                raise DomainValidationError("ingest job is not an expired tracked remote call")
        return self._required(job_id)

    def requeue_after_confirmed_absent(self, job_id: str) -> IngestJob:
        now = _timestamp(self._clock())
        with self._database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE ingest_jobs
                SET status = 'PENDING', lease_owner = NULL, lease_expires_at = NULL,
                    remote_call_started = 0, track_id = NULL, last_error = NULL, updated_at = ?
                WHERE job_id = ? AND remote_call_started = 1 AND track_id IS NOT NULL
                    AND attempt_count < max_attempts AND (
                        status = 'RECONCILE_REQUIRED'
                        OR (status = 'RUNNING' AND lease_expires_at <= ?)
                    )
                """,
                (now, job_id, now),
            )
            if cursor.rowcount != 1:
                raise DomainValidationError("ingest job cannot be safely requeued")
        return self._required(job_id)

    def retry_failed(self, job_id: str) -> IngestJob:
        with self._database.transaction() as connection:
            row = connection.execute(
                """
                SELECT status, attempt_count, max_attempts
                FROM ingest_jobs WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if row is None or row["status"] != IngestJobStatus.FAILED.value:
                raise DomainValidationError("only a failed ingest job can be retried")
            if row["attempt_count"] >= row["max_attempts"]:
                raise DomainValidationError("ingest job reached maximum attempts")
            connection.execute(
                """
                UPDATE ingest_jobs SET status = 'PENDING', updated_at = ?
                WHERE job_id = ?
                """,
                (_timestamp(self._clock()), job_id),
            )
        return self._required(job_id)

    def _required(self, job_id: str) -> IngestJob:
        job = self.get(job_id)
        if job is None:
            raise RuntimeError("ingest job disappeared after update")
        return job

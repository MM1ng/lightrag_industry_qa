"""Persistence operations for incremental update jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.models import UpdateJob, UpdateJobStatus


class UpdateJobRepository:
    """Async CRUD for UpdateJob rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **values: Any) -> UpdateJob:
        job = UpdateJob(**values)
        self._session.add(job)
        await self._session.flush()
        return job

    async def get(self, job_id: str) -> UpdateJob | None:
        return await self._session.get(UpdateJob, job_id)

    async def get_by_kb_and_id(self, kb_id: str, job_id: str) -> UpdateJob | None:
        statement = select(UpdateJob).where(
            UpdateJob.id == job_id, UpdateJob.knowledge_base_id == kb_id
        )
        result = await self._session.execute(statement)
        return result.scalars().first()

    async def list_by_kb(
        self,
        kb_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[UpdateJob]:
        statement = (
            select(UpdateJob)
            .where(UpdateJob.knowledge_base_id == kb_id)
            .order_by(UpdateJob.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def find_active_for_kb(self, kb_id: str) -> UpdateJob | None:
        """Return the newest in-flight update job for a KB (serial guard)."""
        statement = (
            select(UpdateJob)
            .where(
                UpdateJob.knowledge_base_id == kb_id,
                UpdateJob.status.in_(
                    [
                        UpdateJobStatus.pending,
                        UpdateJobStatus.claimed,
                        UpdateJobStatus.running,
                        UpdateJobStatus.building,
                        UpdateJobStatus.validating,
                        UpdateJobStatus.recovery_required,
                    ]
                ),
            )
            .order_by(UpdateJob.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(statement)
        return result.scalars().first()

    async def find_by_candidate(self, candidate_generation_id: str) -> UpdateJob | None:
        statement = select(UpdateJob).where(
            UpdateJob.candidate_generation_id == candidate_generation_id
        )
        result = await self._session.execute(statement)
        return result.scalars().first()

    async def update(self, job_id: str, **values: Any) -> UpdateJob | None:
        job = await self.get(job_id)
        if job is None:
            return None
        for key, val in values.items():
            if hasattr(job, key):
                setattr(job, key, val)
        job.updated_at = datetime.now(tz=UTC)
        await self._session.flush()
        return job

    async def claim_next(
        self,
        *,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
        now: datetime,
        lease_expires_at: datetime,
    ) -> UpdateJob | None:
        """Atomically claim one pending or recovery-required job."""
        eligible_statuses = [
            UpdateJobStatus.pending,
            UpdateJobStatus.recovery_required,
        ]
        candidate_id = (
            select(UpdateJob.id)
            .where(
                UpdateJob.status.in_(eligible_statuses),
                UpdateJob.attempt < UpdateJob.max_attempts,
            )
            .order_by(UpdateJob.created_at.asc(), UpdateJob.id.asc())
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            update(UpdateJob)
            .where(
                UpdateJob.id == candidate_id,
                UpdateJob.status.in_(eligible_statuses),
            )
            .values(
                status=UpdateJobStatus.claimed,
                worker_id=worker_id,
                lease_token=lease_token,
                fencing_token=fencing_token,
                claimed_at=now,
                heartbeat_at=now,
                lease_expires_at=lease_expires_at,
                attempt=UpdateJob.attempt + 1,
                current_stage="claimed",
                updated_at=now,
            )
            .returning(UpdateJob)
        )
        result = await self._session.execute(statement)
        job = result.scalar_one_or_none()
        await self._session.commit()
        return job

    async def next_eligible(self) -> UpdateJob | None:
        statement = (
            select(UpdateJob)
            .where(
                UpdateJob.status.in_(
                    [UpdateJobStatus.pending, UpdateJobStatus.recovery_required]
                ),
                UpdateJob.attempt < UpdateJob.max_attempts,
            )
            .order_by(UpdateJob.created_at.asc(), UpdateJob.id.asc())
            .limit(1)
        )
        return (await self._session.execute(statement)).scalars().first()

    async def list_recoverable_ids(self) -> list[str]:
        statement = (
            select(UpdateJob.id)
            .where(
                UpdateJob.status.in_(
                    [UpdateJobStatus.pending, UpdateJobStatus.recovery_required]
                ),
                UpdateJob.attempt < UpdateJob.max_attempts,
            )
            .order_by(UpdateJob.created_at.asc(), UpdateJob.id.asc())
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def claim_specific(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
        now: datetime,
        lease_expires_at: datetime,
    ) -> UpdateJob | None:
        statement = (
            update(UpdateJob)
            .where(
                UpdateJob.id == job_id,
                UpdateJob.status.in_(
                    [UpdateJobStatus.pending, UpdateJobStatus.recovery_required]
                ),
                UpdateJob.attempt < UpdateJob.max_attempts,
            )
            .values(
                status=UpdateJobStatus.claimed,
                worker_id=worker_id,
                lease_token=lease_token,
                fencing_token=fencing_token,
                claimed_at=now,
                heartbeat_at=now,
                lease_expires_at=lease_expires_at,
                attempt=UpdateJob.attempt + 1,
                current_stage="claimed",
                updated_at=now,
            )
            .returning(UpdateJob)
        )
        result = await self._session.execute(statement)
        job = result.scalar_one_or_none()
        await self._session.commit()
        return job

    async def heartbeat_claim(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        statement = (
            update(UpdateJob)
            .where(
                UpdateJob.id == job_id,
                UpdateJob.worker_id == worker_id,
                UpdateJob.lease_token == lease_token,
                UpdateJob.fencing_token == fencing_token,
                UpdateJob.lease_expires_at > now,
                UpdateJob.status.in_(
                    [
                        UpdateJobStatus.claimed,
                        UpdateJobStatus.running,
                        UpdateJobStatus.building,
                        UpdateJobStatus.validating,
                    ]
                ),
            )
            .values(
                heartbeat_at=now,
                lease_expires_at=lease_expires_at,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.commit()
        return result.rowcount == 1

    async def save_checkpoint(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
        checkpoint: dict[str, Any],
        status: UpdateJobStatus,
        now: datetime,
    ) -> bool:
        statement = (
            update(UpdateJob)
            .where(
                UpdateJob.id == job_id,
                UpdateJob.worker_id == worker_id,
                UpdateJob.lease_token == lease_token,
                UpdateJob.fencing_token == fencing_token,
                UpdateJob.lease_expires_at > now,
                UpdateJob.status.in_(
                    [
                        UpdateJobStatus.claimed,
                        UpdateJobStatus.running,
                        UpdateJobStatus.building,
                        UpdateJobStatus.validating,
                    ]
                ),
            )
            .values(
                checkpoint=checkpoint,
                status=status,
                current_stage=str(checkpoint.get("stage") or status.value),
                heartbeat_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.commit()
        return result.rowcount == 1

    async def mark_succeeded_fenced(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
        result: dict[str, Any],
        now: datetime,
    ) -> bool:
        statement = (
            update(UpdateJob)
            .where(
                UpdateJob.id == job_id,
                UpdateJob.worker_id == worker_id,
                UpdateJob.lease_token == lease_token,
                UpdateJob.fencing_token == fencing_token,
                UpdateJob.lease_expires_at > now,
                UpdateJob.status.in_(
                    [
                        UpdateJobStatus.claimed,
                        UpdateJobStatus.running,
                        UpdateJobStatus.validating,
                    ]
                ),
            )
            .values(
                status=UpdateJobStatus.succeeded,
                current_stage="succeeded",
                result=result,
                finished_at=now,
                heartbeat_at=now,
                lease_expires_at=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        changed = await self._session.execute(statement)
        await self._session.commit()
        return changed.rowcount == 1

    async def mark_expired_for_recovery(self, *, now: datetime) -> list[str]:
        statement = (
            update(UpdateJob)
            .where(
                UpdateJob.status.in_(
                    [
                        UpdateJobStatus.claimed,
                        UpdateJobStatus.running,
                        UpdateJobStatus.building,
                        UpdateJobStatus.validating,
                    ]
                ),
                UpdateJob.lease_expires_at.is_not(None),
                UpdateJob.lease_expires_at <= now,
            )
            .values(
                status=UpdateJobStatus.recovery_required,
                current_stage="recovery_required",
                worker_id=None,
                lease_token=None,
                fencing_token=None,
                claimed_at=None,
                heartbeat_at=None,
                lease_expires_at=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
            .returning(UpdateJob.id)
        )
        result = await self._session.execute(statement)
        job_ids = list(result.scalars().all())
        await self._session.commit()
        return job_ids

    async def mark_building(self, job_id: str, candidate_generation_id: str) -> UpdateJob | None:
        return await self.update(
            job_id,
            status=UpdateJobStatus.building,
            candidate_generation_id=candidate_generation_id,
            started_at=datetime.now(tz=UTC),
            current_stage="building_candidate",
        )

    async def mark_ready(self, job_id: str, result: dict | None = None) -> UpdateJob | None:
        values: dict[str, Any] = {
            "status": UpdateJobStatus.ready,
            "current_stage": "candidate_ready",
        }
        if result is not None:
            values["result"] = result
        return await self.update(job_id, **values)

    async def mark_promoted(self, job_id: str, approved_by: str | None = None) -> UpdateJob | None:
        return await self.update(
            job_id,
            status=UpdateJobStatus.promoted,
            current_stage="promoted",
            approved_by=approved_by or "api",
            finished_at=datetime.now(tz=UTC),
        )

    async def mark_failed(
        self,
        job_id: str,
        *,
        error_code: str | None = None,
        sanitized_error_message: str | None = None,
    ) -> UpdateJob | None:
        return await self.update(
            job_id,
            status=UpdateJobStatus.failed,
            current_stage="failed",
            error_code=error_code,
            sanitized_error_message=sanitized_error_message,
            finished_at=datetime.now(tz=UTC),
        )

"""Persistence operations for incremental update jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
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
                        UpdateJobStatus.building,
                        UpdateJobStatus.validating,
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

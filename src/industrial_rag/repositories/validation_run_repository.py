"""Persistence operations for immutable canonical validation runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.models import ValidationRun, ValidationRunStatus


class ValidationRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **values: Any) -> ValidationRun:
        run = ValidationRun(status=ValidationRunStatus.running, **values)
        self._session.add(run)
        await self._session.flush()
        return run

    async def get(self, run_id: str) -> ValidationRun | None:
        return await self._session.get(ValidationRun, run_id)

    async def finalize(
        self,
        run_id: str,
        *,
        passed: bool,
        metrics: dict[str, Any],
        artifact_path: str,
        artifact_sha256: str,
        finished_at: datetime,
    ) -> ValidationRun | None:
        run = await self.get(run_id)
        if run is None or run.status is not ValidationRunStatus.running:
            return None
        run.status = ValidationRunStatus.passed if passed else ValidationRunStatus.failed
        run.passed = passed
        run.metrics = metrics
        run.result_artifact_path = artifact_path
        run.result_artifact_sha256 = artifact_sha256
        run.finished_at = finished_at
        await self._session.flush()
        return run

    async def latest_eligible(
        self,
        generation_id: str,
        *,
        golden_set_version: str,
        golden_set_sha256: str,
        now: datetime,
    ) -> ValidationRun | None:
        statement = (
            select(ValidationRun)
            .where(
                ValidationRun.generation_id == generation_id,
                ValidationRun.status == ValidationRunStatus.passed,
                ValidationRun.passed.is_(True),
                ValidationRun.golden_set_version == golden_set_version,
                ValidationRun.golden_set_sha256 == golden_set_sha256,
                ValidationRun.expires_at > now,
            )
            .order_by(ValidationRun.finished_at.desc())
            .limit(1)
        )
        result = await self._session.execute(statement)
        return result.scalars().first()


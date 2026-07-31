"""Repository layer: async DB operations for LifecycleTask."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.models import LifecycleTask, TaskStatus


class TaskRepository:
    """Async CRUD for LifecycleTask rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **values: Any) -> LifecycleTask:
        task = LifecycleTask(**values)
        self._session.add(task)
        await self._session.flush()
        return task

    async def get(self, task_id: str) -> LifecycleTask | None:
        return await self._session.get(LifecycleTask, task_id)

    async def list_by_kb(
        self,
        kb_id: str,
        *,
        status_filter: TaskStatus | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[LifecycleTask]:
        stmt = select(LifecycleTask).where(
            LifecycleTask.knowledge_base_id == kb_id
        )
        if status_filter is not None:
            stmt = stmt.where(LifecycleTask.status == status_filter)
        stmt = stmt.order_by(LifecycleTask.created_at.desc()).offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_doc(
        self,
        doc_id: str,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> list[LifecycleTask]:
        stmt = select(LifecycleTask).where(
            LifecycleTask.document_id == doc_id
        ).order_by(LifecycleTask.created_at.desc()).offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def find_pending(self, *, limit: int = 1) -> list[LifecycleTask]:
        stmt = select(LifecycleTask).where(
            LifecycleTask.status.in_([TaskStatus.pending, TaskStatus.retrying])
        ).order_by(LifecycleTask.created_at.asc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def find_active_backend_task(
        self,
        kb_id: str,
        task_type: str,
    ) -> LifecycleTask | None:
        stmt = select(LifecycleTask).where(
            LifecycleTask.knowledge_base_id == kb_id,
            LifecycleTask.task_type == task_type,
            LifecycleTask.status.in_([TaskStatus.pending, TaskStatus.running, TaskStatus.retrying]),
        ).order_by(LifecycleTask.created_at.asc())
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def update(self, task_id: str, **values: Any) -> LifecycleTask | None:
        task = await self.get(task_id)
        if task is None:
            return None
        for key, val in values.items():
            if hasattr(task, key):
                setattr(task, key, val)
        task.updated_at = datetime.now(tz=UTC)
        await self._session.flush()
        return task

    async def mark_running(self, task_id: str) -> LifecycleTask | None:
        task = await self.get(task_id)
        if task is None or task.status not in (TaskStatus.pending, TaskStatus.retrying):
            return None
        task.status = TaskStatus.running
        task.attempt += 1
        task.started_at = datetime.now(tz=UTC)
        task.updated_at = datetime.now(tz=UTC)
        await self._session.flush()
        return task

    async def mark_succeeded(self, task_id: str, result: dict | None = None) -> LifecycleTask | None:
        return await self.update(
            task_id,
            status=TaskStatus.succeeded,
            finished_at=datetime.now(tz=UTC),
            result=result,
            progress=1.0,
        )

    async def mark_failed(
        self, task_id: str, *, error_code: str | None = None, error_message: str | None = None
    ) -> LifecycleTask | None:
        return await self.update(
            task_id,
            status=TaskStatus.failed,
            finished_at=datetime.now(tz=UTC),
            error_code=error_code,
            error_message=error_message,
        )

    async def mark_retrying(self, task_id: str) -> LifecycleTask | None:
        task = await self.get(task_id)
        if task is None:
            return None
        task.status = TaskStatus.retrying
        task.updated_at = datetime.now(tz=UTC)
        await self._session.flush()
        return task

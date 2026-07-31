"""Task query API."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.session import get_session
from industrial_rag.repositories.task_repository import TaskRepository
from industrial_rag.routers.schemas import PaginatedResponse, TaskDetail

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


def _task_to_detail(task) -> TaskDetail:
    return TaskDetail(
        id=task.id,
        knowledge_base_id=task.knowledge_base_id,
        document_id=task.document_id,
        task_type=task.task_type.value if hasattr(task.task_type, "value") else str(task.task_type),
        status=task.status.value if hasattr(task.status, "value") else str(task.status),
        progress=task.progress,
        current_stage=task.current_stage,
        attempt=task.attempt,
        created_at=task.created_at.isoformat() if task.created_at else None,
        started_at=task.started_at.isoformat() if task.started_at else None,
        finished_at=task.finished_at.isoformat() if task.finished_at else None,
        error_code=task.error_code,
        error_message=task.error_message,
        payload=task.payload,
        result=task.result,
        cleanup_steps=task.cleanup_steps,
    )


@router.get("/{task_id}", response_model=TaskDetail)
async def get_task(
    task_id: str,
    session: AsyncSession = Depends(get_session),
) -> TaskDetail:
    repo = TaskRepository(session)
    task = await repo.get(task_id)
    if task is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="任务不存在")
    return _task_to_detail(task)


@router.get("", response_model=PaginatedResponse)
async def list_tasks_by_kb(
    knowledge_base_id: str = Query(..., alias="kb_id"),
    status: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict:
    from industrial_rag.db.models import TaskStatus

    repo = TaskRepository(session)
    status_filter = TaskStatus(status) if status else None
    tasks = await repo.list_by_kb(
        knowledge_base_id, status_filter=status_filter, offset=offset, limit=limit
    )
    return {
        "items": [_task_to_detail(t) for t in tasks],
        "total": len(tasks),
        "offset": offset,
        "limit": limit,
    }

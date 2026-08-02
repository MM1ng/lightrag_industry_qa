"""Incremental update job query API (Phase 9)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.auth import AuthenticatedActor, require_admin_actor
from industrial_rag.db.session import get_session
from industrial_rag.routers.schemas import PaginatedResponse, UpdateJobSummary
from industrial_rag.services.incremental_update_service import IncrementalUpdateService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/knowledge-bases/{kb_id}/update-jobs", tags=["update-jobs"]
)


@router.get("", response_model=PaginatedResponse)
async def list_update_jobs(
    kb_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict:
    svc = IncrementalUpdateService(session)
    jobs = await svc.list_jobs(kb_id)
    items = [UpdateJobSummary(**j) for j in jobs]
    return {"items": items, "total": len(items), "offset": offset, "limit": limit}


@router.get("/{job_id}", response_model=UpdateJobSummary)
async def get_update_job(
    kb_id: str,
    job_id: str,
    session: AsyncSession = Depends(get_session),
) -> UpdateJobSummary:
    svc = IncrementalUpdateService(session)
    return UpdateJobSummary(**await svc.get_job(kb_id, job_id))


@router.post("/{job_id}/resume")
async def resume_update_job(
    kb_id: str,
    job_id: str,
    actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await IncrementalUpdateService(session).resume_job(
        kb_id,
        job_id,
        actor=actor.actor,
    )


@router.post("/{job_id}/cancel")
async def cancel_update_job(
    kb_id: str,
    job_id: str,
    actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await IncrementalUpdateService(session).cancel_job(
        kb_id,
        job_id,
        actor=actor.actor,
    )

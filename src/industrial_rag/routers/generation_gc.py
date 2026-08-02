"""Admin-only two-stage Generation GC API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.auth import AuthenticatedActor, require_admin_actor
from industrial_rag.db.session import get_session
from industrial_rag.services.generation_gc_service import GenerationGCService

router = APIRouter(prefix="/v1/knowledge-bases/{kb_id}/gc", tags=["generation-gc"])


class GCPlanRequest(BaseModel):
    failed_retention_days: int = Field(default=7, ge=0, le=3650)
    archived_keep_count: int = Field(default=2, ge=0, le=100)
    plan_ttl_minutes: int = Field(default=30, ge=1, le=1440)


class GCExecuteRequest(BaseModel):
    manifest_hash: str = Field(min_length=64, max_length=64)


@router.post("/plans")
async def plan_gc(
    kb_id: str,
    payload: GCPlanRequest,
    actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await GenerationGCService(session).plan(
        kb_id,
        actor=actor.actor,
        failed_retention_days=payload.failed_retention_days,
        archived_keep_count=payload.archived_keep_count,
        plan_ttl_minutes=payload.plan_ttl_minutes,
    )


@router.post("/plans/{plan_id}/execute")
async def execute_gc(
    kb_id: str,
    plan_id: str,
    payload: GCExecuteRequest,
    actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await GenerationGCService(session).execute(
        kb_id,
        plan_id,
        manifest_hash=payload.manifest_hash,
        actor=actor.actor,
    )

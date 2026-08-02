"""Generation lifecycle API (Phase 9)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.session import get_session
from industrial_rag.routers.schemas import (
    GenerationActionResponse,
    GenerationSummary,
    GenerationValidateResponse,
)
from industrial_rag.services.incremental_update_service import IncrementalUpdateService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/knowledge-bases/{kb_id}/generations", tags=["generations"]
)


def _summary(g: dict) -> GenerationSummary:
    return GenerationSummary(**g)


@router.get("", response_model=list[GenerationSummary])
async def list_generations(
    kb_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[GenerationSummary]:
    svc = IncrementalUpdateService(session)
    return [_summary(g) for g in await svc.list_generations(kb_id)]


@router.get("/{generation_id}", response_model=GenerationSummary)
async def get_generation(
    kb_id: str,
    generation_id: str,
    session: AsyncSession = Depends(get_session),
) -> GenerationSummary:
    svc = IncrementalUpdateService(session)
    return _summary(await svc.get_generation(kb_id, generation_id))


@router.post("/{generation_id}/validate", response_model=GenerationValidateResponse)
async def validate_generation(
    kb_id: str,
    generation_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> GenerationValidateResponse:
    svc = IncrementalUpdateService(session)
    result = await svc.validate_generation(
        kb_id,
        generation_id,
        approved_by=request.headers.get("x-approved-by"),
    )
    return GenerationValidateResponse(**result)


@router.post("/{generation_id}/promote", response_model=GenerationActionResponse)
async def promote_generation(
    kb_id: str,
    generation_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> GenerationActionResponse:
    svc = IncrementalUpdateService(session)
    result = await svc.promote_generation(
        kb_id,
        generation_id,
        approved_by=request.headers.get("x-approved-by"),
    )
    runtime_manager = getattr(request.app.state, "runtime_manager", None)
    if runtime_manager is not None:
        await runtime_manager.evict_runtime(kb_id)
    return GenerationActionResponse(**result)


@router.post("/{generation_id}/rollback", response_model=GenerationActionResponse)
async def rollback_generation(
    kb_id: str,
    generation_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> GenerationActionResponse:
    svc = IncrementalUpdateService(session)
    result = await svc.rollback_generation(
        kb_id,
        generation_id,
        approved_by=request.headers.get("x-approved-by"),
    )
    runtime_manager = getattr(request.app.state, "runtime_manager", None)
    if runtime_manager is not None:
        await runtime_manager.evict_runtime(kb_id)
    return GenerationActionResponse(**result)


@router.get("/{generation_id}/diff")
async def generation_diff(
    kb_id: str,
    generation_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    svc = IncrementalUpdateService(session)
    return await svc.generation_diff(kb_id, generation_id)

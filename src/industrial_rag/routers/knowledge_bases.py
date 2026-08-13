"""Knowledge Base CRUD API."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.auth import AuthenticatedActor, require_admin_actor
from industrial_rag.db.session import get_session
from industrial_rag.routers.schemas import (
    DeleteTaskResponse,
    ErrorDetail,
    KnowledgeBaseCreate,
    KnowledgeBaseDetail,
    KnowledgeBaseSummary,
    KnowledgeBaseUpdate,
    PaginatedResponse,
    VectorBackendTaskResponse,
    VectorBackendUpdateRequest,
)
from industrial_rag.services.knowledge_base_service import KnowledgeBaseService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/knowledge-bases", tags=["knowledge-bases"])


def _kb_to_summary(kb) -> KnowledgeBaseSummary:
    return KnowledgeBaseSummary(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        status=kb.status.value if hasattr(kb.status, "value") else str(kb.status),
        document_count=kb.document_count,
        active_document_count=kb.active_document_count,
        chunk_count=kb.chunk_count,
        created_at=kb.created_at.isoformat() if kb.created_at else None,
        updated_at=kb.updated_at.isoformat() if kb.updated_at else None,
    )


def _kb_to_detail(kb) -> KnowledgeBaseDetail:
    return KnowledgeBaseDetail(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        status=kb.status.value if hasattr(kb.status, "value") else str(kb.status),
        parser_name=kb.parser_name,
        parser_version=kb.parser_version,
        chunking_strategy=kb.chunking_strategy,
        chunking_version=kb.chunking_version,
        chunking_config=kb.chunking_config,
        embedding_model=kb.embedding_model,
        embedding_dimension=kb.embedding_dimension,
        vector_backend=kb.vector_backend,
        active_vector_generation=(
            kb.active_vector_generation.generation
            if kb.active_vector_generation is not None
            else None
        ),
        document_count=kb.document_count,
        active_document_count=kb.active_document_count,
        chunk_count=kb.chunk_count,
        entity_count=kb.entity_count,
        relation_count=kb.relation_count,
        created_at=kb.created_at.isoformat() if kb.created_at else None,
        updated_at=kb.updated_at.isoformat() if kb.updated_at else None,
        deleted_at=kb.deleted_at.isoformat() if kb.deleted_at else None,
        last_error=kb.last_error,
    )


@router.post("", response_model=KnowledgeBaseDetail, status_code=201)
async def create_knowledge_base(
    body: KnowledgeBaseCreate,
    _actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeBaseDetail:
    svc = KnowledgeBaseService(session)
    kb = await svc.create(name=body.name, description=body.description)
    return _kb_to_detail(kb)


@router.get("", response_model=PaginatedResponse)
async def list_knowledge_bases(
    include_deleted: bool = Query(False),
    status: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict:
    svc = KnowledgeBaseService(session)
    kbs, total = await svc.list_all(
        include_deleted=include_deleted,
        status_filter=status,
        offset=offset,
        limit=limit,
    )
    return {
        "items": [_kb_to_summary(k) for k in kbs],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/{kb_id}", response_model=KnowledgeBaseDetail)
async def get_knowledge_base(
    kb_id: str,
    _actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeBaseDetail:
    svc = KnowledgeBaseService(session)
    kb = await svc.get(kb_id)
    return _kb_to_detail(kb)


@router.patch("/{kb_id}", response_model=KnowledgeBaseDetail)
async def update_knowledge_base(
    kb_id: str,
    body: KnowledgeBaseUpdate,
    _actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeBaseDetail:
    svc = KnowledgeBaseService(session)
    kb = await svc.update(kb_id, name=body.name, description=body.description)
    return _kb_to_detail(kb)


@router.post(
    "/{kb_id}/vector-backend",
    status_code=202,
    response_model=VectorBackendTaskResponse,
)
async def request_vector_backend_change(
    kb_id: str,
    body: VectorBackendUpdateRequest,
    _actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> VectorBackendTaskResponse:
    svc = KnowledgeBaseService(session)
    return VectorBackendTaskResponse(
        **await svc.request_vector_backend_change(
            kb_id,
            target_backend=body.target_backend,
        )
    )

@router.delete("/{kb_id}", status_code=202, response_model=DeleteTaskResponse)
async def delete_knowledge_base(
    kb_id: str,
    _actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> DeleteTaskResponse:
    svc = KnowledgeBaseService(session)
    result = await svc.request_delete(kb_id)
    return DeleteTaskResponse(**result)


async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    from industrial_rag.errors import AppError

    if isinstance(exc, AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorDetail(
                code=exc.code,
                message=exc.message,
                request_id=getattr(request.state, "request_id", ""),
                details=exc.details,
            ).model_dump(),
        )
    # Fallthrough to FastAPI default handler
    raise exc

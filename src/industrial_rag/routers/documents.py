"""Document lifecycle API."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.session import get_session
from industrial_rag.routers.schemas import (
    DocumentSummary,
    DocumentTaskResponse,
    PaginatedResponse,
)
from industrial_rag.services.document_service import DocumentService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/knowledge-bases/{kb_id}/documents", tags=["documents"]
)


def _doc_to_summary(doc) -> DocumentSummary:
    return DocumentSummary(
        id=doc.id,
        knowledge_base_id=doc.knowledge_base_id,
        original_file_name=doc.original_file_name,
        file_hash=doc.file_hash,
        file_size=doc.file_size,
        version=doc.version,
        status=doc.status.value if hasattr(doc.status, "value") else str(doc.status),
        parse_status=doc.parse_status,
        index_status=doc.index_status,
        page_count=doc.page_count,
        parent_chunk_count=doc.parent_chunk_count,
        child_chunk_count=doc.child_chunk_count,
        created_at=doc.created_at.isoformat() if doc.created_at else None,
        updated_at=doc.updated_at.isoformat() if doc.updated_at else None,
        last_error=doc.last_error,
    )


@router.post("", status_code=202, response_model=DocumentSummary)
async def upload_document(
    kb_id: str,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
) -> DocumentSummary:
    if file.filename is None:
        raise ValueError("文件名不能为空")
    content = await file.read()
    svc = DocumentService(session)
    doc = await svc.upload(
        kb_id,
        original_file_name=file.filename,
        content=content,
        mime_type=file.content_type or "application/pdf",
    )
    return _doc_to_summary(doc)


@router.get("", response_model=PaginatedResponse)
async def list_documents(
    kb_id: str,
    include_deleted: bool = Query(False),
    status: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict:
    svc = DocumentService(session)
    docs, total = await svc.list_by_kb(
        kb_id,
        include_deleted=include_deleted,
        status_filter=status,
        offset=offset,
        limit=limit,
    )
    return {
        "items": [_doc_to_summary(d) for d in docs],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/{doc_id}", response_model=DocumentSummary)
async def get_document(
    kb_id: str,
    doc_id: str,
    session: AsyncSession = Depends(get_session),
) -> DocumentSummary:
    svc = DocumentService(session)
    doc = await svc.get(kb_id, doc_id)
    return _doc_to_summary(doc)


@router.post("/{doc_id}/reparse", status_code=202, response_model=DocumentTaskResponse)
async def reparse_document(
    kb_id: str,
    doc_id: str,
    session: AsyncSession = Depends(get_session),
) -> DocumentTaskResponse:
    svc = DocumentService(session)
    result = await svc.request_reparse(kb_id, doc_id)
    return DocumentTaskResponse(**result)


@router.post("/{doc_id}/reindex", status_code=202, response_model=DocumentTaskResponse)
async def reindex_document(
    kb_id: str,
    doc_id: str,
    session: AsyncSession = Depends(get_session),
) -> DocumentTaskResponse:
    svc = DocumentService(session)
    result = await svc.request_reindex(kb_id, doc_id)
    return DocumentTaskResponse(**result)


@router.delete("/{doc_id}", status_code=202, response_model=DocumentTaskResponse)
async def delete_document(
    kb_id: str,
    doc_id: str,
    session: AsyncSession = Depends(get_session),
) -> DocumentTaskResponse:
    svc = DocumentService(session)
    result = await svc.request_delete(kb_id, doc_id)
    return DocumentTaskResponse(**result)

"""Document lifecycle API."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.auth import AuthenticatedActor, require_admin_actor
from industrial_rag.db.session import get_session
from industrial_rag.db.models import VectorIndexGenerationStatus
from industrial_rag.errors import AppError
from industrial_rag.repositories.vector_index_generation_repository import (
    VectorIndexGenerationRepository,
)
from industrial_rag.repositories.knowledge_base_repository import KnowledgeBaseRepository
from industrial_rag.routers.schemas import (
    DocumentSourceResponse,
    DocumentSummary,
    DocumentTaskResponse,
    DocumentUpdateResponse,
    PaginatedResponse,
)
from industrial_rag.services.document_service import DocumentService
from industrial_rag.services.incremental_update_service import IncrementalUpdateService

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


def _source_error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code,
            "message": message,
            "retryable": status_code >= 500,
        },
    )


async def _require_source_document(
    kb_id: str,
    doc_id: str,
    session: AsyncSession,
    *,
    generation_id: str | None = None,
):
    svc = DocumentService(session)
    try:
        doc = await svc.get(kb_id, doc_id)
    except AppError:
        return _source_error("SOURCE_DOCUMENT_NOT_FOUND", "原始文档不存在或已不可用。", 404)
    if generation_id:
        generation = await VectorIndexGenerationRepository(session).get(generation_id)
        kb = await KnowledgeBaseRepository(session).get(kb_id)
        if (
            generation is None
            or generation.knowledge_base_id != kb_id
            or kb is None
            or kb.active_vector_generation_id != generation_id
        ):
            return _source_error("SOURCE_FORBIDDEN", "当前引用不属于这个知识库版本。", 403)
        if generation.status in {
            VectorIndexGenerationStatus.building,
            VectorIndexGenerationStatus.failed,
            VectorIndexGenerationStatus.deleted,
        }:
            return _source_error("SOURCE_FORBIDDEN", "当前知识库版本不可用于原文核验。", 403)
    return doc


def _validate_page(doc, page: int) -> JSONResponse | None:
    if page < 1:
        return _source_error("SOURCE_PAGE_INVALID", "页码不存在，请检查引用页码。", 422)
    page_count = doc.page_count
    if page_count is None:
        path = Path(doc.file_path)
        try:
            import fitz  # type: ignore[import-not-found]

            with fitz.open(path) as pdf:
                page_count = pdf.page_count
        except Exception:
            return _source_error(
                "SOURCE_DOCUMENT_UNAVAILABLE", "当前无法读取原文，请稍后重试。", 404
            )
    if page > page_count:
        return _source_error("SOURCE_PAGE_NOT_FOUND", "页码不存在，请检查引用页码。", 404)
    return None


def _page_context(doc, page: int) -> str:
    path = Path(doc.file_path)
    if not path.exists():
        return ""
    try:
        import fitz  # type: ignore[import-not-found]

        with fitz.open(path) as pdf:
            if page > pdf.page_count:
                return ""
            text = pdf.load_page(page - 1).get_text("text").strip()
            return " ".join(text.split())[:1200]
    except Exception:
        return ""


@router.post("", status_code=202, response_model=DocumentUpdateResponse)
async def upload_document(
    kb_id: str,
    file: UploadFile,
    request: Request,
    actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> DocumentUpdateResponse:
    if file.filename is None:
        raise ValueError("文件名不能为空")
    content = await file.read()
    svc = IncrementalUpdateService(session)
    result = await svc.add_document(
        kb_id,
        original_file_name=file.filename,
        content=content,
        mime_type=file.content_type or "application/pdf",
        request_id=getattr(request.state, "request_id", None),
        trace_id=getattr(request.state, "trace_id", None),
        created_by=actor.actor,
    )
    result["operation"] = "add"
    return DocumentUpdateResponse(**result)


@router.put("/{doc_id}", status_code=202, response_model=DocumentUpdateResponse)
async def replace_document(
    kb_id: str,
    doc_id: str,
    file: UploadFile,
    request: Request,
    actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> DocumentUpdateResponse:
    content = await file.read()
    svc = IncrementalUpdateService(session)
    result = await svc.replace_document(
        kb_id,
        doc_id,
        content=content,
        original_file_name=file.filename,
        mime_type=file.content_type or "application/pdf",
        request_id=getattr(request.state, "request_id", None),
        trace_id=getattr(request.state, "trace_id", None),
        created_by=actor.actor,
    )
    result["operation"] = "replace"
    return DocumentUpdateResponse(**result)


@router.get("", response_model=PaginatedResponse)
async def list_documents(
    kb_id: str,
    _actor: AuthenticatedActor = Depends(require_admin_actor),
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
    _actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> DocumentSummary:
    svc = DocumentService(session)
    doc = await svc.get(kb_id, doc_id)
    return _doc_to_summary(doc)


@router.get("/{doc_id}/source", response_model=DocumentSourceResponse)
async def get_document_source(
    kb_id: str,
    doc_id: str,
    request: Request,
    page: int = Query(..., ge=1),
    generation_id: str | None = Query(None),
    evidence_id: str | None = Query(None),
    excerpt: str = Query("", max_length=600),
    session: AsyncSession = Depends(get_session),
) -> DocumentSourceResponse | JSONResponse:
    doc_or_error = await _require_source_document(
        kb_id, doc_id, session, generation_id=generation_id
    )
    if isinstance(doc_or_error, JSONResponse):
        return doc_or_error
    doc = doc_or_error
    page_error = _validate_page(doc, page)
    if page_error is not None:
        return page_error
    file_path = Path(doc.file_path)
    source_available = file_path.exists() and file_path.is_file()
    source_url = None
    if source_available:
        source_url = str(
            request.url_for("get_document_source_file", kb_id=kb_id, doc_id=doc_id)
        )
        source_url = (
            f"{source_url}?page={page}"
            f"{'&generation_id=' + generation_id if generation_id else ''}"
            f"{'&evidence_id=' + evidence_id if evidence_id else ''}"
        )
    return DocumentSourceResponse(
        document_id=doc.id,
        document_name=doc.original_file_name,
        knowledge_base_id=kb_id,
        generation_id=generation_id,
        document_version=doc.version,
        page=page,
        page_count=doc.page_count,
        excerpt=excerpt,
        page_context=_page_context(doc, page) if source_available else "",
        source_available=source_available,
        source_url=source_url,
        unavailable_reason=None if source_available else "当前无法打开原文，已保留引用摘录供核验。",
    )


@router.get("/{doc_id}/source-file", name="get_document_source_file", response_model=None)
async def get_document_source_file(
    kb_id: str,
    doc_id: str,
    page: int = Query(..., ge=1),
    generation_id: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> FileResponse | JSONResponse:
    doc_or_error = await _require_source_document(
        kb_id, doc_id, session, generation_id=generation_id
    )
    if isinstance(doc_or_error, JSONResponse):
        return doc_or_error
    doc = doc_or_error
    page_error = _validate_page(doc, page)
    if page_error is not None:
        return page_error
    file_path = Path(doc.file_path)
    if not file_path.exists() or not file_path.is_file():
        return _source_error("SOURCE_DOCUMENT_UNAVAILABLE", "当前无法打开原文，已保留引用摘录供核验。", 404)
    return FileResponse(
        file_path,
        media_type=doc.mime_type or "application/pdf",
        filename=doc.original_file_name,
    )


@router.post("/{doc_id}/reparse", status_code=202, response_model=DocumentTaskResponse)
async def reparse_document(
    kb_id: str,
    doc_id: str,
    _actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> DocumentTaskResponse:
    svc = DocumentService(session)
    result = await svc.request_reparse(kb_id, doc_id)
    return DocumentTaskResponse(**result)


@router.post("/{doc_id}/reindex", status_code=202, response_model=DocumentTaskResponse)
async def reindex_document(
    kb_id: str,
    doc_id: str,
    _actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> DocumentTaskResponse:
    svc = DocumentService(session)
    result = await svc.request_reindex(kb_id, doc_id)
    return DocumentTaskResponse(**result)


@router.delete("/{doc_id}", status_code=202, response_model=DocumentUpdateResponse)
async def delete_document(
    kb_id: str,
    doc_id: str,
    request: Request,
    actor: AuthenticatedActor = Depends(require_admin_actor),
    session: AsyncSession = Depends(get_session),
) -> DocumentUpdateResponse:
    svc = IncrementalUpdateService(session)
    result = await svc.delete_document(
        kb_id,
        doc_id,
        request_id=getattr(request.state, "request_id", None),
        trace_id=getattr(request.state, "trace_id", None),
        created_by=actor.actor,
    )
    result["operation"] = "delete"
    return DocumentUpdateResponse(**result)

"""Admin-only, read-only request diagnostics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from industrial_rag.auth import AuthenticatedActor, require_admin_actor
from industrial_rag.errors import AppError, AppErrorCode
from industrial_rag.services.retrieval_trace_service import RetrievalTraceService

router = APIRouter(prefix="/v1/admin/diagnostics", tags=["admin-diagnostics"])


class RetrievalResultResponse(BaseModel):
    initial_rank: int
    initial_score: float | None
    retrieval_source: str
    document_id: str | None
    document_name: str
    page_number: int
    chunk_id: str
    section_path: list[str]
    matched_terms: list[str]
    reranked_rank: int | None
    reranked_score: float | None
    used_for_answer: bool
    cited_in_answer: bool


class SelectedEvidenceResponse(BaseModel):
    final_rank: int
    chunk_id: str
    document_id: str | None
    document_name: str
    page_number: int
    initial_rank: int | None
    reranked_rank: int | None
    used_for_answer: bool
    cited_in_answer: bool


class RetrievalTraceResponse(BaseModel):
    request_id: str
    trace_id: str
    trace_version: str
    knowledge_base_id: str
    generation_id: str
    generation_epoch: int
    original_query: str
    normalized_query: str
    retrieval_config: dict[str, Any]
    initial_results: list[RetrievalResultResponse]
    rerank_applied: bool
    reranked_results: list[RetrievalResultResponse]
    final_selected_chunks: list[SelectedEvidenceResponse]
    normalization_ms: float
    retrieval_ms: float
    rerank_ms: float
    evidence_selection_ms: float
    end_to_end_ms: float
    created_at: str
    expires_at: str


@router.get(
    "/requests/{request_id}/retrieval-trace",
    response_model=RetrievalTraceResponse,
)
async def get_retrieval_trace(
    request_id: str,
    request: Request,
    _actor: AuthenticatedActor = Depends(require_admin_actor),
) -> RetrievalTraceResponse:
    settings = getattr(request.app.state, "resolved_settings", None)
    if settings is None:
        raise AppError(AppErrorCode.index_not_ready, "知识库尚未就绪。")
    payload = await RetrievalTraceService(settings=settings).get_unexpired(request_id)
    if payload is None:
        raise AppError(
            AppErrorCode.retrieval_trace_not_found,
            "检索追踪记录不存在或已过期。",
            status_code=404,
        )
    return RetrievalTraceResponse.model_validate(payload)

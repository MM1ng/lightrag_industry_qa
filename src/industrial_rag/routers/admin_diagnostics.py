"""Admin-only, read-only request diagnostics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

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
    detected_model: str | None = None
    detected_component: str | None = None
    detected_parameter: str | None = None
    added_aliases: list[str] = Field(default_factory=list)
    answer_plan: list[dict[str, Any]] = Field(default_factory=list)
    grounding_audit: dict[str, Any] | None = None
    grounding_audit_version: str | None = None
    replay_eligible: bool = False
    completion_applied: bool = False
    completion_candidates: list[dict[str, Any]] = Field(default_factory=list)
    completed_evidence: list[dict[str, Any]] = Field(default_factory=list)
    coverage_requirements: list[str] = Field(default_factory=list)
    coverage_status: str = "uncovered"
    feature_flags: dict[str, Any] = Field(default_factory=dict)
    supplemental_query_text: str | None = None
    supplemental_query_sha256: str | None = None
    supplemental_query_different_from_normalized: bool = False
    supplemental_retrieval_triggered: bool = False
    supplemental_candidates: list[dict[str, Any]] = Field(default_factory=list)
    supplemental_accepted: list[dict[str, Any]] = Field(default_factory=list)
    supplemental_rejected: list[dict[str, Any]] = Field(default_factory=list)
    original_query_sha256: str | None = None
    normalized_query_sha256: str | None = None
    provider_evidence_ids: list[str] = Field(default_factory=list)
    provider_primary_evidence_ids: list[str] = Field(default_factory=list)
    provider_completed_evidence_ids: list[str] = Field(default_factory=list)
    provider_supplemental_evidence_ids: list[str] = Field(default_factory=list)
    provider_context_order: list[str] = Field(default_factory=list)
    provider_context_sha256: str | None = None
    provider_evidence_count: int = 0
    provider_context_truncated: bool = False
    provider_context_token_estimate: int | None = None
    backend_second_query_called: bool = False
    coverage_before: list[str] = Field(default_factory=list)
    coverage_after_parent_adjacent: list[str] = Field(default_factory=list)
    selected_coverage: list[str] = Field(default_factory=list)
    generated_coverage: list[str] = Field(default_factory=list)
    grounding_retained_coverage: list[str] = Field(default_factory=list)
    grounding_answer_point_identity: list[str] = Field(default_factory=list)
    grounding_support_candidate_ids: list[dict[str, Any]] = Field(default_factory=list)
    grounding_retained_answer_points: list[str] = Field(default_factory=list)
    grounding_removed_answer_points: list[str] = Field(default_factory=list)
    grounding_removal_reasons: list[dict[str, Any]] = Field(default_factory=list)
    grounding_false_negative_diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    generated_answer_points: list[str] = Field(default_factory=list)
    rejected_answer_points: list[str] = Field(default_factory=list)
    support_validation_reason_codes: list[str] = Field(default_factory=list)
    final_answer_point_ids: list[str] = Field(default_factory=list)
    unresolved_requirement_ids: list[str] = Field(default_factory=list)
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

    def model_post_init(self, __context: Any) -> None:
        audit = self.grounding_audit or {}
        object.__setattr__(self, "grounding_audit_version", audit.get("audit_version"))
        object.__setattr__(self, "replay_eligible", bool(audit.get("replay_eligible", False)))


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

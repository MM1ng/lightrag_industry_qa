"""Shared Active and explicit-Generation query application boundary."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.config import Settings
from industrial_rag.conversation.query_rewriter import QueryRewriter
from industrial_rag.db.models import KBStatus, VectorIndexGenerationStatus
from industrial_rag.errors import AppError, AppErrorCode
from industrial_rag.kb_runtime_settings import settings_for_knowledge_base
from industrial_rag.lightrag_service import QueryResult
from industrial_rag.operational_metrics import operational_metrics
from industrial_rag.repositories.document_repository import DocumentRepository
from industrial_rag.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from industrial_rag.repositories.update_job_repository import UpdateJobRepository
from industrial_rag.repositories.vector_index_generation_repository import (
    VectorIndexGenerationRepository,
)
from industrial_rag.safety_policy import evaluate_input
from industrial_rag.vector_collections import VectorBackend


@dataclass(frozen=True, slots=True)
class GenerationQueryResult:
    generation_id: str
    generation_name: str
    generation_epoch: int
    result: QueryResult
    citation_document_ids: dict[str, str]


class QueryApplicationService:
    """Resolve trusted generation metadata before consulting process-local cache."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        base_settings: Settings,
        runtime_manager,
        query_rewriter: QueryRewriter | None = None,
    ) -> None:
        self._session = session
        self._base_settings = base_settings
        self._runtime_manager = runtime_manager
        self._query_rewriter = query_rewriter or QueryRewriter()
        self._kb_repository = KnowledgeBaseRepository(session)
        self._generation_repository = VectorIndexGenerationRepository(session)
        self._document_repository = DocumentRepository(session)
        self._job_repository = UpdateJobRepository(session)

    async def query_active(
        self,
        kb_id: str,
        question: str,
        *,
        history: list[dict[str, str]] | None = None,
    ) -> GenerationQueryResult:
        kb = await self._require_kb(kb_id)
        if kb.active_vector_generation_id is None:
            raise AppError(AppErrorCode.index_not_ready, "知识库没有 Active Generation")
        return await self._query(kb, kb.active_vector_generation_id, question, history=history)

    async def query_generation(
        self,
        kb_id: str,
        generation_id: str,
        question: str,
        *,
        history: list[dict[str, str]] | None = None,
        disable_llm_cache: bool = False,
    ) -> GenerationQueryResult:
        kb = await self._require_kb(kb_id)
        return await self._query(
            kb,
            generation_id,
            question,
            history=history,
            disable_llm_cache=disable_llm_cache,
        )

    async def _query(
        self,
        kb,
        generation_id: str,
        question: str,
        *,
        history: list[dict[str, str]] | None = None,
        disable_llm_cache: bool = False,
    ) -> GenerationQueryResult:
        generation = await self._generation_repository.get(generation_id)
        if generation is None or generation.knowledge_base_id != kb.id:
            raise AppError(
                AppErrorCode.generation_not_found,
                "Generation 不存在。",
                status_code=404,
            )
        if generation.status in {
            VectorIndexGenerationStatus.building,
            VectorIndexGenerationStatus.failed,
            VectorIndexGenerationStatus.deleted,
        }:
            raise AppError(
                AppErrorCode.generation_invalid_state,
                "Generation 当前不可查询。",
                status_code=409,
            )
        rewrite = await self._query_rewriter.rewrite(question, history)
        rewrite_details = rewrite.to_trace()
        if rewrite.status == "ambiguous":
            raise AppError(
                AppErrorCode.query_rewrite_ambiguous,
                "当前问题存在多个可能的指代对象，请明确设备或对象后重试。",
                status_code=422,
                details=rewrite_details,
            )
        retrieval_question = rewrite.standalone_query or question
        if rewrite.status == "failed" and rewrite.history_dependent:
            raise AppError(
                AppErrorCode.query_rewrite_failed,
                "当前问题依赖会话上下文，但无法安全改写，请补充明确的设备或对象。",
                status_code=422,
                details=rewrite_details,
            )
        safety = evaluate_input(retrieval_question)
        if not safety.allowed:
            raise AppError(
                "SAFETY_POLICY_BLOCKED",
                "改写后的查询未通过安全策略。",
                status_code=403,
                details={
                    **rewrite_details,
                    "safety_policy_id": safety.policy_id,
                    "safety_matched_rule": safety.matched_rule,
                },
            )
        settings = settings_for_knowledge_base(
            self._base_settings,
            kb,
            backend=VectorBackend(generation.backend),
            generation=generation.generation,
            working_dir=Path(generation.workspace_path),
        )
        if disable_llm_cache:
            settings = replace(settings, enable_llm_cache=False)
        runtime = await self._runtime_manager.get_runtime(kb.id, settings)
        operational_metrics.set(f"active_generation.{kb.id}", generation.id)
        try:
            result = await runtime.query(
                retrieval_question,
                mode=self._base_settings.phase10b_query_mode,  # type: ignore[arg-type]
                top_k=self._base_settings.phase10b_top_k,
                chunk_top_k=self._base_settings.phase10b_chunk_top_k,
            )
        except TypeError as error:
            if "unexpected keyword argument" not in str(error):
                raise
            result = await runtime.query(
                retrieval_question,
                mode=self._base_settings.phase10b_query_mode,  # type: ignore[arg-type]
            )
        document_ids = await self._citation_document_ids(kb.id, generation)
        if result.retrieval_trace is not None:
            normalized_retrieval_query = result.retrieval_trace.normalized_query
            trace = result.retrieval_trace.with_query_rewrite(
                rewrite,
                retrieval_query=normalized_retrieval_query,
            )
            result = replace(
                result,
                retrieval_trace=trace.with_document_ids(document_ids),
            )
        return GenerationQueryResult(
            generation_id=generation.id,
            generation_name=generation.generation,
            generation_epoch=int(kb.generation_epoch or 0),
            result=result,
            citation_document_ids=document_ids,
        )

    async def _citation_document_ids(self, kb_id: str, generation) -> dict[str, str]:
        mapping: dict[str, str] = {}
        job = await self._job_repository.find_by_candidate(generation.id)
        snapshot = (job.result or {}).get("documents", []) if job is not None else []
        for entry in snapshot:
            if entry.get("is_active") and entry.get("logical_name") and entry.get("document_id"):
                mapping[str(entry["logical_name"])] = str(entry["document_id"])
        docs = await self._document_repository.list_by_kb(kb_id, include_deleted=True)
        for doc in sorted(docs, key=lambda item: (item.version, item.created_at)):
            if snapshot and doc.id not in {str(item.get("document_id")) for item in snapshot if item.get("is_active")}:
                continue
            mapping[doc.original_file_name] = doc.id
            if doc.logical_name:
                mapping[doc.logical_name] = doc.id
        return mapping

    async def _require_kb(self, kb_id: str):
        kb = await self._kb_repository.get(kb_id)
        if kb is None or kb.status in {KBStatus.deleting, KBStatus.deleted}:
            raise AppError(
                AppErrorCode.knowledge_base_not_found,
                "知识库不存在。",
                status_code=404,
            )
        return kb

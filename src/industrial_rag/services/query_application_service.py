"""Shared Active and explicit-Generation query application boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.config import Settings
from industrial_rag.db.models import KBStatus, VectorIndexGenerationStatus
from industrial_rag.errors import AppError, AppErrorCode
from industrial_rag.kb_runtime_settings import settings_for_knowledge_base
from industrial_rag.lightrag_service import QueryResult
from industrial_rag.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from industrial_rag.repositories.vector_index_generation_repository import (
    VectorIndexGenerationRepository,
)
from industrial_rag.vector_collections import VectorBackend


@dataclass(frozen=True, slots=True)
class GenerationQueryResult:
    generation_id: str
    generation_name: str
    generation_epoch: int
    result: QueryResult


class QueryApplicationService:
    """Resolve trusted generation metadata before consulting process-local cache."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        base_settings: Settings,
        runtime_manager,
    ) -> None:
        self._session = session
        self._base_settings = base_settings
        self._runtime_manager = runtime_manager
        self._kb_repository = KnowledgeBaseRepository(session)
        self._generation_repository = VectorIndexGenerationRepository(session)

    async def query_active(self, kb_id: str, question: str) -> GenerationQueryResult:
        kb = await self._require_kb(kb_id)
        if kb.active_vector_generation_id is None:
            raise AppError(AppErrorCode.index_not_ready, "知识库没有 Active Generation")
        return await self._query(kb, kb.active_vector_generation_id, question)

    async def query_generation(
        self,
        kb_id: str,
        generation_id: str,
        question: str,
    ) -> GenerationQueryResult:
        kb = await self._require_kb(kb_id)
        return await self._query(kb, generation_id, question)

    async def _query(self, kb, generation_id: str, question: str) -> GenerationQueryResult:
        generation = await self._generation_repository.get(generation_id)
        if generation is None or generation.knowledge_base_id != kb.id:
            raise AppError(
                AppErrorCode.generation_not_found,
                "Generation 不存在。",
                status_code=404,
            )
        if generation.status in {
            VectorIndexGenerationStatus.failed,
            VectorIndexGenerationStatus.deleted,
        }:
            raise AppError(
                AppErrorCode.generation_invalid_state,
                "Generation 当前不可查询。",
                status_code=409,
            )
        settings = settings_for_knowledge_base(
            self._base_settings,
            kb,
            backend=VectorBackend(generation.backend),
            generation=generation.generation,
            working_dir=Path(generation.workspace_path),
        )
        runtime = await self._runtime_manager.get_runtime(kb.id, settings)
        result = await runtime.query(question, mode="mix")
        return GenerationQueryResult(
            generation_id=generation.id,
            generation_name=generation.generation,
            generation_epoch=int(kb.generation_epoch or 0),
            result=result,
        )

    async def _require_kb(self, kb_id: str):
        kb = await self._kb_repository.get(kb_id)
        if kb is None or kb.status in {KBStatus.deleting, KBStatus.deleted}:
            raise AppError(
                AppErrorCode.knowledge_base_not_found,
                "知识库不存在。",
                status_code=404,
            )
        return kb


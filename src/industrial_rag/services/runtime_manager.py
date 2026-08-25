"""Runtime manager for complete KB vector-index generations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from industrial_rag.config import Settings
from industrial_rag.lightrag_service import LightRAGService, QueryMode, QueryResult
from industrial_rag.operational_metrics import operational_metrics

logger = logging.getLogger(__name__)


class AsyncLightRAGService:
    """Async-friendly LightRAGService wrapper used by FastAPI routes."""

    def __init__(self, settings: Settings) -> None:
        self._svc = LightRAGService(settings)
        self._initialized = False

    async def initialize(self) -> None:
        await self._svc.initialize()
        self._initialized = True

    async def close(self) -> None:
        if self._initialized:
            await self._svc.close()
            self._initialized = False

    async def query(
        self,
        question: str,
        *,
        mode: QueryMode = "mix",
        top_k: int = 12,
        chunk_top_k: int = 20,
    ) -> QueryResult:
        if not self._initialized:
            raise RuntimeError("Service not initialized")
        return await self._svc.query(
            question, mode=mode, top_k=top_k, chunk_top_k=chunk_top_k
        )

    async def ingest(self, chunks: Any) -> str:
        if not self._initialized:
            raise RuntimeError("Service not initialized")
        return await self._svc.ingest(chunks)

    @property
    def initialized(self) -> bool:
        return self._initialized


@dataclass(frozen=True, slots=True)
class RuntimeCacheKey:
    kb_id: str
    vector_backend: str
    generation: str | None
    generation_epoch: int
    enable_llm_cache: bool
    workspace: str
    embedding_model: str
    embedding_dim: int
    vector_workspace: str | None = None

    @classmethod
    def from_settings(cls, kb_id: str, settings: Settings) -> RuntimeCacheKey:
        return cls(
            kb_id=kb_id,
            vector_backend=settings.vector_backend.value,
            generation=settings.qdrant_generation,
            generation_epoch=settings.generation_epoch,
            enable_llm_cache=settings.enable_llm_cache,
            workspace=str(settings.working_dir.resolve()),
            embedding_model=settings.embedding_model,
            embedding_dim=settings.embedding_dim,
            vector_workspace=settings.vector_workspace,
        )


class KnowledgeBaseRuntimeManager:
    """Cache only runtimes matching the full active generation identity."""

    def __init__(
        self,
        *,
        max_cached: int = 8,
        service_factory: Callable[[Settings], Any] | None = None,
    ) -> None:
        self._max_cached = max_cached
        self._service_factory = service_factory or AsyncLightRAGService
        self._runtimes: dict[RuntimeCacheKey, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_runtime(self, kb_id: str, settings: Settings) -> Any:
        key = RuntimeCacheKey.from_settings(kb_id, settings)
        cached = self._runtimes.get(key)
        if cached is not None and cached.initialized:
            operational_metrics.increment("runtime_cache_hit_total")
            return cached
        operational_metrics.increment("runtime_cache_miss_total")
        lock = self._locks.setdefault(kb_id, asyncio.Lock())
        async with lock:
            cached = self._runtimes.get(key)
            if cached is not None and cached.initialized:
                operational_metrics.increment("runtime_cache_hit_after_lock_total")
                return cached
            await self._close_mismatched_kb_runtimes(kb_id, keep=key)
            if len(self._runtimes) >= self._max_cached:
                await self._evict_one()
            service = self._service_factory(settings)
            await service.initialize()
            self._runtimes[key] = service
            logger.info(
                "Runtime created kb=%s backend=%s generation=%s workspace=%s",
                kb_id,
                key.vector_backend,
                key.generation,
                key.workspace,
            )
            return service

    async def close_runtime(self, kb_id: str) -> None:
        keys = [key for key in self._runtimes if key.kb_id == kb_id]
        for key in keys:
            service = self._runtimes.pop(key)
            try:
                await service.close()
            except Exception:
                logger.warning("Error closing runtime for kb=%s", kb_id, exc_info=True)
        self._locks.pop(kb_id, None)

    async def evict_runtime(self, kb_id: str) -> None:
        await self.close_runtime(kb_id)

    async def close_all(self) -> None:
        for kb_id in {key.kb_id for key in self._runtimes}:
            await self.close_runtime(kb_id)
        self._locks.clear()

    def is_cached(self, kb_id: str) -> bool:
        return any(key.kb_id == kb_id for key in self._runtimes)

    async def _close_mismatched_kb_runtimes(self, kb_id: str, *, keep: RuntimeCacheKey) -> None:
        for key in [key for key in self._runtimes if key.kb_id == kb_id and key != keep]:
            service = self._runtimes.pop(key)
            await service.close()

    async def _evict_one(self) -> None:
        if self._runtimes:
            key = next(iter(self._runtimes))
            service = self._runtimes.pop(key)
            try:
                await service.close()
            except Exception:
                logger.warning("Error evicting runtime for kb=%s", key.kb_id, exc_info=True)

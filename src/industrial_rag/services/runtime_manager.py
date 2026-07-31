"""Runtime manager: create / cache / evict LightRAGRuntime per knowledge base.

Each KB gets its own runtime backed by the KB's isolated LightRAG workspace.
The runtime manager ensures:

- One runtime per KB (no duplicates).
- KB delete / rebuild closes and evicts the runtime.
- FastAPI shutdown closes all runtimes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from industrial_rag.config import Settings
from industrial_rag.lightrag_service import LightRAGService, QueryMode, QueryResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Minimal async factory for LightRAGService (no sync bridge needed for API)
# ---------------------------------------------------------------------------


class AsyncLightRAGService:
    """Thin async-friendly wrapper around LightRAGService.

    Unlike ``LightRAGRuntime``, this does NOT create a background thread —
    it runs directly in the async FastAPI event loop.
    """

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

    async def query(self, question: str, *, mode: QueryMode = "mix") -> QueryResult:
        if not self._initialized:
            raise RuntimeError("Service not initialized")
        return await self._svc.query(question, mode=mode)

    async def ingest(self, chunks: Any) -> str:
        if not self._initialized:
            raise RuntimeError("Service not initialized")
        return await self._svc.ingest(chunks)

    @property
    def initialized(self) -> bool:
        return self._initialized


# ---------------------------------------------------------------------------
# Runtime Manager
# ---------------------------------------------------------------------------


class KnowledgeBaseRuntimeManager:
    """Creates and caches async LightRAG services keyed by KB id.

    Not thread-safe — use only within the asyncio event loop.
    """

    def __init__(
        self,
        *,
        max_cached: int = 8,
        service_factory: Callable[[Settings], Any] | None = None,
    ) -> None:
        self._max_cached = max_cached
        self._service_factory = service_factory or AsyncLightRAGService
        self._runtimes: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_runtime(self, kb_id: str, settings: Settings) -> Any:
        """Return a ready async LightRAG service for the given KB."""
        # Fast path: already cached
        cached = self._runtimes.get(kb_id)
        if cached is not None and cached.initialized:
            return cached

        # Serialize creation per KB
        lock = self._locks.setdefault(kb_id, asyncio.Lock())
        async with lock:
            # Double-check inside lock
            cached = self._runtimes.get(kb_id)
            if cached is not None and cached.initialized:
                return cached

            # Evict LRU if over limit
            if len(self._runtimes) >= self._max_cached:
                self._evict_one()

            svc = self._service_factory(settings)
            await svc.initialize()
            self._runtimes[kb_id] = svc
            logger.info("Runtime created for kb=%s workspace=%s", kb_id, settings.working_dir)
            return svc

    async def close_runtime(self, kb_id: str) -> None:
        """Close and evict the runtime for a KB."""
        svc = self._runtimes.pop(kb_id, None)
        if svc is not None:
            try:
                await svc.close()
            except Exception:
                logger.warning("Error closing runtime for kb=%s", kb_id, exc_info=True)
        self._locks.pop(kb_id, None)

    async def evict_runtime(self, kb_id: str) -> None:
        """Alias for close_runtime."""
        await self.close_runtime(kb_id)

    async def close_all(self) -> None:
        """Close and evict all cached runtimes. Called at FastAPI shutdown."""
        for kb_id in list(self._runtimes.keys()):
            await self.close_runtime(kb_id)
        self._runtimes.clear()
        self._locks.clear()

    def is_cached(self, kb_id: str) -> bool:
        return kb_id in self._runtimes

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _evict_one(self) -> None:
        """Simple FIFO eviction — remove the first entry."""
        if self._runtimes:
            first_key = next(iter(self._runtimes))
            svc = self._runtimes.pop(first_key)
            # Fire-and-forget close (best effort)
            try:
                asyncio.ensure_future(svc.close())
            except Exception:
                pass
            self._locks.pop(first_key, None)

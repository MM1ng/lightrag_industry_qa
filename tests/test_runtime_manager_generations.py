from __future__ import annotations

from pathlib import Path

import pytest
from industrial_rag.config import Settings
from industrial_rag.services.runtime_manager import KnowledgeBaseRuntimeManager
from industrial_rag.vector_collections import VectorBackend


class _Service:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.initialized = False
        self.closed = False

    async def initialize(self) -> None:
        self.initialized = True

    async def close(self) -> None:
        self.closed = True
        self.initialized = False


def _settings(*, backend: VectorBackend, generation: str | None, workspace: Path) -> Settings:
    return Settings(
        api_key="test",
        vector_backend=backend,
        qdrant_url="http://qdrant.test:6333" if backend is VectorBackend.qdrant else None,
        qdrant_generation=generation,
        qdrant_kb_id="a" * 32 if backend is VectorBackend.qdrant else None,
        working_dir=workspace,
    )


@pytest.mark.asyncio
async def test_runtime_manager_replaces_cached_runtime_when_generation_changes(tmp_path: Path) -> None:
    manager = KnowledgeBaseRuntimeManager(service_factory=_Service)
    first = await manager.get_runtime(
        "a" * 32,
        _settings(backend=VectorBackend.qdrant, generation="g20260731aaa", workspace=tmp_path / "g1"),
    )
    second = await manager.get_runtime(
        "a" * 32,
        _settings(backend=VectorBackend.qdrant, generation="g20260731bbb", workspace=tmp_path / "g2"),
    )

    assert second is not first
    assert first.closed is True
    assert manager.is_cached("a" * 32)

"""Build trusted per-knowledge-base LightRAG settings."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from industrial_rag.config import Settings
from industrial_rag.vector_collections import VectorBackend


def settings_for_knowledge_base(
    base: Settings,
    knowledge_base: Any,
    *,
    backend: VectorBackend | None = None,
    generation: str | None = None,
    working_dir: Path | None = None,
) -> Settings:
    """Derive runtime settings exclusively from trusted KB metadata."""
    selected_backend = backend or VectorBackend(knowledge_base.vector_backend)
    active_generation = generation
    active_record = getattr(knowledge_base, "active_vector_generation", None)
    if active_generation is None and active_record is not None:
        active_generation = active_record.generation
    if selected_backend is VectorBackend.qdrant and active_generation is None:
        raise RuntimeError("Qdrant knowledge base has no active generation record")
    if selected_backend is VectorBackend.qdrant and not base.qdrant_url:
        raise RuntimeError("Qdrant backend selected but QDRANT_URL is not configured")
    record_workspace = (
        Path(active_record.workspace_path)
        if selected_backend is VectorBackend.qdrant and active_record is not None and generation is None
        else None
    )
    return replace(
        base,
        embedding_model=knowledge_base.embedding_model,
        embedding_dim=knowledge_base.embedding_dimension,
        working_dir=(working_dir or record_workspace or Path(knowledge_base.workspace_path)).resolve(),
        vector_backend=selected_backend,
        qdrant_generation=(
            active_generation if selected_backend is VectorBackend.qdrant else None
        ),
        qdrant_kb_id=knowledge_base.id if selected_backend is VectorBackend.qdrant else None,
        vector_workspace=(
            f"{selected_backend.value}-{active_generation}"
            if active_generation is not None
            else None
        ),
    )

"""Knowledge-base selection helpers for the ordinary service user."""

from __future__ import annotations

from collections.abc import Iterable

from app.api_client import ApiKnowledgeBase


def queryable_knowledge_bases(items: Iterable[ApiKnowledgeBase]) -> tuple[ApiKnowledgeBase, ...]:
    """Keep only KBs whose active generation is ready for ordinary queries."""
    return tuple(item for item in items if item.status.lower() == "ready")


def knowledge_base_label(item: ApiKnowledgeBase) -> str:
    """Return a user-facing label without exposing credentials or diagnostics."""
    generation = item.active_generation or "未激活"
    return (
        f"{item.name} · {item.status} · 文档 {item.document_count} · "
        f"Chunk {item.chunk_count} · Generation {generation[:12]}"
    )


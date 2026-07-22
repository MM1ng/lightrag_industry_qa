"""Minimal LightRAG knowledge-base QA system for centrifugal-pump manuals."""

from industrial_rag.config import Settings
from industrial_rag.lightrag_service import LightRAGService

__all__ = ["LightRAGService", "Settings"]

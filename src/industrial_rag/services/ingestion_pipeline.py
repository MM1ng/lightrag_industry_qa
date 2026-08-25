"""Ingestion pipeline: orchestrates the full parse → index lifecycle.

Handles task chaining: when a parse task succeeds, automatically
creates the follow-up index (or rebuild) task.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.models import TaskStatus, TaskType
from industrial_rag.repositories.document_repository import DocumentRepository
from industrial_rag.repositories.knowledge_base_repository import KnowledgeBaseRepository
from industrial_rag.repositories.task_repository import TaskRepository

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """Coordinates parse → index task flow.

    After a parse task succeeds, this creates a follow-up index (or rebuild)
    task so the executor can pick it up in the next poll cycle.
    """

    def __init__(self, session: AsyncSession, *, runtime_manager: Any = None) -> None:
        self._kb_repo = KnowledgeBaseRepository(session)
        self._doc_repo = DocumentRepository(session)
        self._task_repo = TaskRepository(session)
        self._runtime_manager = runtime_manager

    async def on_parse_succeeded(self, kb_id: str, doc_id: str, parse_manifest: dict) -> str:
        """Create a follow-up index task after a successful parse.

        Returns the new task's ID.
        """
        # Check if there's already a pending index/rebuild task
        existing = await self._task_repo.list_by_doc(doc_id)
        for t in existing:
            if t.task_type in (TaskType.index, TaskType.rebuild) and t.status in (
                TaskStatus.pending,
                TaskStatus.running,
                TaskStatus.retrying,
            ):
                logger.info(
                    "Index task already exists for doc=%s: task=%s", doc_id, t.id
                )
                return t.id

        # Use rebuild for safety (avoids untested incremental insert edge cases)
        task_type = TaskType.rebuild
        task = await self._task_repo.create(
            knowledge_base_id=kb_id,
            document_id=doc_id,
            task_type=task_type,
            payload={
                "trigger": "parse_completed",
                "document_id": doc_id,
                "manifest_hash": parse_manifest.get("manifest_hash"),
            },
        )
        logger.info(
            "Created %s task %s for kb=%s doc=%s",
            task_type.value, task.id, kb_id, doc_id,
        )
        return task.id

    async def on_parse_failed(self, kb_id: str, doc_id: str, error: str) -> None:
        """Update document state when parse fails.  No index task is created."""
        await self._doc_repo.update(
            doc_id, parse_status="failed", last_error=str(error)[:1000],
        )
        logger.warning("Parse failed for doc=%s: %s", doc_id, error[:200])

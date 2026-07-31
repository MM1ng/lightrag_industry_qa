"""KB cleanup service: delete all resources for a knowledge base."""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.models import KBStatus
from industrial_rag.errors import AppError, AppErrorCode
from industrial_rag.repositories.document_repository import DocumentRepository
from industrial_rag.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from industrial_rag.repositories.task_repository import TaskRepository
from industrial_rag.storage_layout import (
    is_safe_to_delete,
    kb_base_dir,
)

logger = logging.getLogger(__name__)

# Steps in KB delete (ordered)
_CLEANUP_STEPS = [
    "close_runtime",
    "delete_workspace",
    "delete_parsed",
    "delete_uploads",
    "delete_temp",
    "mark_documents_deleted",
    "mark_kb_deleted",
]


class KnowledgeBaseCleanupService:
    """Performs the physical cleanup of a deleted knowledge base.

    Each step is idempotent and records its own status so the whole
    operation can be safely retried.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        runtime_manager: Any | None = None,
        delete_source_files: bool = False,
    ) -> None:
        self._session = session
        self._kb_repo = KnowledgeBaseRepository(session)
        self._doc_repo = DocumentRepository(session)
        self._task_repo = TaskRepository(session)
        self._runtime_manager = runtime_manager
        self._delete_source_files = delete_source_files

        # Initialise step tracking
        self._steps: list[dict] = [
            {"step_name": name, "status": "pending", "attempt": 0, "error": None, "completed_at": None}
            for name in _CLEANUP_STEPS
        ]

    async def execute(self, kb_id: str, task_id: str) -> None:
        kb = await self._kb_repo.get(kb_id)
        if kb is None or kb.status not in (KBStatus.deleting, KBStatus.deleted):
            return

        base_dir = kb_base_dir(kb_id)

        for idx, step in enumerate(self._steps):
            try:
                step["attempt"] += 1
                await self._execute_step(step["step_name"], kb_id, base_dir)
                step["status"] = "succeeded"
                step["completed_at"] = datetime.now(tz=UTC).isoformat()
            except Exception as exc:
                step["status"] = "failed"
                step["error"] = str(exc)[:500]
                logger.error("Cleanup step %s failed for kb=%s: %s", step["step_name"], kb_id, exc)
                await self._task_repo.update(
                    task_id,
                    error_code="cleanup_step_failed",
                    error_message=f"Step {step['step_name']} failed: {exc}",
                    cleanup_steps=self._steps,
                )
                # Update KB with error but keep deleting status for retry
                await self._kb_repo.update(
                    kb_id, last_error=f"Cleanup step {step['step_name']} failed: {exc}"
                )
                raise

        # All steps succeeded
        await self._kb_repo.mark_deleted(kb_id)
        await self._task_repo.mark_succeeded(
            task_id, result={"cleaned_kb_id": kb_id, "steps": self._steps}
        )
        logger.info("Knowledge base fully deleted: id=%s", kb_id)

    # ------------------------------------------------------------------
    # Individual steps
    # ------------------------------------------------------------------

    async def _execute_step(self, step_name: str, kb_id: str, base_dir: Path) -> None:
        if step_name == "close_runtime":
            if self._runtime_manager is not None:
                await self._runtime_manager.close_runtime(kb_id)

        elif step_name == "delete_workspace":
            workspace = base_dir / "lightrag"
            self._safe_delete_dir(workspace, kb_id)

        elif step_name == "delete_parsed":
            parsed = base_dir / "parsed"
            self._safe_delete_dir(parsed, kb_id)

        elif step_name == "delete_uploads":
            if self._delete_source_files:
                uploads = base_dir / "uploads"
                self._safe_delete_dir(uploads, kb_id)

        elif step_name == "delete_temp":
            tmp = base_dir / "tmp"
            self._safe_delete_dir(tmp, kb_id)

        elif step_name == "mark_documents_deleted":
            docs = await self._doc_repo.list_by_kb(kb_id, include_deleted=True)
            for doc in docs:
                if doc.status.value != "deleted":
                    await self._doc_repo.mark_deleted(doc.id)

        elif step_name == "mark_kb_deleted":
            await self._kb_repo.update(kb_id, last_error=None)

    # ------------------------------------------------------------------
    # Path safety
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_delete_dir(path: Path, kb_id: str) -> None:
        """Delete a directory tree with safety checks."""
        if not path.exists():
            return  # idempotent
        if not is_safe_to_delete(path, kb_id=kb_id):
            raise AppError(
                AppErrorCode.path_traversal_rejected,
                f"不安全删除: {path}",
            )
        shutil.rmtree(path, ignore_errors=False)

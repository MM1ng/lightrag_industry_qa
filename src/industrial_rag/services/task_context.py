"""Task execution context passed to every handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from industrial_rag.db.models import LifecycleTask
from industrial_rag.repositories.document_repository import DocumentRepository
from industrial_rag.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from industrial_rag.repositories.task_repository import TaskRepository


@dataclass
class TaskExecutionContext:
    task: LifecycleTask
    kb_repo: KnowledgeBaseRepository
    doc_repo: DocumentRepository
    task_repo: TaskRepository
    runtime_manager: Any = None
    settings: Any = None
    delete_source_files: bool = False

    async def update_progress(self, progress: float, stage: str | None = None) -> None:
        values: dict[str, Any] = {"progress": min(max(progress, 0.0), 1.0)}
        if stage is not None:
            values["current_stage"] = stage
        await self.task_repo.update(self.task.id, **values)


@dataclass
class TaskExecutionResult:
    success: bool
    error_code: str | None = None
    error_message: str | None = None
    result: dict[str, Any] | None = None

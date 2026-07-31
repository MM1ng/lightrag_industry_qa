"""KB service: orchestrates CRUD through repository + storage layout."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.models import KBStatus
from industrial_rag.errors import AppError, AppErrorCode
from industrial_rag.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from industrial_rag.repositories.task_repository import TaskRepository
from industrial_rag.storage_layout import (
    kb_base_dir,
    kb_parsed_dir,
    kb_uploads_dir,
    kb_workspace_dir,
)

logger = logging.getLogger(__name__)


class KnowledgeBaseService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = KnowledgeBaseRepository(session)
        self._task_repo = TaskRepository(session)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        name: str,
        description: str | None = None,
        embedding_model: str = "text-embedding-v4",
        embedding_dimension: int = 1024,
        parser_name: str = "PyMuPDF",
        chunking_strategy: str = "fixed_character",
        chunking_version: str = "1",
        chunking_config: dict | None = None,
    ) -> Any:
        name = name.strip()
        if not name or len(name) > 200:
            raise AppError(
                AppErrorCode.invalid_state_transition,
                "知识库名称需为 1-200 个字符",
                status_code=422,
            )

        kb = await self._repo.create(name=name, description=description)

        try:
            # Create isolated directories
            base = kb_base_dir(kb.id)
            base.mkdir(parents=True, exist_ok=True)
            kb_uploads_dir(kb.id).mkdir(parents=True, exist_ok=True)
            kb_parsed_dir(kb.id).mkdir(parents=True, exist_ok=True)
            kb_workspace_dir(kb.id).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AppError(AppErrorCode.storage_failure, f"无法创建知识库目录: {exc}")

        await self._repo.update(
            kb.id,
            workspace_path=str(kb_workspace_dir(kb.id)),
            upload_path=str(kb_uploads_dir(kb.id)),
            parsed_path=str(kb_parsed_dir(kb.id)),
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
            parser_name=parser_name,
            chunking_strategy=chunking_strategy,
            chunking_version=chunking_version,
            chunking_config=chunking_config,
            status=KBStatus.ready,
        )

        logger.info("Knowledge base created: id=%s name=%s", kb.id, name)
        return await self._repo.get(kb.id)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, kb_id: str) -> Any:
        kb = await self._repo.get(kb_id)
        if kb is None:
            raise AppError(
                AppErrorCode.knowledge_base_not_found,
                f"知识库不存在: {kb_id}",
            )
        return kb

    async def list_all(
        self,
        *,
        include_deleted: bool = False,
        status_filter: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[Any], int]:
        filter_enum = KBStatus(status_filter) if status_filter else None
        kbs = await self._repo.list_all(
            include_deleted=include_deleted,
            status_filter=filter_enum,
            offset=offset,
            limit=limit,
        )
        total = await self._repo.count_all(include_deleted=include_deleted)
        return kbs, total

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    async def update(
        self,
        kb_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Any:
        kb = await self.get(kb_id)
        if kb.status in (KBStatus.deleting, KBStatus.deleted):
            raise AppError(
                AppErrorCode.invalid_state_transition,
                f"知识库状态为 {kb.status.value}，无法修改",
                status_code=409,
            )
        values: dict[str, Any] = {}
        if name is not None:
            name = name.strip()
            if not name or len(name) > 200:
                raise AppError(
                    AppErrorCode.invalid_state_transition,
                    "知识库名称需为 1-200 个字符",
                    status_code=422,
                )
            values["name"] = name
        if description is not None:
            values["description"] = description.strip() if description else None
        if not values:
            return kb

        await self._repo.update(kb_id, **values)
        return await self._repo.get(kb_id)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def request_delete(self, kb_id: str) -> Any:
        """Initiate async deletion.  Returns 202 with task info."""
        kb = await self.get(kb_id)
        if kb.status == KBStatus.deleted:
            raise AppError(
                AppErrorCode.invalid_state_transition,
                "知识库已删除",
                status_code=409,
            )
        if kb.protect_from_delete:
            raise AppError(
                AppErrorCode.kb_protected_from_delete,
                "此知识库受保护，不可删除",
                status_code=403,
            )
        if kb.status == KBStatus.deleting:
            raise AppError(
                AppErrorCode.knowledge_base_deleting,
                "知识库正在删除中",
                status_code=423,
            )
        if await self._repo.has_active_tasks(kb_id):
            raise AppError(
                AppErrorCode.knowledge_base_busy,
                "知识库有正在运行的任务，请等待完成后再删除",
                status_code=409,
            )

        await self._repo.soft_delete(kb_id)

        task = await self._task_repo.create(
            knowledge_base_id=kb_id,
            task_type="delete_knowledge_base",
            payload={"kb_name": kb.name},
        )

        return {"task_id": task.id, "knowledge_base_id": kb_id, "status": "pending"}

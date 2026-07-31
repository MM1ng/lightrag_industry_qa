"""Document service: upload, list, reparse, reindex, delete."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.models import KBStatus, TaskType
from industrial_rag.errors import AppError, AppErrorCode
from industrial_rag.repositories.document_repository import DocumentRepository
from industrial_rag.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from industrial_rag.repositories.task_repository import TaskRepository
from industrial_rag.storage_layout import document_stored_path

logger = logging.getLogger(__name__)

# 20 MB
MAX_UPLOAD_SIZE = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf"}
ALLOWED_MIME = {"application/pdf"}

PDF_MAGIC = b"%PDF-"


class DocumentService:
    def __init__(self, session: AsyncSession) -> None:
        self._doc_repo = DocumentRepository(session)
        self._kb_repo = KnowledgeBaseRepository(session)
        self._task_repo = TaskRepository(session)

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    async def upload(
        self,
        kb_id: str,
        *,
        original_file_name: str,
        content: bytes,
        mime_type: str = "application/pdf",
    ) -> Any:
        # 1. Validate KB
        kb = await self._kb_repo.get(kb_id)
        if kb is None:
            raise AppError(AppErrorCode.knowledge_base_not_found, f"知识库不存在: {kb_id}")
        if kb.status in (KBStatus.deleting, KBStatus.deleted):
            raise AppError(AppErrorCode.invalid_state_transition, "知识库正在删除或已删除", status_code=409)

        # 2. Validate file
        ext = Path(original_file_name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise AppError(
                AppErrorCode.unsupported_file_type,
                f"不支持的文件类型: {ext}，仅支持 PDF",
                status_code=415,
            )
        if not content:
            raise AppError(AppErrorCode.empty_file, "上传的文件为空", status_code=422)
        if len(content) > MAX_UPLOAD_SIZE:
            raise AppError(
                AppErrorCode.file_too_large,
                f"文件过大: {len(content)} bytes (上限 {MAX_UPLOAD_SIZE})",
                status_code=413,
            )
        if not content.startswith(PDF_MAGIC):
            raise AppError(AppErrorCode.invalid_pdf, "不是有效的 PDF 文件", status_code=422)

        # 3. Hash and check duplicates
        file_hash = hashlib.sha256(content).hexdigest()
        existing = await self._doc_repo.find_by_hash(kb_id, file_hash)
        if existing is not None:
            raise AppError(
                AppErrorCode.duplicate_document,
                f"该知识库中已存在相同文件: {existing.original_file_name}",
                status_code=409,
            )

        # 4. Create Document record
        import re

        safe_stem = re.sub(r"[^a-zA-Z0-9._-]", "_", Path(original_file_name).stem)
        stored_name = f"{safe_stem}_{file_hash[:8]}.pdf"
        stored_path = document_stored_path(kb_id, stored_name)
        file_size = len(content)

        doc = await self._doc_repo.create(
            knowledge_base_id=kb_id,
            original_file_name=original_file_name,
            stored_file_name=stored_name,
            file_path=str(stored_path),
            file_hash=file_hash,
            file_size=file_size,
            mime_type=mime_type,
            parser_name=kb.parser_name,
            parser_version=kb.parser_version,
            chunking_strategy=kb.chunking_strategy,
            chunking_version=kb.chunking_version,
        )

        # 5. Write file to disk
        try:
            stored_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = stored_path.with_suffix(".tmp")
            tmp_path.write_bytes(content)
            tmp_path.replace(stored_path)
        except OSError as exc:
            # Clean up DB record on failure
            await self._doc_repo.update(doc.id, status="failed", last_error=str(exc))
            raise AppError(AppErrorCode.storage_failure, f"文件写入失败: {exc}")

        # 6. Create parse task
        await self._task_repo.create(
            knowledge_base_id=kb_id,
            document_id=doc.id,
            task_type=TaskType.parse,
            payload={"file_name": original_file_name, "stored_path": str(stored_path)},
        )

        logger.info("Document uploaded: id=%s kb=%s file=%s", doc.id, kb_id, original_file_name)
        return doc

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, kb_id: str, doc_id: str) -> Any:
        doc = await self._doc_repo.get_by_kb_and_id(kb_id, doc_id)
        if doc is None:
            raise AppError(AppErrorCode.document_not_found, f"文档不存在: {doc_id}")
        return doc

    async def list_by_kb(
        self,
        kb_id: str,
        *,
        include_deleted: bool = False,
        status_filter: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[Any], int]:
        from industrial_rag.db.models import DocumentStatus

        filter_enum = DocumentStatus(status_filter) if status_filter else None
        docs = await self._doc_repo.list_by_kb(
            kb_id,
            include_deleted=include_deleted,
            status_filter=filter_enum,
            offset=offset,
            limit=limit,
        )
        total = await self._doc_repo.count_by_kb(kb_id)
        return docs, total

    # ------------------------------------------------------------------
    # Reparse / Reindex
    # ------------------------------------------------------------------

    async def request_reparse(self, kb_id: str, doc_id: str) -> Any:
        doc = await self.get(kb_id, doc_id)
        if await self._has_active_task(doc_id):
            raise AppError(
                AppErrorCode.task_already_running, "文档已有运行中的任务", status_code=409
            )

        task = await self._task_repo.create(
            knowledge_base_id=kb_id,
            document_id=doc_id,
            task_type=TaskType.reparse,
            payload={"file_name": doc.original_file_name},
        )
        return {"task_id": task.id, "document_id": doc_id, "status": "pending"}

    async def request_reindex(self, kb_id: str, doc_id: str) -> Any:
        doc = await self.get(kb_id, doc_id)
        if await self._has_active_task(doc_id):
            raise AppError(
                AppErrorCode.task_already_running, "文档已有运行中的任务", status_code=409
            )

        task = await self._task_repo.create(
            knowledge_base_id=kb_id,
            document_id=doc_id,
            task_type=TaskType.rebuild,
            payload={"file_name": doc.original_file_name, "reason": "manual reindex request"},
        )
        return {"task_id": task.id, "document_id": doc_id, "status": "pending"}

    # ------------------------------------------------------------------
    # Delete document
    # ------------------------------------------------------------------

    async def request_delete(self, kb_id: str, doc_id: str) -> Any:
        doc = await self.get(kb_id, doc_id)
        if await self._has_active_task(doc_id):
            raise AppError(
                AppErrorCode.task_already_running, "文档已有运行中的任务", status_code=409
            )

        await self._doc_repo.soft_delete(doc_id)

        task = await self._task_repo.create(
            knowledge_base_id=kb_id,
            document_id=doc_id,
            task_type=TaskType.delete_document,
            payload={"file_name": doc.original_file_name},
        )

        # Also create a rebuild task for the KB
        await self._task_repo.create(
            knowledge_base_id=kb_id,
            document_id=None,
            task_type=TaskType.rebuild,
            payload={
                "trigger": "document_delete",
                "deleted_document_id": doc_id,
            },
        )

        return {"task_id": task.id, "document_id": doc_id, "status": "pending"}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _has_active_task(self, doc_id: str) -> bool:
        tasks = await self._task_repo.list_by_doc(doc_id, limit=5)
        from industrial_rag.db.models import TaskStatus

        active_statuses = {TaskStatus.pending, TaskStatus.running, TaskStatus.retrying}
        return any(t.status in active_statuses for t in tasks)

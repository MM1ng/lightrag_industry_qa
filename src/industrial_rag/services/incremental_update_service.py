"""Incremental knowledge-base updates with generation lifecycle (Phase 9).

One candidate generation is created per update operation (add / replace /
delete).  The candidate inherits unchanged documents from the active
generation by *copying* its workspace and Qdrant points (vectors are reused,
never recomputed for unchanged chunks).  Only the changed document is parsed,
chunked, embedded and indexed.  Promote/rollback are atomic pointer switches;
physical cleanup is never part of this flow.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import shutil
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from qdrant_client import AsyncQdrantClient, models
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.config import Settings
from industrial_rag.db.models import (
    DocumentStatus,
    KBStatus,
    UpdateJobStatus,
    UpdateOperation,
    VectorIndexGenerationStatus,
)
from industrial_rag.errors import AppError, AppErrorCode
from industrial_rag.kb_runtime_settings import settings_for_knowledge_base
from industrial_rag.operational_metrics import operational_metrics
from industrial_rag.repositories.document_repository import DocumentRepository
from industrial_rag.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from industrial_rag.repositories.update_job_repository import UpdateJobRepository
from industrial_rag.repositories.vector_index_generation_repository import (
    VectorIndexGenerationRepository,
)
from industrial_rag.services.kb_lease_service import KBLeaseService
from industrial_rag.storage_layout import (
    document_stored_path,
    kb_parsed_documents_dir,
    kb_qdrant_generation_workspace,
)
from industrial_rag.vector_collections import CollectionNameResolver, VectorBackend

logger = logging.getLogger(__name__)

MAX_UPLOAD_SIZE = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf"}
PDF_MAGIC = b"%PDF-"

# In-process per-KB lock registry so concurrent API requests serialize
# promote/rollback even though each request creates a fresh service instance.
_KB_LOCKS: dict[str, asyncio.Lock] = {}
_KB_LOCKS_GUARD = asyncio.Lock()


def _kb_lock(kb_id: str) -> asyncio.Lock:
    return _KB_LOCKS.setdefault(kb_id, asyncio.Lock())


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _sanitize_error(error: Exception) -> str:
    text = str(error) or error.__class__.__name__
    # Never persist local user directories or full stack traces.
    text = re.sub(r"[A-Za-z]:\\\\Users\\\\[^\\\\]+", "<USER_DIR>", text)
    text = re.sub(r"[A-Za-z]:/Users/[^/]+", "<USER_DIR>", text)
    return text[:1000]


def _point_id(value: str) -> str:
    """Deterministic Qdrant point id (same scheme as the storage adapter)."""
    return str(uuid.UUID(bytes=hashlib.sha256(value.encode("utf-8")).digest()[:16]))


class IncrementalUpdateService:
    """Orchestrates incremental document updates and generation lifecycle."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        runtime_manager: Any = None,
        qdrant_client_factory: Callable[[], AsyncQdrantClient] | None = None,
        lightrag_service_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self._session = session
        self._kb_repo = KnowledgeBaseRepository(session)
        self._doc_repo = DocumentRepository(session)
        self._generation_repo = VectorIndexGenerationRepository(session)
        self._job_repo = UpdateJobRepository(session)
        self._settings = settings or Settings.from_env()
        self._runtime_manager = runtime_manager
        self._qdrant_client_factory = qdrant_client_factory
        self._lightrag_service_factory = lightrag_service_factory

    @asynccontextmanager
    async def _writer_lease(
        self,
        kb_id: str,
        *,
        actor: str | None,
        operation: str,
        ttl: timedelta,
    ):
        service = KBLeaseService(self._session)
        handle = None
        for attempt in range(20):
            handle = await service.acquire(
                kb_id,
                owner=actor or "admin:local-dev",
                operation=operation,
                now=_utcnow(),
                ttl=ttl,
            )
            if handle is not None:
                break
            if attempt < 19:
                await asyncio.sleep(0.05)
        if handle is None:
            raise AppError(
                AppErrorCode.knowledge_base_busy,
                "知识库正在执行其他管理操作。",
                status_code=409,
            )
        try:
            yield handle
        except Exception:
            await self._session.rollback()
            raise
        finally:
            await service.release(handle)

    # ------------------------------------------------------------------
    # Document operations
    # ------------------------------------------------------------------

    async def add_document(
        self,
        kb_id: str,
        *,
        original_file_name: str,
        content: bytes,
        logical_name: str | None = None,
        mime_type: str = "application/pdf",
        request_id: str | None = None,
        trace_id: str | None = None,
        created_by: str = "api",
    ) -> dict[str, Any]:
        kb = await self._require_kb(kb_id)
        content_hash, _ = self._validate_file(original_file_name, content)
        existing = await self._doc_repo.find_by_hash(kb_id, content_hash)
        if existing is not None:
            return {
                "status": "no_change",
                "document_id": existing.id,
                "knowledge_base_id": kb_id,
                "reason": "same content sha256 already present in the knowledge base",
            }
        if await self._job_repo.find_active_for_kb(kb_id) is not None:
            raise AppError(
                AppErrorCode.knowledge_base_busy,
                "知识库已有进行中的增量更新任务",
                status_code=409,
            )

        safe_stem = re.sub(r"[^a-zA-Z0-9._-]", "_", Path(original_file_name).stem)
        stored_name = f"{safe_stem}_{content_hash[:8]}.pdf"
        stored_path = document_stored_path(kb_id, stored_name)
        doc = await self._doc_repo.create(
            knowledge_base_id=kb_id,
            original_file_name=original_file_name,
            logical_name=logical_name or original_file_name,
            source_type=mime_type or "application/pdf",
            stored_file_name=stored_name,
            file_path=str(stored_path),
            file_hash=content_hash,
            file_size=len(content),
            mime_type=mime_type or "application/pdf",
            parser_name=kb.parser_name,
            parser_version=kb.parser_version,
            chunking_strategy=kb.chunking_strategy,
            chunking_version=kb.chunking_version,
            status=DocumentStatus.uploaded,
        )
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = stored_path.with_suffix(".tmp")
        tmp_path.write_bytes(content)
        tmp_path.replace(stored_path)

        base = await self._generation_repo.get_active(kb_id)
        job = await self._job_repo.create(
            knowledge_base_id=kb_id,
            base_generation_id=base.id if base else None,
            operation=UpdateOperation.add,
            document_id=doc.id,
            old_content_sha256=None,
            new_content_sha256=content_hash,
            status=UpdateJobStatus.pending,
            request_id=request_id,
            trace_id=trace_id,
            created_by=created_by,
        )
        await self._session.commit()
        return await self._execute_persisted_job(kb_id, job.id)

    async def replace_document(
        self,
        kb_id: str,
        doc_id: str,
        *,
        content: bytes,
        original_file_name: str | None = None,
        mime_type: str = "application/pdf",
        request_id: str | None = None,
        trace_id: str | None = None,
        created_by: str = "api",
    ) -> dict[str, Any]:
        kb = await self._require_kb(kb_id)
        original = await self._doc_repo.get_by_kb_and_id(kb_id, doc_id)
        if original is None:
            raise AppError(AppErrorCode.document_not_found, f"文档不存在: {doc_id}")
        old_doc = await self._resolve_active_version(kb_id, original)
        content_hash, _ = self._validate_file(
            original_file_name or old_doc.original_file_name, content
        )
        if content_hash == old_doc.file_hash:
            return {
                "status": "no_change",
                "document_id": doc_id,
                "knowledge_base_id": kb_id,
                "reason": "new content sha256 equals current document sha256",
            }
        if await self._job_repo.find_active_for_kb(kb_id) is not None:
            raise AppError(
                AppErrorCode.knowledge_base_busy,
                "知识库已有进行中的增量更新任务",
                status_code=409,
            )

        file_name = original_file_name or old_doc.original_file_name
        safe_stem = re.sub(r"[^a-zA-Z0-9._-]", "_", Path(file_name).stem)
        stored_name = f"{safe_stem}_{content_hash[:8]}.pdf"
        stored_path = document_stored_path(kb_id, stored_name)
        new_doc = await self._doc_repo.create(
            knowledge_base_id=kb_id,
            original_file_name=file_name,
            logical_name=old_doc.logical_name or old_doc.original_file_name,
            source_type=mime_type or old_doc.source_type or "application/pdf",
            stored_file_name=stored_name,
            file_path=str(stored_path),
            file_hash=content_hash,
            file_size=len(content),
            mime_type=mime_type or "application/pdf",
            parser_name=kb.parser_name,
            parser_version=kb.parser_version,
            chunking_strategy=kb.chunking_strategy,
            chunking_version=kb.chunking_version,
            version=old_doc.version + 1,
            status=DocumentStatus.uploaded,
        )
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = stored_path.with_suffix(".tmp")
        tmp_path.write_bytes(content)
        tmp_path.replace(stored_path)

        base = await self._generation_repo.get_active(kb_id)
        job = await self._job_repo.create(
            knowledge_base_id=kb_id,
            base_generation_id=base.id if base else None,
            operation=UpdateOperation.replace,
            document_id=new_doc.id,
            old_content_sha256=old_doc.file_hash,
            new_content_sha256=content_hash,
            status=UpdateJobStatus.pending,
            request_id=request_id,
            trace_id=trace_id,
            created_by=created_by,
            metrics={"replaced_document_id": old_doc.id},
        )
        await self._session.commit()
        return await self._execute_persisted_job(kb_id, job.id, old_doc=old_doc)

    async def delete_document(
        self,
        kb_id: str,
        doc_id: str,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
        created_by: str = "api",
    ) -> dict[str, Any]:
        await self._require_kb(kb_id)
        original = await self._doc_repo.get_by_kb_and_id(kb_id, doc_id)
        if original is None:
            raise AppError(AppErrorCode.document_not_found, f"文档不存在: {doc_id}")
        doc = await self._resolve_active_version(kb_id, original)
        if await self._job_repo.find_active_for_kb(kb_id) is not None:
            raise AppError(
                AppErrorCode.knowledge_base_busy,
                "知识库已有进行中的增量更新任务",
                status_code=409,
            )
        base = await self._generation_repo.get_active(kb_id)
        job = await self._job_repo.create(
            knowledge_base_id=kb_id,
            base_generation_id=base.id if base else None,
            operation=UpdateOperation.delete,
            document_id=doc.id,
            old_content_sha256=doc.file_hash,
            new_content_sha256=None,
            status=UpdateJobStatus.pending,
            request_id=request_id,
            trace_id=trace_id,
            created_by=created_by,
        )
        await self._session.commit()
        return await self._execute_persisted_job(kb_id, job.id, old_doc=doc)

    # ------------------------------------------------------------------
    # Generations
    # ------------------------------------------------------------------

    async def list_generations(self, kb_id: str) -> list[dict[str, Any]]:
        await self._require_kb(kb_id)
        generations = await self._generation_repo.list_for_kb(kb_id)
        return [self._generation_summary(g) for g in generations]

    async def get_generation(self, kb_id: str, generation_id: str) -> dict[str, Any]:
        await self._require_kb(kb_id)
        generation = await self._generation_repo.get(generation_id)
        if generation is None or generation.knowledge_base_id != kb_id:
            raise AppError(
                AppErrorCode.generation_not_found, f"Generation 不存在: {generation_id}"
            )
        return self._generation_summary(generation)

    async def validate_generation(
        self,
        kb_id: str,
        generation_id: str,
        *,
        approved_by: str | None = None,
        golden_runner: Any = None,
    ) -> dict[str, Any]:
        async with self._writer_lease(
            kb_id,
            actor=approved_by,
            operation="validate_generation",
            ttl=timedelta(hours=2),
        ):
            return await self._validate_generation_under_lease(
                kb_id,
                generation_id,
                approved_by=approved_by,
                golden_runner=golden_runner,
            )

    async def _validate_generation_under_lease(
        self,
        kb_id: str,
        generation_id: str,
        *,
        approved_by: str | None = None,
        golden_runner: Any = None,
    ) -> dict[str, Any]:
        await self._require_kb(kb_id)
        generation = await self._generation_repo.get(generation_id)
        if generation is None or generation.knowledge_base_id != kb_id:
            raise AppError(
                AppErrorCode.generation_not_found, f"Generation 不存在: {generation_id}"
            )
        if generation.status not in (
            VectorIndexGenerationStatus.building,
            VectorIndexGenerationStatus.ready,
            VectorIndexGenerationStatus.validating,
        ):
            raise AppError(
                AppErrorCode.generation_invalid_state,
                f"Generation 状态为 {generation.status.value}，无法执行验收",
                status_code=409,
            )
        generation.status = VectorIndexGenerationStatus.validating
        await self._session.flush()

        from industrial_rag.services.generation_validation_service import (
            GenerationValidationService,
        )

        validator = GenerationValidationService(
            self._session,
            settings=self._settings,
            runtime_manager=self._runtime_manager,
            qdrant_client_factory=self._qdrant_client_factory,
        )
        report = await validator.validate(
            kb_id,
            generation,
            golden_runner=golden_runner,
            approved_by=approved_by,
        )
        operational_metrics.increment("validation_run_total")
        operational_metrics.increment(
            "validation_pass_total" if report["passed"] else "validation_fail_total"
        )
        job = await self._job_repo.find_by_candidate(generation_id)
        if report["passed"]:
            generation.status = VectorIndexGenerationStatus.ready
            if job is not None:
                job.result = {**(job.result or {}), "validation": report}
                await self._job_repo.mark_ready(job.id)
        else:
            generation.status = VectorIndexGenerationStatus.failed
            generation.last_error = "validation failed: " + "; ".join(
                gate for gate, ok in report["gates"].items() if not ok
            )
            if job is not None:
                await self._job_repo.mark_failed(
                    job.id,
                    error_code="generation_validation_failed",
                    sanitized_error_message="validation failed",
                )
                if job.operation in {UpdateOperation.add, UpdateOperation.replace} and job.document_id:
                    failed_document = await self._doc_repo.get(job.document_id)
                    if failed_document is not None:
                        failed_document.status = DocumentStatus.failed
                        failed_document.is_active = False
        await self._session.commit()
        return {
            "generation_id": generation_id,
            "knowledge_base_id": kb_id,
            "passed": report["passed"],
            "gates": report["gates"],
            "report": report,
        }

    async def promote_generation(
        self,
        kb_id: str,
        generation_id: str,
        *,
        approved_by: str | None = None,
    ) -> dict[str, Any]:
        async with self._writer_lease(
            kb_id,
            actor=approved_by,
            operation="promote_generation",
            ttl=timedelta(minutes=2),
        ) as lease:
            return await self._promote_generation_under_lease(
                kb_id,
                generation_id,
                approved_by=approved_by,
                lease=lease,
            )

    async def _promote_generation_under_lease(
        self,
        kb_id: str,
        generation_id: str,
        *,
        approved_by: str | None = None,
        lease,
    ) -> dict[str, Any]:
        await self._require_kb(kb_id)
        async with _kb_lock(kb_id):
            generation = await self._generation_repo.get(generation_id)
            if generation is None or generation.knowledge_base_id != kb_id:
                raise AppError(
                    AppErrorCode.generation_not_found,
                    f"Generation 不存在: {generation_id}",
                )
            active = await self._generation_repo.get_active(kb_id)
            if active is not None and active.id == generation.id:
                await self._session.commit()
                return {
                    "status": "already_active",
                    "idempotent": True,
                    "generation_id": generation_id,
                    "knowledge_base_id": kb_id,
                    "active_generation_id": generation_id,
                }
            if generation.status not in (
                VectorIndexGenerationStatus.ready,
            ):
                raise AppError(
                    AppErrorCode.generation_invalid_state,
                    f"只有验收通过的 Generation 才能发布（当前 {generation.status.value}）",
                    status_code=409,
                )
            job = await self._job_repo.find_by_candidate(generation_id)
            if job is None:
                raise AppError(
                    AppErrorCode.generation_invalid_state,
                    "Generation 没有关联的增量更新任务，无法发布",
                    status_code=409,
                )
            from industrial_rag.services.validation_gate_service import (
                ValidationGateService,
            )

            validation_run = await ValidationGateService(
                self._session,
                settings=self._settings,
                qdrant_client_factory=(
                    self._qdrant_client_factory or self._new_qdrant_client
                ),
            ).require_eligible(kb_id, generation)
            now = _utcnow()
            switched = await KBLeaseService(self._session).switch_active_generation(
                lease,
                target_generation_id=generation.id,
                expected_active_generation_id=active.id if active else None,
                target_workspace_path=generation.workspace_path,
                now=now,
            )
            if not switched:
                raise AppError(
                    AppErrorCode.concurrent_promote,
                    "Active Generation 已被其他实例修改。",
                    status_code=409,
                )
            await self._apply_document_state(job, active_now=True)
            await self._job_repo.mark_promoted(job.id, approved_by=approved_by)
            if self._runtime_manager is not None:
                await self._runtime_manager.close_runtime(kb_id)
            await self._session.commit()
            operational_metrics.increment("promote_total")
            return {
                "status": "promoted",
                "idempotent": False,
                "generation_id": generation_id,
                "knowledge_base_id": kb_id,
                "active_generation_id": generation_id,
                "previous_generation_id": active.id if active else None,
                "validation_run_id": validation_run.id,
            }

    async def rollback_generation(
        self,
        kb_id: str,
        target_generation_id: str,
        *,
        approved_by: str | None = None,
    ) -> dict[str, Any]:
        async with self._writer_lease(
            kb_id,
            actor=approved_by,
            operation="rollback_generation",
            ttl=timedelta(minutes=2),
        ) as lease:
            return await self._rollback_generation_under_lease(
                kb_id,
                target_generation_id,
                approved_by=approved_by,
                lease=lease,
            )

    async def _rollback_generation_under_lease(
        self,
        kb_id: str,
        target_generation_id: str,
        *,
        approved_by: str | None = None,
        lease,
    ) -> dict[str, Any]:
        await self._require_kb(kb_id)
        async with _kb_lock(kb_id):
            target = await self._generation_repo.get(target_generation_id)
            if target is None or target.knowledge_base_id != kb_id:
                raise AppError(
                    AppErrorCode.generation_not_found,
                    f"Generation 不存在: {target_generation_id}",
                )
            active = await self._generation_repo.get_active(kb_id)
            if active is not None and active.id == target.id:
                await self._session.commit()
                return {
                    "status": "already_active",
                    "idempotent": True,
                    "generation_id": target_generation_id,
                    "knowledge_base_id": kb_id,
                    "active_generation_id": target_generation_id,
                }
            if target.status not in (
                VectorIndexGenerationStatus.archived,
                VectorIndexGenerationStatus.ready,
            ):
                raise AppError(
                    AppErrorCode.generation_invalid_state,
                    f"只能回滚到验收通过的 archived/ready Generation（当前 {target.status.value}）",
                    status_code=409,
                )
            now = _utcnow()
            switched = await KBLeaseService(self._session).switch_active_generation(
                lease,
                target_generation_id=target.id,
                expected_active_generation_id=active.id if active else None,
                target_workspace_path=target.workspace_path,
                now=now,
                rollback=True,
            )
            if not switched:
                raise AppError(
                    AppErrorCode.concurrent_promote,
                    "Active Generation 已被其他实例修改。",
                    status_code=409,
                )
            job = await self._job_repo.find_by_candidate(target.id)
            if job is not None:
                await self._apply_document_state(job, active_now=True)
                job.status = UpdateJobStatus.rolled_back
                job.approved_by = approved_by or "api"
            if self._runtime_manager is not None:
                await self._runtime_manager.close_runtime(kb_id)
            await self._session.commit()
            operational_metrics.increment("rollback_total")
            return {
                "status": "rolled_back",
                "generation_id": target_generation_id,
                "knowledge_base_id": kb_id,
                "active_generation_id": target_generation_id,
                "previous_generation_id": active.id if active else None,
            }

    async def generation_diff(self, kb_id: str, generation_id: str) -> dict[str, Any]:
        await self._require_kb(kb_id)
        generation = await self._generation_repo.get(generation_id)
        if generation is None or generation.knowledge_base_id != kb_id:
            raise AppError(
                AppErrorCode.generation_not_found, f"Generation 不存在: {generation_id}"
            )
        active = await self._generation_repo.get_active(kb_id)
        job = await self._job_repo.find_by_candidate(generation_id)
        result: dict[str, Any] = {
            "knowledge_base_id": kb_id,
            "candidate_generation_id": generation_id,
            "candidate_generation": generation.generation,
            "active_generation_id": active.id if active else None,
            "active_generation": active.generation if active else None,
        }
        if job is not None:
            result["operation"] = job.operation.value
            result["document_id"] = job.document_id
            result["old_content_sha256"] = job.old_content_sha256
            result["new_content_sha256"] = job.new_content_sha256
            result["chunk_stats"] = (job.metrics or {}).get("chunk_stats")
            result["entity_relation_delta"] = (job.metrics or {}).get(
                "entity_relation_delta"
            )
            result["documents"] = (job.result or {}).get("documents") or []
        return result

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    async def get_job(self, kb_id: str, job_id: str) -> dict[str, Any]:
        await self._require_kb(kb_id)
        job = await self._job_repo.get_by_kb_and_id(kb_id, job_id)
        if job is None:
            raise AppError(AppErrorCode.update_job_not_found, f"更新任务不存在: {job_id}")
        return self._job_summary(job)

    async def resume_job(
        self,
        kb_id: str,
        job_id: str,
        *,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Resume an interrupted update job (pending/building with no ready
        candidate) after a service restart."""
        await self._require_kb(kb_id)
        job = await self._job_repo.get_by_kb_and_id(kb_id, job_id)
        if job is None:
            raise AppError(AppErrorCode.update_job_not_found, f"更新任务不存在: {job_id}")
        if job.status in (UpdateJobStatus.ready, UpdateJobStatus.promoted):
            return {"status": "already_complete", "job_id": job_id}
        if job.candidate_generation_id is not None:
            candidate = await self._generation_repo.get(job.candidate_generation_id)
            if candidate is not None and candidate.status in (
                VectorIndexGenerationStatus.ready,
                VectorIndexGenerationStatus.active,
            ):
                return {"status": "already_complete", "job_id": job_id}
        old_doc = None
        if job.operation == UpdateOperation.replace:
            replaced_id = (job.metrics or {}).get("replaced_document_id")
            if replaced_id:
                old_doc = await self._doc_repo.get(str(replaced_id))
        elif job.operation == UpdateOperation.delete and job.document_id:
            old_doc = await self._doc_repo.get(job.document_id)
        if job.candidate_generation_id is not None:
            candidate = await self._generation_repo.get(job.candidate_generation_id)
            if candidate is not None:
                candidate.status = VectorIndexGenerationStatus.failed
                candidate.last_error = "superseded by deterministic recovery rebuild"
        job.retry_count += 1
        job.status = UpdateJobStatus.recovery_required
        job.candidate_generation_id = None
        await self._session.commit()
        return await self._execute_persisted_job(
            kb_id,
            job.id,
            old_doc=old_doc,
            actor=actor,
        )

    async def cancel_job(
        self,
        kb_id: str,
        job_id: str,
        *,
        actor: str | None = None,
    ) -> dict[str, Any]:
        await self._require_kb(kb_id)
        async with self._writer_lease(
            kb_id,
            actor=actor,
            operation="cancel_update_job",
            ttl=timedelta(minutes=2),
        ):
            job = await self._job_repo.get_by_kb_and_id(kb_id, job_id)
            if job is None:
                raise AppError(AppErrorCode.update_job_not_found, "更新任务不存在。")
            if job.status == UpdateJobStatus.cancelled:
                return {"status": "cancelled", "job_id": job_id, "idempotent": True}
            if job.status in {UpdateJobStatus.promoted, UpdateJobStatus.rolled_back}:
                raise AppError(
                    AppErrorCode.invalid_state_transition,
                    "已发布或已回滚的任务不能取消。",
                    status_code=409,
                )
            job.status = UpdateJobStatus.cancelled
            job.current_stage = "cancelled"
            job.approved_by = actor or "admin:local-dev"
            job.finished_at = _utcnow()
            if job.candidate_generation_id:
                candidate = await self._generation_repo.get(job.candidate_generation_id)
                if candidate is not None and candidate.status != VectorIndexGenerationStatus.active:
                    candidate.status = VectorIndexGenerationStatus.failed
                    candidate.last_error = "update job cancelled; retained for GC"
            if job.operation in {UpdateOperation.add, UpdateOperation.replace} and job.document_id:
                cancelled_document = await self._doc_repo.get(job.document_id)
                if cancelled_document is not None:
                    cancelled_document.status = DocumentStatus.failed
                    cancelled_document.is_active = False
            await self._session.commit()
            return {"status": "cancelled", "job_id": job_id, "idempotent": False}

    async def list_jobs(self, kb_id: str) -> list[dict[str, Any]]:
        await self._require_kb(kb_id)
        jobs = await self._job_repo.list_by_kb(kb_id)
        return [self._job_summary(j) for j in jobs]

    # ------------------------------------------------------------------
    # Candidate build
    # ------------------------------------------------------------------

    async def _execute_persisted_job(
        self,
        kb_id: str,
        job_id: str,
        *,
        old_doc: Any | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        job = await self._job_repo.get(job_id)
        if job is None:
            raise AppError(AppErrorCode.update_job_not_found, "更新任务不存在。")
        worker_id = f"sync:{actor or job.created_by}"[:100]
        async with self._writer_lease(
            kb_id,
            actor=worker_id,
            operation="update_job",
            ttl=timedelta(hours=2),
        ) as lease:
            claimed = await self._job_repo.claim_specific(
                job_id,
                worker_id=worker_id,
                lease_token=lease.lease_token,
                fencing_token=lease.fencing_token,
                now=_utcnow(),
                lease_expires_at=lease.expires_at,
            )
            if claimed is None:
                raise AppError(
                    AppErrorCode.invalid_state_transition,
                    "更新任务无法被当前 worker 领取。",
                    status_code=409,
                )
            result = await self._run_job(kb_id, job_id, old_doc=old_doc)
            if not await KBLeaseService(self._session).is_current(lease, now=_utcnow()):
                await self._session.rollback()
                raise AppError(
                    AppErrorCode.knowledge_base_busy,
                    "更新任务租约已失效，结果未提交。",
                    status_code=409,
                )
            await self._session.commit()
            return result

    async def _run_job(
        self,
        kb_id: str,
        job_id: str,
        *,
        old_doc: Any | None = None,
    ) -> dict[str, Any]:
        """Build the candidate generation for one update job."""
        job = await self._job_repo.get(job_id)
        if job is None:
            raise RuntimeError(f"Update job {job_id} not found")
        kb = await self._kb_repo.get(kb_id)
        if kb is None:
            raise RuntimeError(f"Knowledge base {kb_id} not found")
        started = time.perf_counter()
        try:
            active = await self._generation_repo.get_active(kb_id)
            token = f"g{secrets.token_hex(12)}"
            candidate_workspace = kb_qdrant_generation_workspace(kb_id, token)
            collections = CollectionNameResolver(
                self._settings.qdrant_collection_prefix
            ).names_for(kb_id=kb_id, generation=token)
            generation = await self._generation_repo.create_shadow(
                knowledge_base_id=kb_id,
                backend=VectorBackend.qdrant.value,
                generation=token,
                workspace_path=str(candidate_workspace),
                collections=collections,
                document_manifest_hash="",
                child_chunks_manifest_hash=hashlib.sha256(
                    f"{job_id}:{token}".encode()
                ).hexdigest(),
                embedding_config_hash="",
                chunking_config_hash="",
            )
            generation.status = VectorIndexGenerationStatus.building
            await self._job_repo.mark_building(job_id, generation.id)
            await self._session.flush()
            # Candidate collections are always created up front (even when the
            # KB has no active generation yet) so namespaces are stable.
            ensure_client = self._new_qdrant_client()
            try:
                await self._ensure_collections(ensure_client, collections)
            finally:
                await ensure_client.close()

            stats = {
                "added_chunks": 0,
                "reused_chunks": 0,
                "invalidated_chunks": 0,
            }
            t_parse = t_embed = t_graph = 0.0

            # 1. Inherit unchanged content from the active generation.
            if active is not None:
                t0 = time.perf_counter()
                reused = await self._inherit_active(
                    kb_id, active, generation, candidate_workspace
                )
                stats["reused_chunks"] = reused
                t_graph += time.perf_counter() - t0

            # 2. Process the changed document.
            doc = (
                await self._doc_repo.get(job.document_id)
                if job.document_id is not None
                else None
            )
            removed_internal_ids: list[str] = []
            if job.operation in (UpdateOperation.add, UpdateOperation.replace) and doc is not None:
                t0 = time.perf_counter()
                await self._parse_document_pymupdf(kb, doc)
                t_parse += time.perf_counter() - t0
                t0 = time.perf_counter()
                added = await self._ingest_document(kb, generation, candidate_workspace, doc)
                stats["added_chunks"] = added
                t_embed += time.perf_counter() - t0

            if (
                job.operation in (UpdateOperation.replace, UpdateOperation.delete)
                and old_doc is not None
            ):
                t0 = time.perf_counter()
                removed_internal_ids = await self._remove_document_points(
                    kb_id, generation, candidate_workspace, old_doc
                )
                stats["invalidated_chunks"] = len(removed_internal_ids)
                t_graph += time.perf_counter() - t0

            # Final consistency sweep: entity/relation references may use
            # different internal id encodings depending on the LightRAG build;
            # keep only references to chunks that exist in the candidate's
            # text-chunk store, removing any orphaned references precisely.
            self._sweep_orphan_references(candidate_workspace, generation)

            # 3. Counts (entity/relation delta).
            entity_relation_delta = await self._count_delta(
                kb_id, active, generation
            )
            metrics = {
                "replaced_document_id": old_doc.id
                if job.operation == UpdateOperation.replace and old_doc is not None
                else None,
                "latencies": {
                    "parse_seconds": round(t_parse, 3),
                    "embedding_seconds": round(t_embed, 3),
                    "graph_seconds": round(t_graph, 3),
                    "validation_seconds": 0.0,
                    "total_seconds": round(time.perf_counter() - started, 3),
                },
                "chunk_stats": stats,
                "entity_relation_delta": entity_relation_delta,
            }
            job.metrics = metrics
            job.result = {
                "documents": await self._document_state_snapshot(kb_id, job),
                "candidate_generation": token,
            }
            generation.document_manifest_hash = self._manifest_hash(
                await self._doc_repo.list_active_for_kb(kb_id)
            )
            await self._session.flush()
            return {
                "status": "candidate_built",
                "knowledge_base_id": kb_id,
                "job_id": job_id,
                "document_id": job.document_id,
                "operation": job.operation.value,
                "candidate_generation_id": generation.id,
                "candidate_generation": token,
                "metrics": metrics,
            }
        except Exception as error:
            if job.operation in {UpdateOperation.add, UpdateOperation.replace} and job.document_id:
                failed_document = await self._doc_repo.get(job.document_id)
                if failed_document is not None:
                    failed_document.status = DocumentStatus.failed
                    failed_document.is_active = False
            await self._job_repo.mark_failed(
                job_id,
                error_code="build_failed",
                sanitized_error_message=_sanitize_error(error),
            )
            await self._session.commit()
            raise

    async def _inherit_active(
        self,
        kb_id: str,
        active: Any,
        candidate: Any,
        candidate_workspace: Path,
    ) -> int:
        """Copy the active workspace and all Qdrant points into the candidate.

        Vectors are copied point-by-point (same deterministic point ids), so
        unchanged chunks are never re-embedded.  Provenance fields are
        injected into every copied point payload.
        """
        active_workspace = Path(active.workspace_path)
        if active_workspace.is_dir() and active_workspace.exists():
            if candidate_workspace.exists():
                shutil.rmtree(candidate_workspace, ignore_errors=True)
            candidate_workspace.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(active_workspace, candidate_workspace)
        else:
            candidate_workspace.mkdir(parents=True, exist_ok=True)
        # The inherited workspace storage lives under the active generation's
        # token directory; re-home it to the candidate token so LightRAG loads
        # the copied kv_store files under the new generation identity.
        active_token_dir = candidate_workspace / f"qdrant-{active.generation}"
        candidate_token_dir = candidate_workspace / f"qdrant-{candidate.generation}"
        if active_token_dir.is_dir() and not candidate_token_dir.exists():
            active_token_dir.rename(candidate_token_dir)

        reused = 0
        if active.collections and active.status not in (
            VectorIndexGenerationStatus.failed,
            VectorIndexGenerationStatus.deleted,
        ):
            client = self._new_qdrant_client()
            try:
                await self._ensure_collections(client, candidate.collections)
                for namespace in ("chunks", "entities", "relationships"):
                    src = active.collections.get(namespace)
                    dst = candidate.collections[namespace]
                    if not src or not await client.collection_exists(src):
                        continue
                    offset = None
                    while True:
                        records, next_offset = await client.scroll(
                            collection_name=src,
                            limit=1000,
                            with_payload=True,
                            with_vectors=True,
                            offset=offset,
                        )
                        points = [
                            models.PointStruct(
                                id=record.id,
                                vector=record.vector,
                                payload={
                                    **(record.payload or {}),
                                    "kb_id": kb_id,
                                    "generation": candidate.generation,
                                },
                            )
                            for record in records
                        ]
                        if points:
                            await client.upsert(dst, points, wait=True)
                            if namespace == "chunks":
                                reused += len(points)
                        if next_offset is None:
                            break
                        offset = next_offset
            finally:
                await client.close()
        return reused

    async def _parse_document_pymupdf(self, kb: Any, doc: Any) -> dict[str, Any]:
        from industrial_rag.document_parser import parse_pdf
        from industrial_rag.structured_chunker import (
            ChunkerConfig,
            build_parent_child_chunks,
            pymupdf_chunks_to_blocks,
        )

        pdf_path = Path(doc.file_path)
        if not pdf_path.is_file():
            raise RuntimeError(f"Source PDF not found: {pdf_path}")
        source_chunks = parse_pdf(pdf_path)
        if not source_chunks:
            raise RuntimeError("PDF 解析未产生任何块")
        blocks = pymupdf_chunks_to_blocks(source_chunks, doc.original_file_name)
        cfg = ChunkerConfig(strategy="pymupdf-v1")
        parents, children = build_parent_child_chunks(
            blocks, doc.original_file_name, config=cfg
        )
        if not children:
            raise RuntimeError("未能生成 ChildChunk")
        parsed_doc_dir = kb_parsed_documents_dir(kb.id) / doc.id
        current_dir = parsed_doc_dir / "current"
        current_dir.mkdir(parents=True, exist_ok=True)
        with (current_dir / "child_chunks.jsonl").open(
            "w", encoding="utf-8", newline="\n"
        ) as f:
            for child in children:
                d = child.to_dict() if hasattr(child, "to_dict") else child.__dict__
                f.write(
                    json.dumps(
                        {
                            k: (v.value if hasattr(v, "value") else v)
                            for k, v in d.items()
                            if not k.startswith("_")
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    )
                    + "\n"
                )
        with (current_dir / "parent_chunks.jsonl").open(
            "w", encoding="utf-8", newline="\n"
        ) as f:
            for p in parents:
                f.write(
                    json.dumps(
                        {
                            "parent_chunk_id": p.parent_chunk_id,
                            "document_id": p.document_id,
                            "document_name": p.document_name,
                            "page_start": p.page_start,
                            "page_end": p.page_end,
                            "section_path": list(p.section_path),
                            "section_title": p.section_title,
                            "content": p.content,
                            "child_chunk_ids": list(p.child_chunk_ids),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    )
                    + "\n"
                )
        await self._doc_repo.update(
            doc.id,
            page_count=max((c.page_start or 1) for c in children),
            parent_chunk_count=len(parents),
            child_chunk_count=len(children),
            parse_status="done",
            status=DocumentStatus.parsed,
            parser_version="1.28.0",
        )
        return {"child_chunk_count": len(children), "parent_chunk_count": len(parents)}

    async def _ingest_document(
        self,
        kb: Any,
        generation: Any,
        candidate_workspace: Path,
        doc: Any,
    ) -> int:
        from industrial_rag.citation_formatter import Citation, encode_chunk_header
        from industrial_rag.lightrag_service import LightRAGService
        from industrial_rag.services.parse_service import load_child_chunks

        children = load_child_chunks(kb_parsed_documents_dir(kb.id) / doc.id)
        if not children:
            raise RuntimeError(f"No child chunks for document {doc.id}")
        settings = settings_for_knowledge_base(
            self._settings,
            kb,
            backend=VectorBackend.qdrant,
            generation=generation.generation,
            working_dir=candidate_workspace,
        )
        boundary = "\n\n<<<INDUSTRIAL_RAG_CHUNK_BOUNDARY>>>\n\n"
        parts = [
            (
                f"{encode_chunk_header(Citation(doc.original_file_name, child.page_start or 1, child.chunk_id))}\n"
                f"[来源：{doc.original_file_name}，第{child.page_start or 1}页，"
                f"章节：{child.section_title or '未识别章节'}]\n"
                f"[parent_chunk_id：{child.parent_chunk_id}]\n"
                f"{child.embedding_content or child.content}"
            )
            for child in children
        ]
        identity = hashlib.sha256(
            "\n".join(child.chunk_id for child in children).encode("utf-8")
        ).hexdigest()[:20]
        service = (
            self._lightrag_service_factory(settings)
            if self._lightrag_service_factory is not None
            else LightRAGService(settings)
        )
        await service.initialize()
        try:
            await service._backend.ainsert(
                input=[boundary.join(parts)],
                ids=[f"kb-{identity}"],
                file_paths=[doc.original_file_name],
                split_by_character=boundary,
                split_by_character_only=True,
            )
        finally:
            await service.close()
        await self._doc_repo.update(
            doc.id,
            index_status="done",
            status=DocumentStatus.indexed,
            indexed_at=_utcnow(),
        )
        return len(children)

    async def _remove_document_points(
        self,
        kb_id: str,
        generation: Any,
        candidate_workspace: Path,
        doc: Any,
    ) -> list[str]:
        from industrial_rag.services.parse_service import load_child_chunks

        children = load_child_chunks(kb_parsed_documents_dir(kb_id) / doc.id)
        identity = ""
        internal_ids: list[str] = []
        if children:
            identity = hashlib.sha256(
                "\n".join(child.chunk_id for child in children).encode("utf-8")
            ).hexdigest()[:20]
            # LightRAG formats chunk ordinals with three-digit zero padding.
            internal_ids = [
                f"kb-{identity}-chunk-{i:03d}" for i in range(len(children))
            ]
        elif doc.child_chunk_count:
            # Fallback for old parsed docs with a recorded count but no artifacts.
            # Recompute the identity from the stored file hash is not possible,
            # so this path is intentionally conservative: nothing is removed
            # blindly; validation will flag stale content if any remains.
            internal_ids = []
        removed = set(internal_ids)
        if not removed:
            return []

        client = self._new_qdrant_client()
        try:
            names = generation.collections or {}
            storage_root = (
                candidate_workspace / f"qdrant-{generation.generation}"
                if (candidate_workspace / f"qdrant-{generation.generation}").is_dir()
                else candidate_workspace
            )
            await self._purge_workspace_references(
                storage_root,
                removed,
                internal_doc_id=f"kb-{identity}",
            )
            if names.get("chunks"):
                await client.delete(
                    names["chunks"],
                    models.PointIdsList(
                        points=[_point_id(i) for i in sorted(removed)]
                    ),
                    wait=True,
                )
            # Entity/relation points reference chunks through a ``<SEP>``-joined
            # ``source_id`` payload.  Remove only the invalidated chunk from the
            # reference; drop the point entirely when nothing remains.
            for namespace in ("entities", "relationships"):
                collection = names.get(namespace)
                if not collection:
                    continue
                offset = None
                while True:
                    records, next_offset = await client.scroll(
                        collection_name=collection,
                        limit=1000,
                        with_payload=True,
                        with_vectors=True,
                        offset=offset,
                    )
                    point_ids: list[str] = []
                    updates: list[models.PointStruct] = []
                    for record in records:
                        payload = dict(record.payload or {})
                        source = str(payload.get("source_id") or "")
                        parts = [part for part in source.split("<SEP>") if part]
                        remaining = [part for part in parts if part not in removed]
                        if len(remaining) == len(parts):
                            continue
                        if not remaining:
                            point_ids.append(record.id)
                            continue
                        payload["source_id"] = "<SEP>".join(remaining)
                        file_parts = str(payload.get("file_path") or "").split("<SEP>")
                        payload["file_path"] = "<SEP>".join(
                            part for part in file_parts if part and part != doc.original_file_name
                        )
                        updates.append(
                            models.PointStruct(
                                id=record.id,
                                vector=record.vector,
                                payload=payload,
                            )
                        )
                    if point_ids:
                        await client.delete(
                            collection,
                            models.PointIdsList(points=point_ids),
                            wait=True,
                        )
                    if updates:
                        await client.upsert(collection, updates, wait=True)
                    if next_offset is None:
                        break
                    offset = next_offset
        finally:
            await client.close()
        return list(removed)

    async def _purge_workspace_references(
        self,
        storage_root: Path,
        removed_chunk_ids: set[str],
        *,
        internal_doc_id: str,
    ) -> None:
        """Remove deleted chunk/doc/entity/relation entries from candidate kv_store."""
        for name in (
            "kv_store_text_chunks.json",
            "kv_store_entity_chunks.json",
            "kv_store_relation_chunks.json",
            "kv_store_full_docs.json",
            "kv_store_full_entities.json",
            "kv_store_full_relations.json",
            "kv_store_doc_status.json",
        ):
            path = storage_root / name
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if name == "kv_store_text_chunks.json":
                data = {k: v for k, v in data.items() if k not in removed_chunk_ids}
            elif name in (
                "kv_store_entity_chunks.json",
                "kv_store_relation_chunks.json",
            ):
                data = {
                    k: v
                    for k, v in data.items()
                    if not (
                        isinstance(v, dict)
                        and set(v.get("chunk_ids") or []) & removed_chunk_ids
                    )
                }
            elif name in (
                "kv_store_full_docs.json",
                "kv_store_doc_status.json",
                "kv_store_full_entities.json",
                "kv_store_full_relations.json",
            ):
                data = {k: v for k, v in data.items() if k != internal_doc_id}
            path.write_text(
                json.dumps(data, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )

    def _sweep_orphan_references(self, candidate_workspace: Path, generation: Any) -> None:
        """Drop entity/relation references to chunks absent from the candidate
        text-chunk store (keeps the graph consistent after replace/delete)."""
        storage_root = (
            candidate_workspace / f"qdrant-{generation.generation}"
            if (candidate_workspace / f"qdrant-{generation.generation}").is_dir()
            else candidate_workspace
        )
        text_path = storage_root / "kv_store_text_chunks.json"
        if not text_path.is_file():
            return
        try:
            text_keys = set(json.loads(text_path.read_text(encoding="utf-8")).keys())
        except Exception:
            return
        for name in (
            "kv_store_entity_chunks.json",
            "kv_store_relation_chunks.json",
        ):
            path = storage_root / name
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            kept = {
                key: value
                for key, value in data.items()
                if isinstance(value, dict)
                and set(value.get("chunk_ids") or []) <= text_keys
            }
            if len(kept) != len(data):
                path.write_text(
                    json.dumps(kept, ensure_ascii=False, sort_keys=True),
                    encoding="utf-8",
                )
        # The copied graph was built from the replaced document's state.  Drop
        # it so the next runtime rebuilds the graph from the corrected Qdrant
        # entity/relation points (whose source_id references were updated to
        # the candidate generation only).
        graph = storage_root / "graph_chunk_entity_relation.graphml"
        if graph.is_file():
            try:
                graph.unlink()
            except OSError:
                pass

    async def _count_delta(
        self,
        kb_id: str,
        active: Any,
        candidate: Any,
    ) -> dict[str, Any]:
        client = self._new_qdrant_client()
        try:
            async def counts(names: dict[str, str] | None) -> dict[str, int]:
                out = {}
                for ns in ("chunks", "entities", "relationships"):
                    if names and names.get(ns) and await client.collection_exists(names[ns]):
                        out[ns] = (await client.count(names[ns], exact=True)).count
                    else:
                        out[ns] = 0
                return out

            active_counts = await counts(active.collections if active else None)
            candidate_counts = await counts(candidate.collections)
            return {
                "active": active_counts,
                "candidate": candidate_counts,
                "delta": {
                    ns: candidate_counts[ns] - active_counts[ns]
                    for ns in ("chunks", "entities", "relationships")
                },
            }
        finally:
            await client.close()

    async def _ensure_collections(
        self, client: AsyncQdrantClient, names: dict[str, str]
    ) -> None:
        for name in names.values():
            if await client.collection_exists(name):
                continue
            await client.create_collection(
                collection_name=name,
                vectors_config=models.VectorParams(
                    size=self._settings.embedding_dim,
                    distance=models.Distance.COSINE,
                ),
            )

    def _new_qdrant_client(self) -> AsyncQdrantClient:
        if self._qdrant_client_factory is not None:
            return self._qdrant_client_factory()
        return AsyncQdrantClient(
            url=self._settings.qdrant_url, api_key=self._settings.qdrant_api_key
        )

    # ------------------------------------------------------------------
    # Document state helpers
    # ------------------------------------------------------------------

    async def _apply_document_state(self, job: Any, *, active_now: bool) -> None:
        """Reflect the promoted/rolled-back generation's document set in DB rows."""
        snapshot = (job.result or {}).get("documents") or []
        active_ids = {
            entry["document_id"]
            for entry in snapshot
            if entry.get("is_active") and entry.get("document_id")
        }
        docs = await self._doc_repo.list_by_kb(job.knowledge_base_id, include_deleted=True)
        for doc in docs:
            await self._doc_repo.update(
                doc.id,
                is_active=doc.id in active_ids,
                status=(
                    DocumentStatus.indexed
                    if doc.id in active_ids
                    else DocumentStatus.deleted
                ),
            )

    async def _document_state_snapshot(
        self, kb_id: str, job: Any
    ) -> list[dict[str, Any]]:
        docs = await self._doc_repo.list_by_kb(kb_id, include_deleted=True)
        rows: list[dict[str, Any]] = []
        for d in docs:
            effective_active = d.is_active and d.status != DocumentStatus.deleted
            if job.operation == UpdateOperation.delete and d.id == job.document_id:
                effective_active = False
            if job.operation == UpdateOperation.replace and d.id == job.document_id:
                effective_active = True
            replaced_id = (job.metrics or {}).get("replaced_document_id")
            if (
                job.operation == UpdateOperation.replace
                and replaced_id
                and d.id == replaced_id
            ):
                effective_active = False
            rows.append(
                {
                "document_id": d.id,
                "logical_name": d.logical_name or d.original_file_name,
                "version": d.version,
                "content_sha256": d.file_hash,
                "is_active": effective_active,
            }
            )
        return rows

    def _manifest_hash(self, docs: list[Any]) -> str:
        payload = [
            f"{d.id}:{d.file_hash}:{d.version}:{d.is_active}"
            for d in sorted(docs, key=lambda d: d.id)
        ]
        return hashlib.sha256("\n".join(payload).encode("utf-8")).hexdigest()

    async def _resolve_active_version(self, kb_id: str, doc: Any) -> Any:
        """Return the active row of the same logical document (by identity)."""
        from sqlalchemy import select

        from industrial_rag.db.models import Document

        logical = doc.logical_name or doc.original_file_name
        statement = (
            select(Document)
            .where(
                Document.knowledge_base_id == kb_id,
                Document.is_active == True,  # noqa: E712
                Document.status != DocumentStatus.deleted,
                (
                    (Document.logical_name == logical)
                    | (Document.original_file_name == logical)
                ),
            )
            .order_by(Document.version.desc())
            .limit(1)
        )
        result = await self._session.execute(statement)
        return result.scalars().first() or doc

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    async def _require_kb(self, kb_id: str) -> Any:
        kb = await self._kb_repo.get(kb_id)
        if kb is None:
            raise AppError(AppErrorCode.knowledge_base_not_found, f"知识库不存在: {kb_id}")
        if kb.status in (KBStatus.deleting, KBStatus.deleted):
            raise AppError(
                AppErrorCode.invalid_state_transition,
                "知识库正在删除或已删除",
                status_code=409,
            )
        return kb

    def _validate_file(self, file_name: str, content: bytes) -> tuple[str, str]:
        ext = Path(file_name).suffix.lower()
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
        return hashlib.sha256(content).hexdigest(), ext

    def _generation_summary(self, generation: Any) -> dict[str, Any]:
        return {
            "id": generation.id,
            "knowledge_base_id": generation.knowledge_base_id,
            "generation": generation.generation,
            "status": generation.status.value,
            "backend": generation.backend,
            "collections": generation.collections,
            "created_at": generation.created_at.isoformat() if generation.created_at else None,
            "activated_at": generation.activated_at.isoformat() if generation.activated_at else None,
            "last_error": generation.last_error,
        }

    def _job_summary(self, job: Any) -> dict[str, Any]:
        return {
            "job_id": job.id,
            "knowledge_base_id": job.knowledge_base_id,
            "base_generation_id": job.base_generation_id,
            "candidate_generation_id": job.candidate_generation_id,
            "operation": job.operation.value,
            "document_id": job.document_id,
            "old_content_sha256": job.old_content_sha256,
            "new_content_sha256": job.new_content_sha256,
            "status": job.status.value,
            "current_stage": job.current_stage,
            "retry_count": job.retry_count,
            "error_code": job.error_code,
            "sanitized_error_message": job.sanitized_error_message,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "created_by": job.created_by,
            "approved_by": job.approved_by,
            "metrics": job.metrics,
            "result": job.result,
        }

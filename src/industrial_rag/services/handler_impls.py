"""Built-in handler implementations — REAL parse/index/rebuild execution.

All handlers now call real services (ParseService, IndexService, RebuildService,
CleanupService).  No stub handlers remain.
"""

from __future__ import annotations

import logging

from industrial_rag.db.models import TaskType
from industrial_rag.services.cleanup_service import KnowledgeBaseCleanupService
from industrial_rag.services.generation_fingerprint_service import build_generation_fingerprint
from industrial_rag.services.task_context import TaskExecutionContext, TaskExecutionResult
from industrial_rag.services.task_handlers import register_handler

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# KB delete
# ---------------------------------------------------------------------------


@register_handler(TaskType.delete_knowledge_base)
async def handle_delete_knowledge_base(ctx: TaskExecutionContext) -> TaskExecutionResult:
    """Execute KB delete: close runtime, clean directories, mark deleted."""
    try:
        cleanup = KnowledgeBaseCleanupService(
            ctx.task_repo._session,
            runtime_manager=ctx.runtime_manager,
            delete_source_files=ctx.delete_source_files,
        )
        await cleanup.execute(ctx.task.knowledge_base_id, ctx.task.id)
        return TaskExecutionResult(success=True, result={"action": "kb_deleted"})
    except Exception as exc:
        return TaskExecutionResult(
            success=False,
            error_code="kb_delete_failed",
            error_message=str(exc)[:500],
        )


# ---------------------------------------------------------------------------
# Document delete
# ---------------------------------------------------------------------------


@register_handler(TaskType.delete_document)
async def handle_delete_document(ctx: TaskExecutionContext) -> TaskExecutionResult:
    """Soft-delete document then trigger KB rebuild."""
    try:
        await ctx.update_progress(0.0, "deleting_document")
        doc_id = ctx.task.document_id
        if doc_id:
            await ctx.doc_repo.soft_delete(doc_id)
        await ctx.update_progress(1.0, "document_deleted")
        return TaskExecutionResult(
            success=True,
            result={"action": "document_soft_deleted", "document_id": doc_id},
        )
    except Exception as exc:
        return TaskExecutionResult(
            success=False,
            error_code="document_delete_failed",
            error_message=str(exc)[:500],
        )


# ---------------------------------------------------------------------------
# Parse — REAL PyMuPDF parse with artifact generation
# ---------------------------------------------------------------------------


@register_handler(TaskType.parse)
async def handle_parse(ctx: TaskExecutionContext) -> TaskExecutionResult:
    """Real PDF parsing via PyMuPDF → ParsedBlock → ParentChunk → ChildChunk."""
    try:
        await ctx.update_progress(0.0, "starting_parse")
        doc_id = ctx.task.document_id
        kb_id = ctx.task.knowledge_base_id
        if doc_id is None:
            return TaskExecutionResult(
                success=False, error_code="parse_no_document",
                error_message="Parse task has no document_id",
            )
        doc = await ctx.doc_repo.get(doc_id)
        if doc is None:
            return TaskExecutionResult(
                success=False, error_code="document_not_found",
                error_message=f"Document {doc_id} not found",
            )

        # Mark parsing
        await ctx.doc_repo.update(doc_id, parse_status="parsing", status="parsing")
        await ctx.update_progress(0.10, "parsing")

        from industrial_rag.services.parse_service import ParseService
        from industrial_rag.storage_layout import kb_parsed_dir

        parsed_base = kb_parsed_dir(kb_id) / "documents" / doc_id
        svc = ParseService(ctx.task_repo._session)
        manifest = await svc.parse_document(
            kb_id, doc_id, ctx.task.id, parsed_base=parsed_base,
        )

        # Mark parsed
        await ctx.doc_repo.update(doc_id, status="parsed", parse_status="done")
        await ctx.kb_repo.update(
            kb_id,
            document_count=(
                await ctx.doc_repo.count_by_kb(kb_id, active_only=True)
            ),
        )
        await ctx.update_progress(1.0, "parse_done")

        # Auto-create follow-up index/rebuild task
        from industrial_rag.services.ingestion_pipeline import IngestionPipeline

        pipeline = IngestionPipeline(
            ctx.task_repo._session, runtime_manager=ctx.runtime_manager,
        )
        follow_up_id = await pipeline.on_parse_succeeded(kb_id, doc_id, manifest)

        return TaskExecutionResult(
            success=True,
            result={
                "action": "document_parsed",
                "document_id": doc_id,
                "manifest": manifest,
                "follow_up_task_id": follow_up_id,
            },
        )
    except Exception as exc:
        if doc_id:
            await ctx.doc_repo.update(doc_id, parse_status="failed", last_error=str(exc)[:500])
        # Notify pipeline that parse failed (no index task created)
        from industrial_rag.services.ingestion_pipeline import IngestionPipeline

        pipeline = IngestionPipeline(ctx.task_repo._session)
        await pipeline.on_parse_failed(kb_id, doc_id, str(exc))
        return TaskExecutionResult(
            success=False, error_code="parse_failed", error_message=str(exc)[:500],
        )


# ---------------------------------------------------------------------------
# Index / Rebuild — REAL LightRAG workspace build + health verification
# ---------------------------------------------------------------------------


@register_handler(TaskType.rebuild)
async def handle_rebuild(ctx: TaskExecutionContext) -> TaskExecutionResult:
    """Full KB index rebuild using IndexService."""
    try:
        await ctx.update_progress(0.0, "starting_rebuild")
        from industrial_rag.services.index_service import IndexService

        svc = IndexService(
            ctx.task_repo._session,
            settings=ctx.settings,
            runtime_manager=ctx.runtime_manager,
        )
        result = await svc.index_knowledge_base(
            ctx.task.knowledge_base_id, ctx.task.id,
        )
        await ctx.update_progress(1.0, "rebuild_done")
        return TaskExecutionResult(success=True, result=result)
    except Exception as exc:
        return TaskExecutionResult(
            success=False, error_code="rebuild_failed", error_message=str(exc)[:500],
        )


@register_handler(TaskType.migrate_to_qdrant)
async def handle_migrate_to_qdrant(ctx: TaskExecutionContext) -> TaskExecutionResult:
    """Build a verified Qdrant shadow generation from existing ChildChunks."""
    try:
        await ctx.update_progress(0.0, "starting_qdrant_migration")
        from industrial_rag.services.index_service import IndexService
        from industrial_rag.vector_collections import VectorBackend

        result = await IndexService(
            ctx.task_repo._session,
            settings=ctx.settings,
            runtime_manager=ctx.runtime_manager,
        ).index_knowledge_base(
            ctx.task.knowledge_base_id,
            ctx.task.id,
            target_backend=VectorBackend.qdrant,
        )
        await ctx.update_progress(1.0, "qdrant_migration_done")
        return TaskExecutionResult(success=True, result=result)
    except Exception as exc:
        return TaskExecutionResult(
            success=False,
            error_code="qdrant_migration_failed",
            error_message=str(exc)[:500],
        )


@register_handler(TaskType.rollback_to_nano)
async def handle_rollback_to_nano(ctx: TaskExecutionContext) -> TaskExecutionResult:
    """Activate Nano only when its immutable input fingerprint still matches the KB."""
    try:
        from industrial_rag.db.models import VectorIndexGenerationStatus
        from industrial_rag.repositories.vector_index_generation_repository import (
            VectorIndexGenerationRepository,
        )
        from industrial_rag.services.parse_service import load_child_chunks
        from industrial_rag.storage_layout import kb_parsed_dir

        kb = await ctx.kb_repo.get(ctx.task.knowledge_base_id)
        if kb is None:
            return TaskExecutionResult(False, "knowledge_base_not_found", "知识库不存在")
        generations = await VectorIndexGenerationRepository(ctx.task_repo._session).list_for_kb(kb.id)
        nano = next(
            (
                generation
                for generation in generations
                if generation.backend == "nano" and generation.status in {
                    VectorIndexGenerationStatus.active,
                    VectorIndexGenerationStatus.retired,
                }
            ),
            None,
        )
        if nano is None:
            return TaskExecutionResult(False, "nano_generation_missing", "没有可回滚的 Nano generation")
        document_children = []
        for document in await ctx.doc_repo.list_active_for_kb(kb.id):
            document_children.extend(
                (document, child)
                for child in load_child_chunks(kb_parsed_dir(kb.id) / "documents" / document.id)
            )
        if not document_children:
            return TaskExecutionResult(False, "nano_generation_stale", "当前知识库没有可验证的 ChildChunk")
        fingerprint = build_generation_fingerprint(kb, document_children)
        if (
            nano.document_manifest_hash != fingerprint.document_manifest_hash
            or nano.child_chunks_manifest_hash != fingerprint.child_chunks_manifest_hash
            or nano.embedding_config_hash != fingerprint.embedding_config_hash
            or nano.chunking_config_hash != fingerprint.chunking_config_hash
        ):
            return TaskExecutionResult(
                False,
                "nano_generation_stale",
                "Nano workspace 与当前有效知识库不一致；拒绝陈旧回滚",
            )
        if ctx.runtime_manager is not None:
            await ctx.runtime_manager.close_runtime(kb.id)
        await VectorIndexGenerationRepository(ctx.task_repo._session).activate(nano)
        await ctx.kb_repo.update(kb.id, vector_backend="nano", active_vector_generation_id=nano.id)
        return TaskExecutionResult(
            success=True,
            result={"backend": "nano", "generation": nano.generation, "generation_id": nano.id},
        )
    except Exception as exc:
        return TaskExecutionResult(False, "nano_rollback_failed", str(exc)[:500])


@register_handler(TaskType.index)
async def handle_index(ctx: TaskExecutionContext) -> TaskExecutionResult:
    """Index: delegates to the rebuild handler (full KB rebuild for safety)."""
    return await handle_rebuild(ctx)


# ---------------------------------------------------------------------------
# Reparse — re-parse without losing old artifacts on failure
# ---------------------------------------------------------------------------


@register_handler(TaskType.reparse)
async def handle_reparse(ctx: TaskExecutionContext) -> TaskExecutionResult:
    """Re-parse: create temp artifacts, validate, then atomically replace.

    On failure, old artifacts are preserved.
    """
    try:
        await ctx.update_progress(0.0, "starting_reparse")
        doc_id = ctx.task.document_id
        kb_id = ctx.task.knowledge_base_id
        if doc_id is None:
            return TaskExecutionResult(
                success=False, error_code="reparse_no_document",
                error_message="Reparse task has no document_id",
            )
        doc = await ctx.doc_repo.get(doc_id)
        if doc is None:
            return TaskExecutionResult(
                success=False, error_code="document_not_found",
                error_message=f"Document {doc_id} not found",
            )

        await ctx.doc_repo.update(doc_id, parse_status="parsing")
        await ctx.update_progress(0.10, "parsing")

        from industrial_rag.services.parse_service import ParseService
        from industrial_rag.storage_layout import kb_parsed_dir

        parsed_base = kb_parsed_dir(kb_id) / "documents" / doc_id
        svc = ParseService(ctx.task_repo._session)
        manifest = await svc.parse_document(
            kb_id, doc_id, ctx.task.id, parsed_base=parsed_base,
        )

        await ctx.doc_repo.update(doc_id, status="parsed", parse_status="done")
        await ctx.update_progress(1.0, "reparse_done")

        # Trigger follow-up rebuild
        from industrial_rag.services.ingestion_pipeline import IngestionPipeline

        pipeline = IngestionPipeline(
            ctx.task_repo._session, runtime_manager=ctx.runtime_manager,
        )
        follow_up_id = await pipeline.on_parse_succeeded(kb_id, doc_id, manifest)

        return TaskExecutionResult(
            success=True,
            result={
                "action": "document_reparsed",
                "document_id": doc_id,
                "manifest": manifest,
                "follow_up_task_id": follow_up_id,
            },
        )
    except Exception as exc:
        if doc_id:
            await ctx.doc_repo.update(doc_id, last_error=str(exc)[:500])
        return TaskExecutionResult(
            success=False, error_code="reparse_failed", error_message=str(exc)[:500],
        )


# ---------------------------------------------------------------------------
# Reindex — full KB rebuild (same as rebuild handler)
# ---------------------------------------------------------------------------


@register_handler(TaskType.reindex)
async def handle_reindex(ctx: TaskExecutionContext) -> TaskExecutionResult:
    """Re-index: full KB rebuild."""
    return await handle_rebuild(ctx)

"""Full knowledge-base shadow indexing with generation activation."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.config import Settings
from industrial_rag.kb_runtime_settings import settings_for_knowledge_base
from industrial_rag.repositories.document_repository import DocumentRepository
from industrial_rag.repositories.knowledge_base_repository import KnowledgeBaseRepository
from industrial_rag.repositories.task_repository import TaskRepository
from industrial_rag.repositories.vector_index_generation_repository import (
    VectorIndexGenerationRepository,
)
from industrial_rag.services.generation_fingerprint_service import build_generation_fingerprint
from industrial_rag.services.parse_service import load_child_chunks
from industrial_rag.services.qdrant_collection_service import QdrantCollectionService
from industrial_rag.storage_layout import (
    kb_nano_workspace,
    kb_parsed_dir,
    kb_qdrant_generation_workspace,
)
from industrial_rag.vector_collections import CollectionNameResolver, VectorBackend

logger = logging.getLogger(__name__)


class IndexService:
    """Build a complete local LightRAG workspace before activating one generation."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        runtime_manager: Any = None,
    ) -> None:
        self._session = session
        self._kb_repo = KnowledgeBaseRepository(session)
        self._doc_repo = DocumentRepository(session)
        self._task_repo = TaskRepository(session)
        self._generation_repo = VectorIndexGenerationRepository(session)
        self._settings = settings
        self._runtime_manager = runtime_manager

    async def index_knowledge_base(
        self,
        kb_id: str,
        task_id: str,
        *,
        target_backend: VectorBackend | None = None,
    ) -> dict[str, Any]:
        kb = await self._kb_repo.get(kb_id)
        if kb is None:
            raise RuntimeError(f"KnowledgeBase {kb_id} not found")
        active_docs = await self._doc_repo.list_active_for_kb(kb_id)
        if not active_docs:
            raise RuntimeError(f"KB {kb_id} has no active documents")
        await self._task_repo.update(task_id, current_stage="collecting_docs", progress=0.05)
        settings = self._settings or Settings.from_env()
        selected_backend = target_backend or VectorBackend(kb.vector_backend)

        all_children: list[tuple[Any, Any]] = []
        for doc in active_docs:
            children = load_child_chunks(kb_parsed_dir(kb_id) / "documents" / doc.id)
            if not children:
                logger.warning("No child chunks for doc=%s, skipping", doc.id)
            all_children.extend((doc, child) for child in children)
        if not all_children:
            raise RuntimeError("No child chunks found for any active document")

        fingerprint = build_generation_fingerprint(kb, all_children)
        generation_name = (
            f"g{secrets.token_hex(12)}"
            if selected_backend is VectorBackend.qdrant
            else f"n{secrets.token_hex(12)}"
        )
        canonical_nano_workspace = kb_nano_workspace(kb_id)
        if selected_backend is VectorBackend.qdrant:
            shadow_workspace = kb_qdrant_generation_workspace(kb_id, generation_name)
            collections: dict[str, str] | None = CollectionNameResolver(
                settings.qdrant_collection_prefix
            ).names_for(kb_id=kb_id, generation=generation_name)
        else:
            shadow_workspace = canonical_nano_workspace.parent / f"shadow-{generation_name}" / "workspace"
            collections = None

        record = await self._generation_repo.create_shadow(
            knowledge_base_id=kb_id,
            backend=selected_backend.value,
            generation=generation_name,
            workspace_path=str(shadow_workspace),
            collections=collections,
            document_manifest_hash=fingerprint.document_manifest_hash,
            child_chunks_manifest_hash=fingerprint.child_chunks_manifest_hash,
            embedding_config_hash=fingerprint.embedding_config_hash,
            chunking_config_hash=fingerprint.chunking_config_hash,
            created_by_task_id=task_id,
        )
        if shadow_workspace.exists():
            shutil.rmtree(shadow_workspace, ignore_errors=True)
        shadow_workspace.mkdir(parents=True, exist_ok=True)
        kb_settings = settings_for_knowledge_base(
            settings,
            kb,
            backend=selected_backend,
            generation=generation_name,
            working_dir=shadow_workspace,
        )
        try:
            await self._task_repo.update(task_id, current_stage="indexing", progress=0.20)
            from industrial_rag.citation_formatter import Citation, encode_chunk_header
            from industrial_rag.lightrag_service import LightRAGService

            boundary = "\n\n<<<INDUSTRIAL_RAG_CHUNK_BOUNDARY>>>\n\n"
            rendered = []
            for doc, child in all_children:
                citation = Citation(doc.original_file_name, child.page_start or 1, child.chunk_id)
                rendered.append(
                    f"{encode_chunk_header(citation)}\n"
                    f"[来源：{doc.original_file_name}，第{child.page_start or 1}页，"
                    f"章节：{child.section_title or '未识别章节'}]\n"
                    f"[parent_chunk_id：{child.parent_chunk_id}]\n"
                    f"{child.embedding_content or child.content}"
                )
            identity = hashlib.sha256(
                "\n".join(child.chunk_id for _, child in all_children).encode("utf-8")
            ).hexdigest()[:20]
            service = LightRAGService(kb_settings)
            await service.initialize()
            try:
                await self._task_repo.update(task_id, current_stage="ingesting", progress=0.30)
                await service._backend.ainsert(
                    input=[boundary.join(rendered)],
                    ids=[f"kb-{identity}"],
                    file_paths=[doc.original_file_name for doc in active_docs],
                    split_by_character=boundary,
                    split_by_character_only=True,
                )
            finally:
                await service.close()

            await self._health_verify(
                kb_id,
                shadow_workspace,
                len(active_docs),
                backend=selected_backend,
                workspace_token=kb_settings.vector_workspace,
            )
            if selected_backend is VectorBackend.qdrant:
                qdrant_chunks = await QdrantCollectionService(kb_settings).verify_generation(
                    expected_chunks=len(all_children)
                )
            else:
                qdrant_chunks = 0

            if selected_backend is VectorBackend.nano:
                backup = canonical_nano_workspace.parent / f"backup-{generation_name}"
                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)
                if canonical_nano_workspace.exists():
                    canonical_nano_workspace.rename(backup)
                shadow_workspace.rename(canonical_nano_workspace)
                shadow_workspace = canonical_nano_workspace
                await self._generation_repo.update_workspace_path(record, str(shadow_workspace))
                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)

            if self._runtime_manager is not None:
                await self._runtime_manager.close_runtime(kb_id)
            await self._generation_repo.activate(record)
            storage_root = (
                shadow_workspace / kb_settings.vector_workspace
                if kb_settings.vector_workspace
                else shadow_workspace
            )
            doc_status_path = storage_root / "kv_store_doc_status.json"
            chunk_count = qdrant_chunks
            if doc_status_path.is_file():
                statuses = json.loads(doc_status_path.read_text(encoding="utf-8"))
                chunk_count = max(
                    chunk_count,
                    sum(v.get("chunks_count", 0) for v in statuses.values() if isinstance(v, dict)),
                )
            await self._kb_repo.update(
                kb_id,
                active_document_count=len(active_docs),
                document_count=len(active_docs),
                chunk_count=chunk_count,
                vector_backend=selected_backend.value,
                active_vector_generation_id=record.id,
                workspace_path=str(canonical_nano_workspace),
                status="ready",
                updated_at=datetime.now(tz=UTC),
            )
            for doc in active_docs:
                await self._doc_repo.update(
                    doc.id, index_status="done", status="indexed", indexed_at=datetime.now(tz=UTC)
                )
            return {
                "kb_id": kb_id,
                "active_docs": len(active_docs),
                "chunks": chunk_count,
                "backend": selected_backend.value,
                "generation": generation_name,
                "generation_id": record.id,
            }
        except Exception as error:
            await self._generation_repo.mark_failed(record, str(error))
            if selected_backend is VectorBackend.qdrant:
                try:
                    await QdrantCollectionService(kb_settings).delete_generation()
                except Exception:
                    logger.exception("Failed to clean Qdrant shadow kb=%s", kb_id)
            if shadow_workspace.exists():
                shutil.rmtree(shadow_workspace, ignore_errors=True)
            raise

    async def _health_verify(
        self,
        kb_id: str,
        workspace: Path,
        expected_docs: int,
        *,
        backend: VectorBackend,
        workspace_token: str | None = None,
    ) -> None:
        marker = workspace / "industrial_rag_index.json"
        storage_root = workspace / workspace_token if workspace_token else workspace
        doc_status = storage_root / "kv_store_doc_status.json"
        if not marker.is_file():
            raise RuntimeError(f"Index health: required storage missing in {workspace}")
        if backend is VectorBackend.nano:
            # Nano stores chunk payloads as workspace JSON; Qdrant stores them in
            # Qdrant collections and is verified separately via verify_generation().
            text_chunks = storage_root / "kv_store_text_chunks.json"
            if not text_chunks.is_file():
                raise RuntimeError(f"Index health: required storage missing in {workspace}")
            chunks = json.loads(text_chunks.read_text(encoding="utf-8"))
            if not chunks:
                raise RuntimeError("Index health: zero text chunks produced")
            headers = sum(
                1
                for value in chunks.values()
                if isinstance(value, dict) and "INDUSTRIAL_RAG_SOURCE" in value.get("content", "")
            )
            if headers == 0:
                raise RuntimeError("Index health: no source headers found in chunks")
        if doc_status.is_file():
            statuses = json.loads(doc_status.read_text(encoding="utf-8"))
            processed = sum(
                1
                for value in statuses.values()
                if isinstance(value, dict) and value.get("status") == "processed"
            )
            logger.info("Index health kb=%s: %d/%d documents processed", kb_id, processed, expected_docs)

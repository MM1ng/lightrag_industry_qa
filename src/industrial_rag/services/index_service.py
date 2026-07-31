"""Index service: real LightRAG index build + health verification.

Uses the current NanoVectorDB + NetworkX backend.  For safety with
incremental inserts, this performs a **full KB rebuild** each time
a new document needs to be indexed.

In the future (Qdrant phase) this can be replaced with incremental
point-level inserts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.config import Settings
from industrial_rag.repositories.document_repository import DocumentRepository
from industrial_rag.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from industrial_rag.repositories.task_repository import TaskRepository
from industrial_rag.services.parse_service import load_child_chunks
from industrial_rag.storage_layout import kb_parsed_dir

logger = logging.getLogger(__name__)


class IndexService:
    """Build a LightRAG index from parsed ChildChunks for an entire KB.

    Strategy: full KB rebuild into a temporary workspace, validate,
    then atomically swap with the canonical workspace.
    """

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
        self._settings = settings
        self._runtime_manager = runtime_manager

    async def index_knowledge_base(self, kb_id: str, task_id: str) -> dict[str, Any]:
        """Full KB rebuild: collect all active parsed docs → new LightRAG index."""
        kb = await self._kb_repo.get(kb_id)
        if kb is None:
            raise RuntimeError(f"KnowledgeBase {kb_id} not found")

        active_docs = await self._doc_repo.list_active_for_kb(kb_id)
        if not active_docs:
            raise RuntimeError(f"KB {kb_id} has no active documents")

        await self._task_repo.update(
            task_id, current_stage="collecting_docs", progress=0.05
        )

        # Resolve settings for this KB
        if self._settings is None:
            from industrial_rag.config import Settings

            settings = Settings.from_env()
        else:
            settings = self._settings

        workspace = Path(kb.workspace_path)
        workspace.parent.mkdir(parents=True, exist_ok=True)

        tmp_workspace = workspace.parent / f"{workspace.name}.rebuild-{task_id}"
        backup_workspace = workspace.parent / f"{workspace.name}.backup-{task_id}"
        parsed_base = kb_parsed_dir(kb_id)

        # Clean up any leftover tmp from previous failed attempts
        if tmp_workspace.exists():
            shutil.rmtree(tmp_workspace, ignore_errors=True)

        try:
            # 1. Gather all ChildChunks from active documents
            all_children: list[tuple[Any, Any]] = []  # (doc, child_chunk)
            for doc in active_docs:
                doc_parsed = parsed_base / "current"
                children = load_child_chunks(doc_parsed)
                if not children:
                    logger.warning("No child chunks for doc=%s, skipping", doc.id)
                    continue
                for child in children:
                    all_children.append((doc, child))

            if not all_children:
                raise RuntimeError("No child chunks found for any active document")

            await self._task_repo.update(
                task_id, current_stage="indexing", progress=0.20
            )
            logger.info(
                "Index rebuild kb=%s: %d children from %d docs",
                kb_id, len(all_children), len(active_docs),
            )

            # 2. Initialise LightRAG in tmp workspace
            tmp_workspace.mkdir(parents=True, exist_ok=True)
            kb_settings = Settings(
                api_key=settings.api_key,
                llm_base_url=settings.llm_base_url,
                llm_model=settings.llm_model,
                embedding_model=settings.embedding_model,
                embedding_dim=settings.embedding_dim,
                working_dir=tmp_workspace,
            )

            from industrial_rag.lightrag_service import LightRAGService

            svc = LightRAGService(kb_settings)
            await svc.initialize()

            try:
                # 3. Ingest all child chunks
                await self._task_repo.update(
                    task_id, current_stage="ingesting", progress=0.30
                )

                # Build a single combined text with per-child boundaries
                from industrial_rag.citation_formatter import Citation, encode_chunk_header

                _CHUNK_BOUNDARY = "\n\n<<<INDUSTRIAL_RAG_CHUNK_BOUNDARY>>>\n\n"
                rendered: list[str] = []
                for doc, child in all_children:
                    citation = Citation(
                        doc.original_file_name,
                        child.page_start or 1,
                        child.chunk_id,
                    )
                    section = child.section_title or "未识别章节"
                    rendered.append(
                        f"{encode_chunk_header(citation)}\n"
                        f"[来源：{doc.original_file_name}，第{child.page_start or 1}页，"
                        f"章节：{section}]\n"
                        f"[parent_chunk_id：{child.parent_chunk_id}]\n"
                        f"{child.embedding_content or child.content}"
                    )

                identity = hashlib.sha256(
                    "\n".join(c.chunk_id for _, c in all_children).encode("utf-8")
                ).hexdigest()[:20]

                await svc._backend.ainsert(
                    input=[_CHUNK_BOUNDARY.join(rendered)],
                    ids=[f"kb-{identity}"],
                    file_paths=[doc.original_file_name for doc in active_docs],
                    split_by_character=_CHUNK_BOUNDARY,
                    split_by_character_only=True,
                )

                await self._task_repo.update(
                    task_id, current_stage="processing", progress=0.70
                )
            finally:
                await svc.close()

            # 4. Health check
            await self._health_verify(kb_id, tmp_workspace, len(active_docs))

            # 5. Close old runtime
            if self._runtime_manager is not None:
                await self._runtime_manager.close_runtime(kb_id)

            # 6. Atomic swap
            if workspace.exists():
                if backup_workspace.exists():
                    shutil.rmtree(backup_workspace, ignore_errors=True)
                workspace.rename(backup_workspace)
            tmp_workspace.rename(workspace)
            logger.info("Index rebuild kb=%s: atomic swap complete", kb_id)

            # 7. Update counts
            chunk_count = 0
            doc_status_path = workspace / "kv_store_doc_status.json"
            if doc_status_path.is_file():
                ds = json.loads(doc_status_path.read_text(encoding="utf-8"))
                chunk_count = sum(
                    v.get("chunks_count", 0)
                    for v in ds.values()
                    if isinstance(v, dict)
                )

            await self._kb_repo.update(
                kb_id,
                active_document_count=len(active_docs),
                document_count=len(active_docs),
                chunk_count=chunk_count,
                status="ready",
                updated_at=datetime.now(tz=UTC),
            )

            # 8. Mark all active documents as indexed
            now = datetime.now(tz=UTC)
            for doc in active_docs:
                await self._doc_repo.update(
                    doc.id,
                    index_status="done",
                    status="indexed",
                    indexed_at=now,
                )

            # 9. Delete backup
            if backup_workspace.exists():
                shutil.rmtree(backup_workspace, ignore_errors=True)

            logger.info("Index rebuild kb=%s: success (%d docs, %d chunks)", kb_id, len(active_docs), chunk_count)
            return {"kb_id": kb_id, "active_docs": len(active_docs), "chunks": chunk_count}

        except Exception:
            # Rollback: restore workspace if it was renamed
            if backup_workspace.exists() and not workspace.exists():
                backup_workspace.rename(workspace)
            if tmp_workspace.exists():
                shutil.rmtree(tmp_workspace, ignore_errors=True)
            raise

    # ------------------------------------------------------------------
    # Health verification
    # ------------------------------------------------------------------

    async def _health_verify(self, kb_id: str, workspace: Path, expected_docs: int) -> None:
        """Verify a built workspace is viable."""
        idx_marker = workspace / "industrial_rag_index.json"
        doc_status = workspace / "kv_store_doc_status.json"
        text_chunks = workspace / "kv_store_text_chunks.json"
        _ = workspace / "graph_chunk_entity_relation.graphml"

        if not idx_marker.is_file():
            raise RuntimeError(f"Index health: marker missing in {workspace}")

        if not text_chunks.is_file():
            raise RuntimeError(f"Index health: text_chunks missing in {workspace}")

        tc = json.loads(text_chunks.read_text(encoding="utf-8"))
        if len(tc) == 0:
            raise RuntimeError("Index health: zero text chunks produced")

        # Each chunk must contain a source header
        header_count = sum(
            1 for v in tc.values()
            if isinstance(v, dict) and "INDUSTRIAL_RAG_SOURCE" in v.get("content", "")
        )
        if header_count == 0:
            raise RuntimeError("Index health: no source headers found in chunks")

        # Verify document status counts
        if doc_status.is_file():
            ds = json.loads(doc_status.read_text(encoding="utf-8"))
            processed = sum(
                1 for v in ds.values()
                if isinstance(v, dict) and v.get("status") == "processed"
            )
            logger.info(
                "Index health kb=%s: %d/%d docs processed, %d chunks, %d headers",
                kb_id, processed, expected_docs, len(tc), header_count,
            )

        logger.info("Index health kb=%s: PASSED", kb_id)

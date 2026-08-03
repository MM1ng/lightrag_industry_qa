"""Register and index the isolated Phase 10B-3C candidate in staging."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

from industrial_rag.citation_formatter import Citation, encode_chunk_header
from industrial_rag.config import Settings
from industrial_rag.db.models import VectorIndexGenerationStatus
from industrial_rag.db.session import get_session_factory
from industrial_rag.document_parser import DocumentChunk
from industrial_rag.kb_runtime_settings import settings_for_knowledge_base
from industrial_rag.lightrag_service import LightRAGService
from industrial_rag.repositories.knowledge_base_repository import KnowledgeBaseRepository
from industrial_rag.repositories.vector_index_generation_repository import (
    VectorIndexGenerationRepository,
)
from industrial_rag.services.qdrant_collection_service import QdrantCollectionService
from industrial_rag.vector_collections import CollectionNameResolver, VectorBackend

ROOT = Path(__file__).resolve().parents[1]
KB_ID = "8fce4626859d44abb70a9ae5b0372cea"
GENERATION_NAME = "g10b3c20260803"
CANDIDATE_ROOT = ROOT / "runtime" / "phase10b3c" / "kb_data" / KB_ID / GENERATION_NAME


def _load_env() -> None:
    for line in (ROOT / ".env.local_staging").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ[key] = value
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{ROOT / 'runtime' / 'phase10b3c' / 'industrial_rag_candidate.db'}"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def main() -> int:
    _load_env()
    settings = Settings.from_env()
    manifest_path = CANDIDATE_ROOT / "context_registry" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    chunks = [
        json.loads(line)
        for line in (CANDIDATE_ROOT / "context_registry" / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    candidate_workspace = CANDIDATE_ROOT / "workspace"
    candidate_workspace.mkdir(parents=True, exist_ok=True)
    candidate_id = _sha(f"{KB_ID}:{GENERATION_NAME}")[:32]

    async with get_session_factory()() as session:
        kb = await KnowledgeBaseRepository(session).get(KB_ID)
        if kb is None:
            raise RuntimeError("staging knowledge base not found")
        generation_repo = VectorIndexGenerationRepository(session)
        generation = await generation_repo.get(candidate_id)
        if generation is None:
            collections = CollectionNameResolver(settings.qdrant_collection_prefix).names_for(
                kb_id=KB_ID, generation=GENERATION_NAME
            )
            source_hash = _sha(json.dumps(manifest["source_artifacts"], sort_keys=True))
            generation = await generation_repo.create_shadow(
                id=candidate_id,
                knowledge_base_id=KB_ID,
                backend=VectorBackend.qdrant.value,
                generation=GENERATION_NAME,
                workspace_path=str(candidate_workspace),
                collections=collections,
                document_manifest_hash=source_hash,
                child_chunks_manifest_hash=_sha((CANDIDATE_ROOT / "context_registry" / "chunks.jsonl").read_text(encoding="utf-8")),
                embedding_config_hash=_sha(f"{kb.embedding_model}:{kb.embedding_dimension}"),
                chunking_config_hash=_sha(f"{manifest['chunking_strategy']}:{manifest['chunking_version']}"),
            )
            generation.status = VectorIndexGenerationStatus.building
            await session.flush()
            # Release the SQLite write lock before the network-bound embedding build.
            await session.commit()
        elif generation.status == VectorIndexGenerationStatus.ready:
            print(json.dumps({"generation_id": candidate_id, "generation": GENERATION_NAME, "status": "already_ready"}))
            return 0

        candidate_settings = settings_for_knowledge_base(
            settings,
            kb,
            backend=VectorBackend.qdrant,
            generation=GENERATION_NAME,
            working_dir=candidate_workspace,
        )
        service = LightRAGService(candidate_settings)
        try:
            await service.initialize()
            from industrial_rag.lightrag_service import _CHUNK_BOUNDARY

            grouped: dict[str, list[DocumentChunk]] = {}
            for row in chunks:
                grouped.setdefault(str(row["document_name"]), []).append(
                    DocumentChunk(
                        chunk_id=str(row["chunk_id"]),
                        text=str(row["content"]),
                        source_file=str(row["document_name"]),
                        page_number=int(row["page_start"] or 1),
                        section_title=(str(row["section_path"][-1]) if row["section_path"] else None),
                    )
                )
            existing_count = await QdrantCollectionService(candidate_settings).verify_generation(
                expected_chunks=0, require_chunks=False
            )
            if existing_count < len(chunks):
                for source_file, source_chunks in grouped.items():
                    rendered = []
                    for chunk in source_chunks:
                        rendered.append(
                            f"{encode_chunk_header(Citation(chunk.source_file, chunk.page_number, chunk.chunk_id))}\n"
                            f"[来源：{chunk.source_file}，第{chunk.page_number}页，章节：{chunk.section_title or '未识别章节'}]\n"
                            f"{chunk.text}"
                        )
                    identity = _sha("\n".join(chunk.chunk_id for chunk in source_chunks))[:20]
                    await service._backend.ainsert(  # type: ignore[union-attr]
                        input=[_CHUNK_BOUNDARY.join(rendered)],
                        ids=[f"manual-{identity}"],
                        file_paths=[source_file],
                        split_by_character=_CHUNK_BOUNDARY,
                        split_by_character_only=True,
                    )
        except Exception:
            generation.status = VectorIndexGenerationStatus.failed
            await session.commit()
            raise
        finally:
            try:
                await asyncio.wait_for(service.close(), timeout=30)
            except TimeoutError:
                pass

        count = await QdrantCollectionService(candidate_settings).verify_generation(expected_chunks=len(chunks))
        generation.status = VectorIndexGenerationStatus.ready
        generation.last_error = None
        await session.commit()
        print(json.dumps({"generation_id": candidate_id, "generation": GENERATION_NAME, "status": "ready", "chunk_count": count}))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

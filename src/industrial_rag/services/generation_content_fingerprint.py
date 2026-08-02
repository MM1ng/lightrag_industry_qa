"""Full Generation evidence fingerprints used by validation and Promote."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from industrial_rag.config import Settings
from industrial_rag.repositories.document_repository import DocumentRepository
from industrial_rag.repositories.update_job_repository import UpdateJobRepository


def stable_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def app_git_commit() -> str:
    configured = os.environ.get("APP_GIT_COMMIT", "").strip()
    if configured:
        return configured[:40]
    git_dir = Path(__file__).resolve().parents[3] / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            return (git_dir / head[5:]).read_text(encoding="utf-8").strip()[:40]
        return head[:40]
    except OSError:
        return "unknown"


@dataclass(frozen=True, slots=True)
class GenerationEvidenceFingerprint:
    app_git_commit: str
    strategy_fingerprint: str
    generation_manifest_hash: str
    qdrant_content_fingerprint: str
    document_registry_fingerprint: str
    generation_content_epoch: int
    qdrant_point_count: int


class GenerationContentFingerprintService:
    def __init__(self, session, *, settings: Settings, qdrant_client_factory) -> None:
        self._settings = settings
        self._documents = DocumentRepository(session)
        self._jobs = UpdateJobRepository(session)
        self._qdrant_client_factory = qdrant_client_factory

    async def calculate(self, kb_id: str, generation: Any) -> GenerationEvidenceFingerprint:
        manifest_hash = stable_hash(
            {
                "document": generation.document_manifest_hash,
                "chunks": generation.child_chunks_manifest_hash,
                "embedding": generation.embedding_config_hash,
                "chunking": generation.chunking_config_hash,
                "collections": generation.collections or {},
            }
        )
        strategy_hash = stable_hash(
            {
                "llm_model": self._settings.llm_model,
                "embedding_model": self._settings.embedding_model,
                "embedding_dim": self._settings.embedding_dim,
                "chunk_token_size": self._settings.chunk_token_size,
                "query_mode": "mix",
                "llm_cache": False,
            }
        )
        job = await self._jobs.find_by_candidate(generation.id)
        snapshot = (job.result or {}).get("documents") if job is not None else None
        if snapshot is None:
            docs = await self._documents.list_active_for_kb(kb_id)
            snapshot = [
                {
                    "document_id": doc.id,
                    "logical_name": doc.logical_name or doc.original_file_name,
                    "version": doc.version,
                    "content_sha256": doc.file_hash,
                    "is_active": True,
                }
                for doc in docs
            ]
        registry_hash = stable_hash(sorted(snapshot, key=lambda item: str(item.get("document_id"))))
        qdrant_hash, point_count = await self._qdrant_fingerprint(generation.collections or {})
        return GenerationEvidenceFingerprint(
            app_git_commit=app_git_commit(),
            strategy_fingerprint=strategy_hash,
            generation_manifest_hash=manifest_hash,
            qdrant_content_fingerprint=qdrant_hash,
            document_registry_fingerprint=registry_hash,
            generation_content_epoch=int(generation.content_epoch or 0),
            qdrant_point_count=point_count,
        )

    async def _qdrant_fingerprint(self, collections: dict[str, str]) -> tuple[str, int]:
        client = self._qdrant_client_factory()
        records: list[dict[str, Any]] = []
        try:
            for namespace, collection in sorted(collections.items()):
                if not await client.collection_exists(collection):
                    records.append({"namespace": namespace, "missing": True})
                    continue
                offset = None
                while True:
                    points, next_offset = await client.scroll(
                        collection_name=collection,
                        limit=256,
                        offset=offset,
                        with_payload=True,
                        with_vectors=True,
                    )
                    for point in points:
                        vector = getattr(point, "vector", None)
                        records.append(
                            {
                                "namespace": namespace,
                                "id": str(point.id),
                                "payload": point.payload or {},
                                "vector": vector,
                            }
                        )
                    if next_offset is None:
                        break
                    offset = next_offset
        finally:
            await client.close()
        records.sort(key=lambda item: (str(item.get("namespace")), str(item.get("id"))))
        return stable_hash(records), sum(1 for item in records if "id" in item)

"""Deterministic fingerprints for complete vector-index inputs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GenerationFingerprint:
    document_manifest_hash: str
    child_chunks_manifest_hash: str
    embedding_config_hash: str
    chunking_config_hash: str


def _hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_generation_fingerprint(
    knowledge_base: Any,
    document_children: Iterable[tuple[Any, Any]],
) -> GenerationFingerprint:
    """Hash active documents, ChildChunks, embedding, and chunking reproducibly."""
    pairs = list(document_children)
    documents = {
        str(doc.id): {
            "id": str(doc.id),
            "version": int(doc.version),
            "file_hash": str(doc.file_hash),
        }
        for doc, _ in pairs
    }
    children = sorted(
        (
            {
                "document_id": str(doc.id),
                "chunk_id": str(child.chunk_id),
                "parent_chunk_id": str(child.parent_chunk_id),
                "page_start": child.page_start,
                "page_end": child.page_end,
                "content": child.embedding_content or child.content,
            }
            for doc, child in pairs
        ),
        key=lambda value: (value["document_id"], value["chunk_id"]),
    )
    return GenerationFingerprint(
        document_manifest_hash=_hash([documents[key] for key in sorted(documents)]),
        child_chunks_manifest_hash=_hash(children),
        embedding_config_hash=_hash(
            {
                "model": knowledge_base.embedding_model,
                "dimension": knowledge_base.embedding_dimension,
            }
        ),
        chunking_config_hash=_hash(
            {
                "strategy": knowledge_base.chunking_strategy,
                "version": knowledge_base.chunking_version,
                "config": knowledge_base.chunking_config or {},
            }
        ),
    )

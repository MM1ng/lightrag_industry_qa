from __future__ import annotations

from types import SimpleNamespace

from industrial_rag.services.generation_fingerprint_service import (
    build_generation_fingerprint,
)


class _Child:
    def __init__(self, chunk_id: str, content: str) -> None:
        self.chunk_id = chunk_id
        self.content = content
        self.embedding_content = None
        self.parent_chunk_id = "parent"
        self.page_start = 1
        self.page_end = 1
        self.section_title = None


def _kb() -> SimpleNamespace:
    return SimpleNamespace(
        embedding_model="text-embedding-v4",
        embedding_dimension=1024,
        chunking_strategy="fixed_character",
        chunking_version="1",
        chunking_config={"child_target_tokens": 256},
    )


def test_generation_fingerprint_is_stable_when_documents_and_children_are_reordered() -> None:
    first = SimpleNamespace(id="b" * 32, version=2, file_hash="b" * 64)
    second = SimpleNamespace(id="a" * 32, version=1, file_hash="a" * 64)
    pairs = [(first, _Child("chunk-b", "B")), (second, _Child("chunk-a", "A"))]

    expected = build_generation_fingerprint(_kb(), pairs)
    reordered = build_generation_fingerprint(_kb(), list(reversed(pairs)))

    assert reordered == expected


def test_generation_fingerprint_changes_when_current_document_version_changes() -> None:
    document = SimpleNamespace(id="a" * 32, version=1, file_hash="a" * 64)
    original = build_generation_fingerprint(_kb(), [(document, _Child("chunk-a", "A"))])
    document.version = 2

    changed = build_generation_fingerprint(_kb(), [(document, _Child("chunk-a", "A"))])

    assert changed.document_manifest_hash != original.document_manifest_hash

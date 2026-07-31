"""Unit tests for ParentChunkStore — JSONL-backed with in-memory index."""

from __future__ import annotations

from pathlib import Path

from industrial_rag.parent_chunk_store import ParentChunkStore
from industrial_rag.parser_models import (
    ChildChunk,
    ParentChunk,
)


def _make_parent(pchunk_id: str, doc_id: str, doc_name: str, child_ids: tuple[str, ...], content: str = "parent content") -> ParentChunk:
    return ParentChunk(
        parent_chunk_id=pchunk_id,
        document_id=doc_id,
        document_name=doc_name,
        section_path=(),
        content=content,
        token_count=100,
        source_hash="abc",
        child_chunk_ids=child_ids,
    )


def _make_child(chunk_id: str, pchunk_id: str, doc_id: str, doc_name: str) -> ChildChunk:
    return ChildChunk(
        chunk_id=chunk_id,
        parent_chunk_id=pchunk_id,
        document_id=doc_id,
        document_name=doc_name,
        content="child content",
        embedding_content="child content",
        token_count=50,
        source_hash="xyz",
    )


def setup_store(tmp_path: Path, parents: list[ParentChunk], children: list[ChildChunk]) -> ParentChunkStore:
    store = ParentChunkStore(tmp_path)
    store.write_all(parents, children)
    return store


# ---------------------------------------------------------------------------
# Write + read
# ---------------------------------------------------------------------------


def test_write_and_get_parent(tmp_path: Path) -> None:
    parents = [_make_parent("p1", "d1", "a.pdf", ("c1",))]
    children = [_make_child("c1", "p1", "d1", "a.pdf")]
    store = setup_store(tmp_path, parents, children)

    p = store.get_parent("p1")
    assert p is not None
    assert p.parent_chunk_id == "p1"
    assert p.child_chunk_ids == ("c1",)


def test_get_parent_missing(tmp_path: Path) -> None:
    store = ParentChunkStore(tmp_path)
    store.write_all([], [])
    assert store.get_parent("nonexistent") is None


def test_get_parent_by_child(tmp_path: Path) -> None:
    parents = [_make_parent("p1", "d1", "a.pdf", ("c1", "c2"))]
    children = [
        _make_child("c1", "p1", "d1", "a.pdf"),
        _make_child("c2", "p1", "d1", "a.pdf"),
    ]
    store = setup_store(tmp_path, parents, children)

    p = store.get_parent_by_child("c1")
    assert p is not None
    assert p.parent_chunk_id == "p1"


def test_get_parents_by_children_deduplicates(tmp_path: Path) -> None:
    parents = [_make_parent("p1", "d1", "a.pdf", ("c1", "c2", "c3"))]
    children = [
        _make_child("c1", "p1", "d1", "a.pdf"),
        _make_child("c2", "p1", "d1", "a.pdf"),
        _make_child("c3", "p1", "d1", "a.pdf"),
    ]
    store = setup_store(tmp_path, parents, children)

    result = store.get_parents_by_children(["c1", "c2", "c2", "c1"])
    assert len(result) == 1
    assert result[0].parent_chunk_id == "p1"


def test_count_orphaned_children(tmp_path: Path) -> None:
    parents = [_make_parent("p1", "d1", "a.pdf", ("c1",))]
    children = [_make_child("c1", "p1", "d1", "a.pdf")]
    store = setup_store(tmp_path, parents, children)

    assert store.count_orphaned_children(["c1"]) == 0
    assert store.count_orphaned_children(["c1", "c2"]) == 1


def test_get_parents_by_document(tmp_path: Path) -> None:
    parents = [
        _make_parent("p1", "d1", "a.pdf", ("c1",)),
        _make_parent("p2", "d2", "b.pdf", ("c2",)),
    ]
    children = [
        _make_child("c1", "p1", "d1", "a.pdf"),
        _make_child("c2", "p2", "d2", "b.pdf"),
    ]
    store = setup_store(tmp_path, parents, children)

    assert len(store.get_parents_by_document("d1")) == 1


def test_count_parents(tmp_path: Path) -> None:
    parents = [
        _make_parent("p1", "d1", "a.pdf", ("c1",)),
        _make_parent("p2", "d2", "b.pdf", ("c2",)),
    ]
    children = [
        _make_child("c1", "p1", "d1", "a.pdf"),
        _make_child("c2", "p2", "d2", "b.pdf"),
    ]
    store = setup_store(tmp_path, parents, children)
    assert store.count_parents() == 2


def test_reload_from_disk(tmp_path: Path) -> None:
    parents = [_make_parent("p1", "d1", "a.pdf", ("c1",))]
    children = [_make_child("c1", "p1", "d1", "a.pdf")]
    setup_store(tmp_path, parents, children)

    # New store instance — should load from disk
    store2 = ParentChunkStore(tmp_path)
    p = store2.get_parent("p1")
    assert p is not None
    assert p.parent_chunk_id == "p1"

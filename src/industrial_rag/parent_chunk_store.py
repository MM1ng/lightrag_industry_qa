"""JSONL-backed ParentChunk store with lightweight in-memory index.

Reads / writes ``parent_chunks.jsonl`` with an accompanying ``parent_index.json``
that maps ``parent_chunk_id → byte_offset`` (and ``child_chunk_id → parent_chunk_id``
reverse mapping) so all lookups are O(1) after load.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from industrial_rag.parser_models import ChildChunk, ParentChunk


@dataclass(frozen=True, slots=True)
class ParentChunkLookup:
    offset: int
    parent_chunk_id: str


class ParentChunkStore:
    """Read / write parent chunks from JSONL with an O(1) lookup index.

    Thread-unsafe by design: callers should serialize access externally.
    """

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._data_path = directory / "parent_chunks.jsonl"
        self._index_path = directory / "parent_index.json"
        # parent_chunk_id → byte offset in JSONL
        self._parent_index: dict[str, int] = {}
        # child_chunk_id → parent_chunk_id (reverse lookup)
        self._child_index: dict[str, str] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def write_all(
        self,
        parents: Iterable[ParentChunk],
        children: Iterable[ChildChunk],
    ) -> None:
        """Atomically replace the store with new parent/child data."""
        self._dir.mkdir(parents=True, exist_ok=True)

        parent_list = list(parents)
        list(children)

        # Write parents JSONL + index
        tmp_data = self._data_path.with_suffix(".tmp")
        parent_index: dict[str, int] = {}
        child_index: dict[str, str] = {}

        with tmp_data.open("w", encoding="utf-8", newline="\n") as f:
            for parent in parent_list:
                offset = f.tell()
                parent_index[parent.parent_chunk_id] = offset
                line = json.dumps(
                    {
                        "parent_chunk_id": parent.parent_chunk_id,
                        "document_id": parent.document_id,
                        "document_name": parent.document_name,
                        "document_version": parent.document_version,
                        "page_start": parent.page_start,
                        "page_end": parent.page_end,
                        "section_path": list(parent.section_path),
                        "section_title": parent.section_title,
                        "content_type": parent.content_type.value,
                        "content": parent.content,
                        "token_count": parent.token_count,
                        "source_hash": parent.source_hash,
                        "parser": parent.parser,
                        "parser_version": parent.parser_version,
                        "child_chunk_ids": list(parent.child_chunk_ids),
                        "metadata": parent.metadata,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                f.write(line + "\n")

                # Build child → parent index
                for child_id in parent.child_chunk_ids:
                    child_index[child_id] = parent.parent_chunk_id

        tmp_data.replace(self._data_path)

        # Write index
        tmp_index = self._index_path.with_suffix(".tmp")
        tmp_index.write_text(
            json.dumps(
                {"parent_index": parent_index, "child_index": child_index},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        tmp_index.replace(self._index_path)

        self._parent_index = parent_index
        self._child_index = child_index
        self._loaded = True

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not self._index_path.is_file():
            self._loaded = True
            return
        raw = json.loads(self._index_path.read_text(encoding="utf-8"))
        pi = raw.get("parent_index", {})
        ci = raw.get("child_index", {})
        self._parent_index = {str(k): int(v) for k, v in pi.items()}
        self._child_index = {str(k): str(v) for k, v in ci.items()}
        self._loaded = True

    def get_parent(self, parent_chunk_id: str) -> ParentChunk | None:
        self._ensure_loaded()
        offset = self._parent_index.get(parent_chunk_id)
        if offset is None:
            return None
        return self._read_parent_at(offset)

    def get_parent_by_child(self, child_chunk_id: str) -> ParentChunk | None:
        self._ensure_loaded()
        parent_id = self._child_index.get(child_chunk_id)
        if parent_id is None:
            return None
        return self.get_parent(parent_id)

    def get_parents_by_children(
        self, child_chunk_ids: Iterable[str]
    ) -> list[ParentChunk]:
        self._ensure_loaded()
        seen: set[str] = set()
        parents: list[ParentChunk] = []
        for cid in child_chunk_ids:
            pid = self._child_index.get(cid)
            if pid and pid not in seen:
                seen.add(pid)
                parent = self.get_parent(pid)
                if parent:
                    parents.append(parent)
        return parents

    def get_parents_by_document(self, document_id: str) -> list[ParentChunk]:
        self._ensure_loaded()
        # Slow path: scan all parents.  Index it by doc_id in future.
        results: list[ParentChunk] = []
        if not self._data_path.is_file():
            return results
        with self._data_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("document_id") == document_id:
                    results.append(self._record_to_parent(rec))
        return results

    def iter_parents(self) -> Iterable[ParentChunk]:
        self._ensure_loaded()
        if not self._data_path.is_file():
            return
        with self._data_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                yield self._record_to_parent(rec)

    def count_parents(self) -> int:
        self._ensure_loaded()
        return len(self._parent_index)

    def count_orphaned_children(self, child_chunk_ids: Iterable[str]) -> int:
        self._ensure_loaded()
        count = 0
        for cid in child_chunk_ids:
            if cid not in self._child_index:
                count += 1
        return count

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _read_parent_at(self, offset: int) -> ParentChunk | None:
        if not self._data_path.is_file():
            return None
        with self._data_path.open("r", encoding="utf-8") as f:
            f.seek(offset)
            line = f.readline().strip()
        if not line:
            return None
        rec = json.loads(line)
        return self._record_to_parent(rec)

    @staticmethod
    def _record_to_parent(rec: dict[str, Any]) -> ParentChunk:
        from industrial_rag.parser_models import ContentType

        sp = rec.get("section_path", [])
        if isinstance(sp, list):
            sp = tuple(sp)
        child_ids = rec.get("child_chunk_ids", [])
        if isinstance(child_ids, list):
            child_ids = tuple(child_ids)

        return ParentChunk(
            parent_chunk_id=rec["parent_chunk_id"],
            document_id=rec["document_id"],
            document_name=rec["document_name"],
            document_version=rec.get("document_version", "1"),
            page_start=rec.get("page_start"),
            page_end=rec.get("page_end"),
            section_path=sp,
            section_title=rec.get("section_title"),
            content_type=ContentType(rec.get("content_type", "normal_text")),
            content=rec.get("content", ""),
            token_count=rec.get("token_count", 0),
            source_hash=rec.get("source_hash", ""),
            parser=rec.get("parser", "unknown"),
            parser_version=rec.get("parser_version"),
            child_chunk_ids=child_ids,
            metadata=rec.get("metadata", {}),
        )

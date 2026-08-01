"""Deterministic ParentChunk loader for the frozen PyMuPDF artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from industrial_rag.parser_models import ContentType, ParentChunk

from .config import PDF_NAMES, PYMUPDF_PARENTS_DIR


class ParentLoader:
    """Load frozen ParentChunks by id; never crosses documents/KBs."""

    def __init__(self, parents_dir: Path | None = None) -> None:
        parents_dir = parents_dir or PYMUPDF_PARENTS_DIR
        self._by_id: dict[str, ParentChunk] = {}
        self._by_document: dict[str, dict[str, ParentChunk]] = {}
        for pdf in PDF_NAMES:
            path = parents_dir / pdf / "parent_chunks.jsonl"
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                import json

                row = json.loads(line)
                parent = ParentChunk(
                    parent_chunk_id=row["parent_chunk_id"],
                    document_id=row["document_id"],
                    document_name=row["document_name"],
                    page_start=row.get("page_start"),
                    page_end=row.get("page_end"),
                    section_path=tuple(row.get("section_path", [])),
                    section_title=row.get("section_title"),
                    content_type=ContentType(row.get("content_type", "normal_text")),
                    content=row.get("content", ""),
                    token_count=row.get("token_count", 0),
                    source_hash=row.get("source_hash", ""),
                    child_chunk_ids=tuple(row.get("child_chunk_ids", [])),
                )
                self._by_id[parent.parent_chunk_id] = parent
                self._by_document.setdefault(parent.document_name, {})[
                    parent.parent_chunk_id
                ] = parent

    def get(self, parent_id: str) -> ParentChunk | None:
        return self._by_id.get(parent_id)

    def get_for_child(self, child: dict[str, Any]) -> ParentChunk | None:
        parent_id = child.get("parent_chunk_id") or child.get("parent_id")
        parent = self._by_id.get(str(parent_id)) if parent_id else None
        if parent is None:
            return None
        # Parent must belong to the same document (no cross-document/KB merge).
        if parent.document_name != child.get("document_name"):
            return None
        return parent

    @property
    def count(self) -> int:
        return len(self._by_id)

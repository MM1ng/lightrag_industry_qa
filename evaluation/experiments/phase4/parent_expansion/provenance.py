"""Citation/provenance structures for parent-expanded evidence."""

from __future__ import annotations

from typing import Any


def provenance_rows(rows: list[Any]) -> list[dict[str, Any]]:
    """Emit per-evidence provenance: parent ids, page ranges, citation page.

    Citations stay on the original child page; parent pages are recorded as
    context range only and are never substituted as evidence pages.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if not row.included:
            continue
        out.append(
            {
                "source_parent_id": row.parent_id,
                "supporting_child_id": row.child_chunk_id,
                "actual_evidence_page": row.child_page,
                "context_page_range": (
                    [row.parent_page_start, row.parent_page_end] if row.parent_id else None
                ),
                "citation_page": row.child_page,
                "child_document": row.child_document_id,
                "child_rank": row.child_rank,
            }
        )
    return out


def citation_for_child(row: Any) -> dict[str, Any]:
    return {
        "source_file": row.child_document_id,
        "page_number": row.child_page,
        "chunk_id": row.child_chunk_id,
    }

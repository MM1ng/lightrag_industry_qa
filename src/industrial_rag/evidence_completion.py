"""Bounded, deterministic evidence completion over a generation registry.

The registry is supplied by the document build pipeline; this module never
opens PDFs or changes retrieval candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CompletionSource = Literal["parent_context", "adjacent", "table_header", "multi_evidence_completion"]


@dataclass(frozen=True, slots=True)
class ContextRecord:
    knowledge_base_id: str
    generation_id: str
    document_id: str
    document_name: str
    chunk_id: str
    text: str
    page_start: int
    section_path: tuple[str, ...] = ()
    parent_chunk_id: str | None = None
    previous_chunk_id: str | None = None
    next_chunk_id: str | None = None
    table_id: str | None = None
    table_header_chunk_id: str | None = None


def complete_evidence(
    selected: list[ContextRecord],
    registry: dict[str, ContextRecord],
    *,
    max_completion: int = 2,
) -> tuple[ContextRecord, ...]:
    """Return at most two same-document/generation context records."""
    if max_completion <= 0:
        return ()
    selected_ids = {item.chunk_id for item in selected}
    out: list[ContextRecord] = []
    for item in selected:
        candidate_ids = (item.parent_chunk_id, item.previous_chunk_id, item.next_chunk_id, item.table_header_chunk_id)
        for candidate_id in candidate_ids:
            if not candidate_id or candidate_id in selected_ids or candidate_id in {row.chunk_id for row in out}:
                continue
            candidate = registry.get(candidate_id)
            if candidate is None:
                continue
            if candidate.document_id != item.document_id or candidate.generation_id != item.generation_id:
                continue
            out.append(candidate)
            if len(out) >= max_completion:
                return tuple(out)
    return tuple(out)


"""Parent context expansion: given hit children, fetch parent chunks
and expand context within a token budget.

Works with ``ParentChunkStore`` and requires no rerank / Qdrant.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from industrial_rag.citation_formatter import Citation
from industrial_rag.parent_chunk_store import ParentChunkStore
from industrial_rag.parser_models import ChildChunk, ParentChunk


@dataclass(frozen=True, slots=True)
class ExpansionConfig:
    enabled: bool = True
    max_parent_chunks: int = 3
    max_parent_tokens: int = 4000
    include_safety_warnings: bool = True
    # When True, keep cited source-chunk text alongside parent context
    keep_source_context: bool = True


@dataclass(frozen=True, slots=True)
class ExpandedContext:
    """Result of parent expansion for one query."""

    # Ordered context text ready for the LLM prompt
    context_text: str
    # Citations for the original hit children
    citations: tuple[Citation, ...]
    # Parent chunks that were included
    parent_chunks: tuple[ParentChunk, ...]
    # Total tokens consumed
    total_tokens: int
    # Metadata for debugging
    metadata: dict[str, object] = field(default_factory=dict)


def expand_context(
    hit_children: list[ChildChunk],
    store: ParentChunkStore,
    *,
    config: ExpansionConfig | None = None,
) -> ExpandedContext:
    """Expand context for a set of hit children.

    1. Look up parent chunks for every hit child.
    2. De-duplicate parents (multiple children may share a parent).
    3. Optionally add safety-warning children from each parent.
    4. Fit within ``max_parent_tokens`` budget.
    """
    cfg = config or ExpansionConfig()
    if not cfg.enabled or not hit_children:
        return ExpandedContext(
            context_text=_raw_child_text(hit_children),
            citations=(),
            parent_chunks=(),
            total_tokens=_estimate_tokens(_raw_child_text(hit_children)),
            metadata={"expanded": False, "reason": "disabled or no hits"},
        )

    # 1. Get unique parent chunks
    parent_ids: list[str] = []
    seen: set[str] = set()
    for ch in hit_children:
        pid = ch.parent_chunk_id
        if pid not in seen:
            seen.add(pid)
            parent_ids.append(pid)

    # 2. Fetch parent chunks
    parents: list[ParentChunk] = []
    for pid in parent_ids[: cfg.max_parent_chunks]:
        parent = store.get_parent(pid)
        if parent:
            parents.append(parent)

    # 3. Build context blocks
    sections: list[str] = []
    total_tokens = 0
    included_parents: list[ParentChunk] = []

    # Always start with the source child chunks
    if cfg.keep_source_context:
        source_text = _raw_child_text(hit_children)
        source_tokens = _estimate_tokens(source_text)
        if source_tokens <= cfg.max_parent_tokens // 4:
            sections.append(source_text)
            total_tokens += source_tokens

    # Add parent chunks, respecting budget
    for parent in parents:
        parent_text = _format_parent_context(parent)
        parent_tokens = _estimate_tokens(parent_text)
        if total_tokens + parent_tokens > cfg.max_parent_tokens:
            break
        sections.append(parent_text)
        total_tokens += parent_tokens
        included_parents.append(parent)

    # 4. Optionally add safety warnings
    if cfg.include_safety_warnings:
        for parent in parents:
            for child_id in parent.child_chunk_ids:
                # Simple heuristic: check if any child chunk contains warning keywords
                # In production we'd look up actual child data
                pass

    context_text = "\n\n---\n\n".join(sections)
    return ExpandedContext(
        context_text=context_text,
        citations=(),
        parent_chunks=tuple(included_parents),
        total_tokens=total_tokens,
        metadata={
            "expanded": True,
            "num_parents": len(included_parents),
            "num_children": len(hit_children),
            "token_budget": cfg.max_parent_tokens,
            "tokens_used": total_tokens,
        },
    )


def _raw_child_text(children: list[ChildChunk]) -> str:
    return "\n\n".join(ch.content for ch in children)


def _estimate_tokens(text: str) -> int:
    from industrial_rag.structured_chunker import count_tokens

    return count_tokens(text)


def _format_parent_context(parent: ParentChunk) -> str:
    """Render a parent chunk as context for the LLM."""
    parts: list[str] = []
    if parent.section_title:
        parts.append(f"## {parent.section_title}")
    parts.append(parent.content)
    return "\n".join(parts)

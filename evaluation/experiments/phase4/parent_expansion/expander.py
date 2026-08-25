"""Deterministic Parent Expansion strategies (PE0-PE3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .parent_loader import ParentLoader


@dataclass(frozen=True, slots=True)
class ExpandedEvidence:
    question_id: str
    child_chunk_id: str
    child_rank: int
    child_score: float | None
    child_document_id: str
    child_page: int | None
    child_text: str
    parent_id: str | None
    parent_document_id: str | None
    parent_page_start: int | None
    parent_page_end: int | None
    parent_text: str
    parent_token_count: int
    expansion_strategy: str
    expansion_order: int
    included: bool
    exclusion_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "child_chunk_id": self.child_chunk_id,
            "child_rank": self.child_rank,
            "child_score": self.child_score,
            "child_document_id": self.child_document_id,
            "child_page": self.child_page,
            "child_text": self.child_text,
            "parent_id": self.parent_id,
            "parent_document_id": self.parent_document_id,
            "parent_page_start": self.parent_page_start,
            "parent_page_end": self.parent_page_end,
            "parent_text": self.parent_text,
            "parent_token_count": self.parent_token_count,
            "expansion_strategy": self.expansion_strategy,
            "expansion_order": self.expansion_order,
            "included": self.included,
            "exclusion_reason": self.exclusion_reason,
        }


def expand(
    question_id: str,
    children: list[dict[str, Any]],
    *,
    strategy: str,
    loader: ParentLoader,
    max_parents: int = 3,
    max_context_tokens: int = 6000,
) -> list[ExpandedEvidence]:
    """Build ExpandedEvidence rows for one question under a fixed strategy.

    Children keep their original rank/score; parent expansion only adds
    context. All rules are deterministic; no LLM is consulted.
    """
    rows: list[ExpandedEvidence] = []
    if strategy == "none":
        for rank, child in enumerate(children, start=1):
            rows.append(
                _child_row(question_id, child, rank, strategy, order=rank)
            )
        return rows

    # Common deterministic expansion order: iterate children by original rank,
    # deduplicate parents, respect budget; children are never dropped.
    seen_parents: set[str] = set()
    child_token_total = 0
    parent_token_total = 0
    parent_order = 0
    for rank, child in enumerate(children, start=1):
        parent = loader.get_for_child(child)
        parent_id = parent.parent_chunk_id if parent else None
        parent_token = parent.token_count if parent else 0
        budget_ok = (
            child_token_total + parent_token_total + parent_token
        ) <= max_context_tokens
        if strategy == "top_1_parent":
            include_parent = rank == 1 and parent is not None
        elif strategy == "top_3_parents":
            include_parent = (
                parent is not None
                and parent_id not in seen_parents
                and len(seen_parents) < max_parents
            )
        else:  # adaptive (budget-driven, deterministic)
            include_parent = (
                parent is not None
                and parent_id not in seen_parents
                and len(seen_parents) < max_parents
                and budget_ok
            )
        exclusion_reason = None
        if parent is None and strategy != "none":
            exclusion_reason = "missing_parent"
        elif parent is not None and not include_parent:
            if parent_id in seen_parents:
                exclusion_reason = "duplicate_parent"
            elif strategy == "adaptive" and not budget_ok:
                exclusion_reason = "budget_exceeded"
            elif strategy in ("top_3_parents", "adaptive"):
                exclusion_reason = "max_parents_reached"
            else:
                exclusion_reason = "not_rank1"
        child_token = child.get("token_count", 0)
        child_token_total += child_token
        if include_parent and parent is not None:
            parent_order += 1
            parent_token_total += parent_token
            seen_parents.add(parent_id)
        # The original child is ALWAYS retained as its own evidence row.
        rows.append(
            _child_row(
                question_id,
                child,
                rank,
                strategy,
                order=rank,
                exclusion_reason=exclusion_reason,
            )
        )
        if include_parent and parent is not None:
            # Parent context is a separate row; it never replaces the child.
            rows.append(
                _parent_row(
                    question_id,
                    child,
                    rank,
                    strategy,
                    parent=parent,
                    order=parent_order,
                )
            )
    return rows


def _child_row(
    question_id: str,
    child: dict[str, Any],
    rank: int,
    strategy: str,
    *,
    order: int,
    exclusion_reason: str | None = None,
) -> ExpandedEvidence:
    return ExpandedEvidence(
        question_id=question_id,
        child_chunk_id=str(child.get("chunk_id", "")),
        child_rank=rank,
        child_score=child.get("retrieval_score"),
        child_document_id=str(child.get("document_id", "")),
        child_page=child.get("page_start"),
        child_text=str(child.get("embedding_content") or child.get("content") or ""),
        parent_id=None,
        parent_document_id=None,
        parent_page_start=None,
        parent_page_end=None,
        parent_text="",
        parent_token_count=0,
        expansion_strategy=strategy,
        expansion_order=order,
        included=True,
        exclusion_reason=exclusion_reason,
    )


def _parent_row(
    question_id: str,
    child: dict[str, Any],
    rank: int,
    strategy: str,
    *,
    parent: Any,
    order: int,
) -> ExpandedEvidence:
    return ExpandedEvidence(
        question_id=question_id,
        child_chunk_id=str(child.get("chunk_id", "")),
        child_rank=rank,
        child_score=child.get("retrieval_score"),
        child_document_id=str(child.get("document_id", "")),
        child_page=child.get("page_start"),
        child_text="",
        parent_id=parent.parent_chunk_id,
        parent_document_id=parent.document_name,
        parent_page_start=parent.page_start,
        parent_page_end=parent.page_end,
        parent_text=parent.content,
        parent_token_count=parent.token_count,
        expansion_strategy=strategy,
        expansion_order=order,
        included=True,
        exclusion_reason=None,
    )

"""Deterministic context assembly with a frozen token budget."""

from __future__ import annotations

from typing import Any

from industrial_rag.structured_chunker import count_tokens

from .expander import ExpandedEvidence


def build_context(
    rows: list[ExpandedEvidence],
    *,
    max_context_tokens: int = 6000,
) -> dict[str, Any]:
    """Assemble Section A (children) + Section B (parents) deterministically.

    Original children are always preserved; parents are added by
    expansion_order until the budget is exhausted. Parent text is never
    summarized or rewritten.
    """
    children = [row for row in rows if row.included and not row.parent_id]
    parents = [row for row in rows if row.included and row.parent_id]
    parents.sort(key=lambda row: (row.expansion_order, row.child_rank))

    child_texts = [row.child_text for row in children]
    parent_texts: list[str] = []
    total_tokens = sum(count_tokens(t) for t in child_texts)
    over_budget = 0
    excluded_parents: list[dict[str, Any]] = []
    for row in parents:
        parent_tokens = count_tokens(row.parent_text)
        if total_tokens + parent_tokens > max_context_tokens:
            over_budget += 1
            excluded_parents.append(
                {
                    "parent_id": row.parent_id,
                    "child_rank": row.child_rank,
                    "reason": "budget_exceeded",
                }
            )
            continue
        total_tokens += parent_tokens
        parent_texts.append(row.parent_text)

    section_a = "\n\n".join(child_texts)
    section_b = "\n\n".join(parent_texts)
    context = section_a
    if section_b:
        context = section_a + "\n\n[Parent Context]\n" + section_b if section_a else section_b
    duplicate_tokens = _duplicate_token_count(child_texts, parent_texts)
    return {
        "context": context,
        "token_count": count_tokens(context),
        "child_tokens": sum(count_tokens(t) for t in child_texts),
        "parent_tokens": sum(count_tokens(t) for t in parent_texts),
        "duplicate_tokens": duplicate_tokens,
        "duplicate_ratio": round(duplicate_tokens / max(1, total_tokens), 4),
        "over_budget_parents": over_budget,
        "excluded_parents": excluded_parents,
        "parent_count": len(parent_texts),
    }


def _duplicate_token_count(child_texts: list[str], parent_texts: list[str]) -> int:
    """Estimate duplicated tokens between child text and parent text."""
    child_tokens = set()
    for text in child_texts:
        child_tokens.update(_tokens(text))
    parent_token_total = 0
    for text in parent_texts:
        parent_token_total += sum(1 for token in _tokens(text) if token in child_tokens)
    return parent_token_total


def _tokens(text: str) -> list[str]:
    import re

    return re.findall(r"[\u3400-\u9fff]+|[a-z0-9]+", text.casefold())

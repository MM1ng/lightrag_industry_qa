"""Deterministic Phase 4C metrics: coverage, density, citations, bootstrap."""

from __future__ import annotations

import random
from collections.abc import Iterable
from typing import Any

from industrial_rag.structured_chunker import count_tokens


def bigrams(text: str) -> set[str]:
    normalized = "".join(text.split()).casefold()
    return {normalized[i : i + 2] for i in range(max(1, len(normalized) - 1))}


def percentile(values: list[int | float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, int(len(ordered) * pct))])


def context_token_stats(contexts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    tokens = [c["token_count"] for c in contexts]
    return {
        "mean": round(sum(tokens) / len(tokens), 1) if tokens else 0,
        "p50": percentile(tokens, 0.5),
        "p95": percentile(tokens, 0.95),
        "max": max(tokens) if tokens else 0,
    }


def expanded_gold_coverage(
    *,
    included_children: list[dict[str, Any]],
    included_parents: list[dict[str, Any]],
    mapped_child_ids: set[str],
    gold_pages: set[tuple[str, int]],
    gold_texts: list[str],
) -> dict[str, Any]:
    """Question-level deterministic coverage from the expanded context."""
    child_ids = {c.get("child_chunk_id") for c in included_children}
    evidence_hit = bool(child_ids & mapped_child_ids)
    # Parent text fallback: gold bigram coverage >= 0.5 in any included parent.
    parent_text = " ".join(p.get("parent_text", "") for p in included_parents)
    parent_grams = bigrams(parent_text)
    for gold_text in gold_texts:
        gold_grams = bigrams(gold_text)
        if gold_grams and parent_grams and len(gold_grams & parent_grams) / len(gold_grams) >= 0.5:
            evidence_hit = True
            break
    child_pages = {(c.get("child_document_id"), c.get("child_page")) for c in included_children}
    parent_pages = {
        (p.get("parent_document_id"), page)
        for p in included_parents
        for page in range(
            int(p.get("parent_page_start") or 0),
            int(p.get("parent_page_end") or int(p.get("parent_page_start") or 0)) + 1,
        )
    }
    page_hit = bool((child_pages | parent_pages) & gold_pages)
    return {"evidence_hit": evidence_hit, "page_hit": page_hit}


def context_evidence_density(context: str, gold_texts: list[str]) -> float:
    if not context.strip() or not gold_texts:
        return 0.0
    context_grams = bigrams(context)
    union_gold = set()
    for gold_text in gold_texts:
        union_gold |= bigrams(gold_text)
    if not context_grams or not union_gold:
        return 0.0
    return len(context_grams & union_gold) / len(context_grams)


def citation_metrics_from_rows(rows: list[dict[str, Any]], gold_pages_by_q: dict[str, set[tuple[str, int]]]) -> dict[str, Any]:
    """Deterministic citation metrics over per-question answer rows."""
    precision = recall = 0.0
    accuracy = 0
    traceable = 0
    unsupported = 0
    denom = 0
    for row in rows:
        if not row.get("expects_evidence"):
            continue
        citations = row.get("citations", [])
        expected = gold_pages_by_q.get(row["question_id"], set())
        citation_ids = {(c.get("source_file"), c.get("page_number")) for c in citations}
        if citations:
            denom += 1
            correct = len(citation_ids & expected)
            precision += correct / len(citation_ids)
            recall += correct / len(expected) if expected else 0.0
            accuracy += int(correct >= 1)
            traceable += int(all(c.get("chunk_id") for c in citations))
            unsupported += int(not citation_ids)
        else:
            denom += 1
    return {
        "citation_accuracy": round(accuracy / denom, 4) if denom else None,
        "citation_precision": round(precision / denom, 4) if denom else None,
        "citation_recall": round(recall / denom, 4) if denom else None,
        "citation_traceability": round(traceable / denom, 4) if denom else None,
        "unsupported_citation_rate": round(unsupported / denom, 4) if denom else None,
    }


def paired_bootstrap(
    base: list[float],
    candidate: list[float],
    *,
    n_iter: int = 1000,
    seed: int = 20260801,
) -> dict[str, Any]:
    """Paired bootstrap of mean differences with a fixed seed."""
    if not base or len(base) != len(candidate):
        return {"n": 0, "mean_diff": None, "ci95": None}
    rng = random.Random(seed)
    diffs: list[float] = []
    for _ in range(n_iter):
        indices = [rng.randrange(len(base)) for _ in range(len(base))]
        diffs.append(
            sum(candidate[i] - base[i] for i in indices) / len(indices)
        )
    diffs.sort()
    lo = diffs[int(len(diffs) * 0.025)]
    hi = diffs[int(len(diffs) * 0.975)]
    return {
        "n": len(base),
        "mean_diff": round(sum(candidate[i] - base[i] for i in range(len(base))) / len(base), 4),
        "ci95": [round(lo, 4), round(hi, 4)],
        "crosses_zero": lo < 0 < hi,
    }

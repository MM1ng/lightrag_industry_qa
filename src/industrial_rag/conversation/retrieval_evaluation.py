"""Pure metrics for the Development conversation retrieval experiment."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any


def ranked_chunk_ids(initial_results: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Return unique chunk IDs in first-retrieval order."""

    result: list[str] = []
    seen: set[str] = set()
    for item in initial_results:
        chunk_id = item.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id or chunk_id in seen:
            continue
        seen.add(chunk_id)
        result.append(chunk_id)
    return tuple(result)


def _ranks(ranked_ids: Sequence[str], gold_ids: Collection[str]) -> dict[str, int]:
    return {
        chunk_id: index
        for index, chunk_id in enumerate(ranked_ids, start=1)
        if chunk_id in gold_ids
    }


def compute_retrieval_metrics(
    ranked_ids: Sequence[str],
    gold_ids: Collection[str],
    ks: Sequence[int] = (5, 10),
) -> dict[str, float]:
    """Compute hit recall, evidence recall, and first-hit MRR at each K."""

    gold = {str(item) for item in gold_ids if str(item)}
    if not gold:
        raise ValueError("gold chunk IDs are required")
    ranks = _ranks(ranked_ids, gold)
    output: dict[str, float] = {}
    for k in ks:
        if k <= 0:
            raise ValueError("metric K must be positive")
        hits = [rank for rank in ranks.values() if rank <= k]
        output[f"hit_recall_at_{k}"] = 1.0 if hits else 0.0
        output[f"evidence_recall_at_{k}"] = len(hits) / len(gold)
        output[f"mrr_at_{k}"] = 1.0 / min(hits) if hits else 0.0
    return output


def compare_retrieval_metrics(
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
    gold_ids: Collection[str],
) -> dict[str, Any]:
    before_ids = ranked_chunk_ids(before)
    after_ids = ranked_chunk_ids(after)
    before_metrics = compute_retrieval_metrics(before_ids, gold_ids)
    after_metrics = compute_retrieval_metrics(after_ids, gold_ids)
    before_ranks = _ranks(before_ids, set(gold_ids))
    after_ranks = _ranks(after_ids, set(gold_ids))
    before_first = min(before_ranks.values(), default=None)
    after_first = min(after_ranks.values(), default=None)
    before_hit = before_first is not None
    after_hit = after_first is not None
    improved = (not before_hit and after_hit) or (
        before_hit and after_hit and after_first < before_first
    )
    regressed = (before_hit and not after_hit) or (
        before_hit and after_hit and after_first > before_first
    )
    return {
        "before": before_metrics,
        "after": after_metrics,
        "delta": {
            key: after_metrics[key] - before_metrics[key]
            for key in before_metrics
        },
        "before_ranks": before_ranks,
        "after_ranks": after_ranks,
        "improved": improved,
        "regressed": regressed,
        "unchanged": not improved and not regressed,
    }


def aggregate_metric_rows(rows: Sequence[Mapping[str, float]]) -> dict[str, float | None]:
    if not rows:
        return {}
    keys = tuple(rows[0])
    return {key: sum(float(row[key]) for row in rows) / len(rows) for key in keys}

"""Deterministic Ragas metrics preserving the historical ID semantics."""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from typing import Any

from ragas.dataset_schema import SingleTurnSample
from ragas.metrics import (
    IDBasedContextPrecision,
    IDBasedContextRecall,
    MetricResult,
    numeric_metric,
)

from industrial_rag.conversation.retrieval_evaluation import (
    compute_retrieval_metrics,
    ranked_chunk_ids,
)


def _decoded(value: str) -> list[str]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise TypeError("metric ID input must be a JSON list")
    return [str(item) for item in parsed if str(item)]


def _metric_result(name: str, value: float, retrieved: list[str], reference: list[str]) -> MetricResult:
    unique_retrieved = list(dict.fromkeys(retrieved))
    gold = list(dict.fromkeys(reference))
    gold_ranks = {
        item: index for index, item in enumerate(unique_retrieved, start=1) if item in set(gold)
    }
    first_rank = min(gold_ranks.values(), default=None)
    return MetricResult(
        value=value,
        reason=f"{name} uses exact retrieved/reference chunk ID membership and rank.",
        traces={
            "input": {
                "retrieved_context_ids": retrieved,
                "reference_context_ids": reference,
            },
            "output": {
                "metric": name,
                "unique_retrieved_count": len(unique_retrieved),
                "reference_count": len(gold),
                "gold_ranks": gold_ranks,
                "first_gold_rank": first_rank,
            },
        },
    )


def _metric(name: str, k: int, retrieved_json: str, reference_json: str) -> MetricResult:
    retrieved = _decoded(retrieved_json)
    reference = _decoded(reference_json)
    values = compute_retrieval_metrics(retrieved, reference, ks=(k,))
    return _metric_result(name, values[name], retrieved, reference)


@numeric_metric(name="hit_recall_at_5")
def hit_recall_at_5(retrieved_context_ids: str, reference_context_ids: str) -> MetricResult:
    return _metric("hit_recall_at_5", 5, retrieved_context_ids, reference_context_ids)


@numeric_metric(name="hit_recall_at_10")
def hit_recall_at_10(retrieved_context_ids: str, reference_context_ids: str) -> MetricResult:
    return _metric("hit_recall_at_10", 10, retrieved_context_ids, reference_context_ids)


@numeric_metric(name="evidence_recall_at_5")
def evidence_recall_at_5(retrieved_context_ids: str, reference_context_ids: str) -> MetricResult:
    return _metric("evidence_recall_at_5", 5, retrieved_context_ids, reference_context_ids)


@numeric_metric(name="evidence_recall_at_10")
def evidence_recall_at_10(retrieved_context_ids: str, reference_context_ids: str) -> MetricResult:
    return _metric("evidence_recall_at_10", 10, retrieved_context_ids, reference_context_ids)


@numeric_metric(name="mrr_at_5")
def mrr_at_5(retrieved_context_ids: str, reference_context_ids: str) -> MetricResult:
    return _metric("mrr_at_5", 5, retrieved_context_ids, reference_context_ids)


@numeric_metric(name="mrr_at_10")
def mrr_at_10(retrieved_context_ids: str, reference_context_ids: str) -> MetricResult:
    return _metric("mrr_at_10", 10, retrieved_context_ids, reference_context_ids)


CUSTOM_METRICS = (
    hit_recall_at_5,
    hit_recall_at_10,
    evidence_recall_at_5,
    evidence_recall_at_10,
    mrr_at_5,
    mrr_at_10,
)


def score_retrieval_metric_results(
    retrieved_ids: Sequence[str], reference_ids: Collection[str]
) -> dict[str, MetricResult]:
    retrieved = list(ranked_chunk_ids([{"chunk_id": item} for item in retrieved_ids]))
    reference = [str(item) for item in reference_ids if str(item)]
    encoded_retrieved = json.dumps(retrieved, ensure_ascii=False)
    encoded_reference = json.dumps(reference, ensure_ascii=False)
    return {
        metric.name: metric.score(
            retrieved_context_ids=encoded_retrieved,
            reference_context_ids=encoded_reference,
        )
        for metric in CUSTOM_METRICS
    }


def score_retrieval_metrics(
    retrieved_ids: Sequence[str], reference_ids: Collection[str]
) -> dict[str, float]:
    return {name: float(result.value) for name, result in score_retrieval_metric_results(retrieved_ids, reference_ids).items()}


async def score_official_id_metrics(
    retrieved_ids: Sequence[str], reference_ids: Collection[str], k: int
) -> dict[str, float]:
    sample = SingleTurnSample(
        retrieved_context_ids=list(ranked_chunk_ids([{"chunk_id": item} for item in retrieved_ids]))[:k],
        reference_context_ids=[str(item) for item in reference_ids if str(item)],
    )
    recall = await IDBasedContextRecall().single_turn_ascore(sample)
    precision = await IDBasedContextPrecision().single_turn_ascore(sample)
    return {
        "id_based_context_recall": float(recall),
        "id_based_context_precision": float(precision),
    }

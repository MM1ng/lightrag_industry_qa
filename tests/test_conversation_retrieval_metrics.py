from __future__ import annotations

import pytest
from industrial_rag.conversation.retrieval_evaluation import (
    compare_retrieval_metrics,
    compute_retrieval_metrics,
    ranked_chunk_ids,
)


def test_metrics_cover_hit_evidence_recall_and_mrr_for_multiple_gold_chunks() -> None:
    ranked = ["a", "gold-2", "a", "gold-1", "other"]

    assert ranked_chunk_ids([{"chunk_id": item} for item in ranked]) == (
        "a",
        "gold-2",
        "gold-1",
        "other",
    )
    assert compute_retrieval_metrics(
        ranked_chunk_ids([{"chunk_id": item} for item in ranked]),
        {"gold-1", "gold-2"},
    ) == {
        "hit_recall_at_5": 1.0,
        "evidence_recall_at_5": 1.0,
        "mrr_at_5": 0.5,
        "hit_recall_at_10": 1.0,
        "evidence_recall_at_10": 1.0,
        "mrr_at_10": 0.5,
    }


def test_metrics_return_zero_for_misses_and_reject_empty_gold() -> None:
    assert compute_retrieval_metrics(["a", "b"], {"gold"}) == {
        "hit_recall_at_5": 0.0,
        "evidence_recall_at_5": 0.0,
        "mrr_at_5": 0.0,
        "hit_recall_at_10": 0.0,
        "evidence_recall_at_10": 0.0,
        "mrr_at_10": 0.0,
    }
    with pytest.raises(ValueError, match="gold"):
        compute_retrieval_metrics([], set())


def test_comparison_reports_deltas_and_changed_ranks() -> None:
    result = compare_retrieval_metrics(
        [{"chunk_id": "noise"}, {"chunk_id": "gold"}],
        [{"chunk_id": "gold"}],
        {"gold"},
    )

    assert result["before"]["mrr_at_5"] == 0.5
    assert result["after"]["mrr_at_5"] == 1.0
    assert result["delta"]["mrr_at_5"] == 0.5
    assert result["before_ranks"] == {"gold": 2}
    assert result["after_ranks"] == {"gold": 1}
    assert result["improved"] is True
    assert result["regressed"] is False

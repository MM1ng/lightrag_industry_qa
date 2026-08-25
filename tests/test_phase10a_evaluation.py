from __future__ import annotations

import pytest
from industrial_rag.phase10_evaluation import diagnose_case, evaluate_retrieval


def _evidence(
    evidence_id: str,
    chunk: str,
    page: int,
    grade: int = 2,
    document: str = "manual.pdf",
) -> dict:
    return {
        "evidence_id": evidence_id,
        "document_name": document,
        "page_number": page,
        "chunk_id": chunk,
        "evidence_text": "evidence",
        "role": "primary" if grade == 2 else "supporting",
        "relevance_grade": grade,
    }


def _case(
    question_id: str,
    *,
    expected: list[dict],
    retrieved: list[tuple[str, int]],
    selected: list[str],
    status: str,
    cited: list[tuple[str, int]],
    answerable: bool = True,
) -> dict:
    return {
        "golden": {
            "question_id": question_id,
            "question": "question",
            "answerable": answerable,
            "expected_evidence": expected,
            "expected_answer_points": [],
            "question_type": "parameter" if answerable else "negative",
            "difficulty": "medium",
            "split": "development",
        },
        "response": {
            "status": status,
            "citations": [
                {"document_name": "manual.pdf", "page": page, "chunk_id": chunk}
                for chunk, page in cited
            ],
        },
        "trace": {
            "initial_results": [
                {
                    "initial_rank": rank,
                    "document_name": "manual.pdf",
                    "page_number": page,
                    "chunk_id": chunk,
                }
                for rank, (chunk, page) in enumerate(retrieved, start=1)
            ],
            "final_selected_chunks": [
                {"final_rank": rank, "chunk_id": chunk}
                for rank, chunk in enumerate(selected, start=1)
            ],
            "retrieval_ms": 10.0,
            "rerank_ms": 0.0,
            "end_to_end_ms": 50.0,
        },
    }


def test_metrics_freeze_positive_denominators_multi_evidence_and_graded_rank() -> None:
    """Catches negatives entering recall or any/complete evidence collapsing together."""
    positive = _case(
        "P1",
        expected=[_evidence("e1", "gold-a", 7, 2), _evidence("e2", "gold-b", 8, 1)],
        retrieved=[("distractor", 1), ("gold-a", 7), ("other", 9)],
        selected=["gold-a"],
        status="success",
        cited=[("gold-a", 7)],
    )
    negative = _case(
        "N1",
        expected=[],
        retrieved=[],
        selected=[],
        status="insufficient_evidence",
        cited=[],
        answerable=False,
    )

    overall = evaluate_retrieval([positive, negative])["overall"]

    assert overall["chunk_recall_at_1"] == {
        "numerator": 0,
        "denominator": 2,
        "value": 0.0,
    }
    assert overall["chunk_recall_at_3"] == {
        "numerator": 1,
        "denominator": 2,
        "value": 0.5,
    }
    assert overall["any_evidence_recall_at_3"] == {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0,
    }
    assert overall["complete_evidence_recall_at_3"] == {
        "numerator": 0,
        "denominator": 1,
        "value": 0.0,
    }
    assert overall["mrr"] == {"numerator": 0.5, "denominator": 1, "value": 0.5}
    assert overall["graded_ndcg_at_10"]["denominator"] == 1
    assert overall["negative_rejection_rate"] == {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0,
    }
    assert overall["claim_level_citation_accuracy"] == {
        "available": False,
        "numerator": 0,
        "denominator": 0,
        "value": None,
    }


def test_empty_denominators_are_null_and_all_rates_keep_counts() -> None:
    """Catches zero-filled undefined rates or missing numerator/denominator evidence."""
    overall = evaluate_retrieval([])["overall"]
    for name, metric in overall.items():
        if name in {"latency_ms", "claim_level_citation_accuracy"}:
            continue
        assert set(metric) == {"numerator", "denominator", "value"}
        assert metric["denominator"] == 0
        assert metric["value"] is None


@pytest.mark.parametrize(
    ("case", "layer", "category"),
    [
        (
            _case(
                "wrong-doc",
                expected=[_evidence("e1", "gold", 7, document="expected.pdf")],
                retrieved=[("other", 1)],
                selected=[],
                status="insufficient_evidence",
                cited=[],
            ),
            "Retrieval Error",
            "wrong_document",
        ),
        (
            _case(
                "rank-low",
                expected=[_evidence("e1", "gold", 7)],
                retrieved=[(f"other-{i}", 1) for i in range(10)] + [("gold", 7)],
                selected=[],
                status="insufficient_evidence",
                cited=[],
            ),
            "Ranking Error",
            "correct_chunk_rank_too_low",
        ),
        (
            _case(
                "refused",
                expected=[_evidence("e1", "gold", 7)],
                retrieved=[("gold", 7)],
                selected=["gold"],
                status="insufficient_evidence",
                cited=[],
            ),
            "Refusal Decision Error",
            "evidence_threshold_too_high",
        ),
        (
            _case(
                "citation",
                expected=[_evidence("e1", "gold", 7)],
                retrieved=[("gold", 7)],
                selected=["gold"],
                status="success",
                cited=[("wrong", 9)],
            ),
            "Citation Error",
            "citation_binding_failure",
        ),
    ],
)
def test_diagnosis_uses_deterministic_failure_precedence(
    case: dict, layer: str, category: str
) -> None:
    """Catches generic model-error labels that hide the actual failed pipeline stage."""
    diagnosis = diagnose_case(case)
    assert diagnosis["failure_layer"] == layer
    assert diagnosis["failure_category"] == category

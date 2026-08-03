"""Frozen Phase 10A retrieval metrics and deterministic failure diagnosis."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

RECALL_K = (1, 3, 5, 10, 20)


def _rate(numerator: float | int, denominator: int) -> dict[str, float | int | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": None if denominator == 0 else numerator / denominator,
    }


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


def _metric_group(cases: list[dict[str, Any]]) -> dict[str, Any]:
    positives = [case for case in cases if case["golden"]["answerable"]]
    negatives = [case for case in cases if not case["golden"]["answerable"]]
    metrics: dict[str, Any] = {}

    expected_chunk_total = sum(
        len(case["golden"]["expected_evidence"]) for case in positives
    )
    for k in RECALL_K:
        chunk_hits = 0
        any_hits = 0
        complete_hits = 0
        document_hits = 0
        page_hits = 0
        for case in positives:
            expected = case["golden"]["expected_evidence"]
            retrieved = (case.get("trace") or {}).get("initial_results", [])[:k]
            retrieved_chunks = {item.get("chunk_id") for item in retrieved}
            retrieved_documents = {item.get("document_name") for item in retrieved}
            retrieved_pages = {
                (item.get("document_name"), item.get("page_number")) for item in retrieved
            }
            expected_chunks = {item["chunk_id"] for item in expected}
            expected_documents = {item["document_name"] for item in expected}
            expected_pages = {
                (item["document_name"], item["page_number"]) for item in expected
            }
            hits = len(expected_chunks & retrieved_chunks)
            chunk_hits += hits
            any_hits += int(hits > 0)
            complete_hits += int(bool(expected_chunks) and expected_chunks <= retrieved_chunks)
            document_hits += int(bool(expected_documents & retrieved_documents))
            page_hits += int(bool(expected_pages & retrieved_pages))
        metrics[f"chunk_recall_at_{k}"] = _rate(chunk_hits, expected_chunk_total)
        metrics[f"any_evidence_recall_at_{k}"] = _rate(any_hits, len(positives))
        metrics[f"complete_evidence_recall_at_{k}"] = _rate(
            complete_hits, len(positives)
        )
        metrics[f"document_recall_at_{k}"] = _rate(document_hits, len(positives))
        metrics[f"page_recall_at_{k}"] = _rate(page_hits, len(positives))

    reciprocal_rank_sum = 0.0
    ndcg_sum = 0.0
    for case in positives:
        expected = case["golden"]["expected_evidence"]
        relevance = {item["chunk_id"]: int(item["relevance_grade"]) for item in expected}
        retrieved = (case.get("trace") or {}).get("initial_results", [])
        first_rank = next(
            (
                int(item["initial_rank"])
                for item in retrieved
                if item.get("chunk_id") in relevance
            ),
            None,
        )
        if first_rank is not None:
            reciprocal_rank_sum += 1 / first_rank
        dcg = sum(
            (2 ** relevance.get(item.get("chunk_id"), 0) - 1)
            / math.log2(rank + 1)
            for rank, item in enumerate(retrieved[:10], start=1)
        )
        ideal = sorted(relevance.values(), reverse=True)[:10]
        idcg = sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(ideal, 1))
        ndcg_sum += 0.0 if idcg == 0 else dcg / idcg
    metrics["mrr"] = _rate(reciprocal_rank_sum, len(positives))
    metrics["graded_ndcg_at_10"] = _rate(ndcg_sum, len(positives))

    false_rejections = sum(
        case["response"].get("status") in {"insufficient_evidence", "safety_blocked"}
        for case in positives
    )
    negative_rejections = sum(
        case["response"].get("status") == "insufficient_evidence" for case in negatives
    )
    # Both complete and partial answers are substantive answers.  Refusals and
    # failed executions must not disappear from the quality denominators.
    answered = [
        case
        for case in cases
        if case["response"].get("status") in {"success", "partial_answer"}
    ]
    unsupported = 0
    citation_correct = 0
    citation_denominator = 0
    for case in answered:
        expected_chunks = {
            item["chunk_id"] for item in case["golden"]["expected_evidence"]
        }
        cited_chunks = {
            item.get("chunk_id") for item in case["response"].get("citations", [])
        }
        has_support = bool(expected_chunks & cited_chunks)
        unsupported += int(not has_support)
        if case["golden"]["answerable"]:
            citation_denominator += 1
            citation_correct += int(has_support)
    metrics["false_rejection_rate"] = _rate(false_rejections, len(positives))
    metrics["negative_rejection_rate"] = _rate(negative_rejections, len(negatives))
    metrics["unsupported_answer_rate"] = _rate(unsupported, len(answered))
    metrics["question_level_citation_accuracy"] = _rate(
        citation_correct, citation_denominator
    )
    metrics["citation_trace_completeness"] = _rate(
        sum(case.get("trace") is not None for case in cases), len(cases)
    )
    metrics["claim_level_citation_accuracy"] = {
        "available": False,
        "numerator": 0,
        "denominator": 0,
        "value": None,
    }
    metrics["latency_ms"] = {
        stage: {
            "p50": _percentile(values, 0.5),
            "p95": _percentile(values, 0.95),
            "count": len(values),
        }
        for stage, values in {
            "retrieval": [
                float(case["trace"]["retrieval_ms"])
                for case in cases
                if case.get("trace") and case["trace"].get("retrieval_ms") is not None
            ],
            "rerank": [
                float(case["trace"]["rerank_ms"])
                for case in cases
                if case.get("trace") and case["trace"].get("rerank_ms") is not None
            ],
            "end_to_end": [
                float(case["trace"]["end_to_end_ms"])
                for case in cases
                if case.get("trace") and case["trace"].get("end_to_end_ms") is not None
            ],
        }.items()
    }
    return metrics


def evaluate_retrieval(cases: list[dict[str, Any]]) -> dict[str, Any]:
    breakdown_fields = {
        "split": "split",
        "question_type": "question_type",
        "difficulty": "difficulty",
        "answerability": "answerable",
    }
    breakdowns: dict[str, dict[str, Any]] = {}
    for output_name, field in breakdown_fields.items():
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for case in cases:
            grouped[str(case["golden"].get(field))].append(case)
        breakdowns[output_name] = {
            key: _metric_group(group) for key, group in sorted(grouped.items())
        }
    document_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        documents = {
            item["document_name"] for item in case["golden"]["expected_evidence"]
        } or {"none"}
        for document in documents:
            document_groups[document].append(case)
    breakdowns["document"] = {
        key: _metric_group(group) for key, group in sorted(document_groups.items())
    }
    return {"overall": _metric_group(cases), "breakdowns": breakdowns}


def diagnose_case(case: dict[str, Any]) -> dict[str, Any]:
    golden = case["golden"]
    base = {
        "question_id": golden["question_id"],
        "question": golden["question"],
        "failure_layer": None,
        "failure_category": None,
        "failure_reason": None,
    }
    trace = case.get("trace")
    if trace is None:
        return {
            **base,
            "failure_layer": "Retrieval Error",
            "failure_category": "retrieval_trace_missing",
            "failure_reason": "ordinary request completed without a readable trace",
        }
    if not golden["answerable"]:
        if case["response"].get("status") == "success":
            return {
                **base,
                "failure_layer": "Refusal Decision Error",
                "failure_category": "negative_not_rejected",
                "failure_reason": "unanswerable question received an answer",
            }
        return base

    expected = golden["expected_evidence"]
    retrieved = trace.get("initial_results", [])
    expected_documents = {item["document_name"] for item in expected}
    expected_pages = {(item["document_name"], item["page_number"]) for item in expected}
    expected_chunks = {item["chunk_id"] for item in expected}
    retrieved_documents = {item.get("document_name") for item in retrieved}
    retrieved_pages = {
        (item.get("document_name"), item.get("page_number")) for item in retrieved
    }
    retrieved_chunks = {item.get("chunk_id") for item in retrieved}
    if not expected_documents & retrieved_documents:
        return _failure(base, "Retrieval Error", "wrong_document")
    if not expected_pages & retrieved_pages:
        return _failure(base, "Retrieval Error", "page_not_recalled")
    if not expected_chunks & retrieved_chunks:
        return _failure(base, "Retrieval Error", "chunk_not_recalled")
    first_rank = min(
        int(item["initial_rank"])
        for item in retrieved
        if item.get("chunk_id") in expected_chunks
    )
    if first_rank > 10:
        return _failure(base, "Ranking Error", "correct_chunk_rank_too_low")
    selected_chunks = {
        item.get("chunk_id") for item in trace.get("final_selected_chunks", [])
    }
    if not expected_chunks & selected_chunks:
        return _failure(base, "Evidence Selection Error", "expected_evidence_not_selected")
    if case["response"].get("status") == "insufficient_evidence":
        return _failure(base, "Refusal Decision Error", "evidence_threshold_too_high")
    cited_chunks = {
        item.get("chunk_id") for item in case["response"].get("citations", [])
    }
    if not expected_chunks & cited_chunks:
        return _failure(base, "Citation Error", "citation_binding_failure")
    if not case["response"].get("answer") and case["response"].get("status") == "success":
        return _failure(base, "Answer Generation Error", "generation_extraction_failure")
    return base


def _failure(base: dict[str, Any], layer: str, category: str) -> dict[str, Any]:
    return {
        **base,
        "failure_layer": layer,
        "failure_category": category,
        "failure_reason": category.replace("_", " "),
    }

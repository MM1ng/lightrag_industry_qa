"""Deterministic Phase 10B failure classification for dev/validation traces."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from industrial_rag.evidence_policy import _tokens

ANALYZED_SPLITS = frozenset({"development", "validation"})
FAILURE_LAYERS = (
    "Retrieval",
    "Ranking",
    "Evidence Selection",
    "Generation",
    "Refusal",
    "Citation",
    "None",
)
FAILURE_CATEGORIES = (
    "wrong_document",
    "page_not_recalled",
    "chunk_not_recalled",
    "correct_chunk_rank_too_low",
    "evidence_not_selected",
    "table_parse_failure",
    "cross_page_context_missing",
    "query_term_mismatch",
    "metadata_filter_failure",
    "evidence_threshold_too_high",
    "generation_extraction_failure",
    "citation_binding_failure",
    "none",
)


@dataclass(frozen=True)
class FailureClassification:
    failure_layer: str
    failure_category: str
    failure_reason: str
    expected_evidence_recalled_count: int
    expected_evidence_selected_count: int
    initial_rank: dict[str, int | None]


def _golden(case: dict[str, Any]) -> dict[str, Any]:
    return case["golden"]


def _trace(case: dict[str, Any]) -> dict[str, Any]:
    return case["trace"]


def _expected_ids(golden: dict[str, Any]) -> set[str]:
    return {item["chunk_id"] for item in golden.get("expected_evidence", [])}


def _initial_map(trace: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["chunk_id"]: item for item in trace.get("initial_results", [])}


def _selected_map(trace: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["chunk_id"]: item for item in trace.get("final_selected_chunks", [])}


def _citation_ids(case: dict[str, Any]) -> set[str]:
    return {
        citation.get("chunk_id")
        for citation in case.get("response", {}).get("citations", [])
        if citation.get("chunk_id")
    }


def _classification(
    *,
    layer: str,
    category: str,
    reason: str,
    recalled: set[str],
    selected: set[str],
    initial: dict[str, dict[str, Any]],
    expected: set[str],
) -> FailureClassification:
    return FailureClassification(
        failure_layer=layer,
        failure_category=category,
        failure_reason=reason,
        expected_evidence_recalled_count=len(recalled),
        expected_evidence_selected_count=len(selected),
        initial_rank={
            chunk_id: initial.get(chunk_id, {}).get("initial_rank")
            for chunk_id in sorted(expected)
        },
    )


def _answer_has_point_support(case: dict[str, Any], golden: dict[str, Any]) -> bool:
    answer_tokens = _tokens(case.get("response", {}).get("answer", ""))
    points = golden.get("expected_answer_points", [])
    if not points:
        return True
    supported = 0
    for point in points:
        point_tokens = _tokens(point.get("text", ""))
        if point_tokens and len(point_tokens & answer_tokens) >= max(1, min(3, len(point_tokens))):
            supported += 1
    return supported == len(points)


def classify_failure(case: dict[str, Any]) -> FailureClassification:
    golden = _golden(case)
    trace = _trace(case)
    expected = _expected_ids(golden)
    initial = _initial_map(trace)
    selected = _selected_map(trace)
    initial_ids = set(initial)
    selected_ids = set(selected)
    recalled = expected & initial_ids
    selected_expected = expected & selected_ids
    citations = _citation_ids(case)
    status = case.get("response", {}).get("status")
    if not golden.get("answerable", True):
        return _classification(
            layer="None",
            category="none",
            reason="negative sample has no expected evidence and is outside retrieval failure denominators",
            recalled=recalled,
            selected=selected_expected,
            initial=initial,
            expected=expected,
        )

    missing = expected - recalled
    if missing:
        config = trace.get("retrieval_config", {})
        if config.get("metadata_filter"):
            category = "metadata_filter_failure"
        else:
            expected_documents = {
                item["document_name"] for item in golden.get("expected_evidence", [])
            }
            initial_documents = {
                item.get("document_name") for item in trace.get("initial_results", [])
            }
            expected_pages = {
                item["page_number"]
                for item in golden.get("expected_evidence", [])
            }
            initial_pages = {
                item.get("page_number") for item in trace.get("initial_results", [])
            }
            if not expected_documents & initial_documents:
                category = "wrong_document"
            elif not expected_pages & initial_pages:
                category = "page_not_recalled"
            elif golden.get("question_type") == "table":
                category = "table_parse_failure"
            elif golden.get("question_type") in {"cross_page", "multi_evidence"}:
                category = "cross_page_context_missing"
            elif not any(item.get("matched_terms") for item in trace.get("initial_results", [])):
                category = "query_term_mismatch"
            else:
                category = "chunk_not_recalled"
        return _classification(
            layer="Retrieval",
            category=category,
            reason=f"{len(missing)} expected evidence chunk(s) absent from initial LightRAG candidates",
            recalled=recalled,
            selected=selected_expected,
            initial=initial,
            expected=expected,
        )

    low_rank = [
        chunk_id
        for chunk_id in expected
        if (initial[chunk_id].get("initial_rank") or 0) > 5
    ]
    if low_rank and selected_expected != expected:
        return _classification(
            layer="Ranking",
            category="correct_chunk_rank_too_low",
            reason=f"expected evidence is recalled but {len(low_rank)} chunk(s) rank below 5",
            recalled=recalled,
            selected=selected_expected,
            initial=initial,
            expected=expected,
        )

    if selected_expected != expected:
        category = (
            "evidence_threshold_too_high"
            if status == "insufficient_evidence" and not selected_expected
            else "evidence_not_selected"
        )
        return _classification(
            layer="Evidence Selection",
            category=category,
            reason=f"{len(expected - selected_expected)} expected evidence chunk(s) were recalled but not selected",
            recalled=recalled,
            selected=selected_expected,
            initial=initial,
            expected=expected,
        )

    if status == "insufficient_evidence":
        return _classification(
            layer="Refusal",
            category="evidence_threshold_too_high",
            reason="all expected evidence was selected but the answer was refused",
            recalled=recalled,
            selected=selected_expected,
            initial=initial,
            expected=expected,
        )

    if citations & expected != expected:
        return _classification(
            layer="Citation",
            category="citation_binding_failure",
            reason=f"{len(expected - citations)} expected evidence chunk(s) are absent from final citations",
            recalled=recalled,
            selected=selected_expected,
            initial=initial,
            expected=expected,
        )

    if status == "success" and not _answer_has_point_support(case, golden):
        return _classification(
            layer="Generation",
            category="generation_extraction_failure",
            reason="answer text does not contain deterministic token support for every expected answer point",
            recalled=recalled,
            selected=selected_expected,
            initial=initial,
            expected=expected,
        )

    return _classification(
        layer="None",
        category="none",
        reason="expected evidence was recalled, selected, cited, and answer-point support was observed",
        recalled=recalled,
        selected=selected_expected,
        initial=initial,
        expected=expected,
    )


def _case_row(case: dict[str, Any], classification: FailureClassification) -> dict[str, Any]:
    golden = _golden(case)
    trace = _trace(case)
    response = case.get("response", {})
    expected = golden.get("expected_evidence", [])
    selected_ids = {item["chunk_id"] for item in trace.get("final_selected_chunks", [])}
    citations = response.get("citations", [])
    evidence_details = [
        {
            **evidence,
            "initial_rank": classification.initial_rank.get(evidence["chunk_id"]),
            "selected": evidence["chunk_id"] in selected_ids,
            "citation_count": sum(
                citation.get("chunk_id") == evidence["chunk_id"] for citation in citations
            ),
        }
        for evidence in expected
    ]
    return {
        "question_id": golden["question_id"],
        "split": golden["split"],
        "question_type": golden["question_type"],
        "difficulty": golden["difficulty"],
        "expected_evidence": evidence_details,
        "expected_answer_points": golden.get("expected_answer_points", []),
        "initial_rank": classification.initial_rank,
        "expected_evidence_recalled_count": classification.expected_evidence_recalled_count,
        "expected_evidence_selected_count": classification.expected_evidence_selected_count,
        "final_citation_count": len(citations),
        "citations": citations,
        "answer_status": response.get("status"),
        "failure_layer": classification.failure_layer,
        "failure_category": classification.failure_category,
        "failure_reason": classification.failure_reason,
    }


def build_failure_matrix(
    results: Iterable[dict[str, Any]], diagnoses: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    del diagnoses
    rows = []
    for case in results:
        if _golden(case).get("split") not in ANALYZED_SPLITS:
            continue
        rows.append(_case_row(case, classify_failure(case)))
    return sorted(rows, key=lambda row: row["question_id"])


def _group_summary(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if key == "document":
            documents = {
                item["document_name"] for item in row["expected_evidence"]
            } or {"none"}
            for document in documents:
                grouped[document].append(row)
        else:
            grouped[str(row[key])].append(row)
    return {
        group: {
            "question_count": len(items),
            "failure_count": sum(item["failure_category"] != "none" for item in items),
            "failure_rate": {
                "numerator": sum(item["failure_category"] != "none" for item in items),
                "denominator": len(items),
                "value": (
                    sum(item["failure_category"] != "none" for item in items) / len(items)
                    if items
                    else None
                ),
            },
            "failure_layers": dict(Counter(item["failure_layer"] for item in items)),
            "failure_categories": dict(Counter(item["failure_category"] for item in items)),
        }
        for group, items in sorted(grouped.items())
    }


def summarize_failure_matrix(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    materialized = list(rows)
    category_counts = Counter(row["failure_category"] for row in materialized)
    layer_counts = Counter(row["failure_layer"] for row in materialized)
    for category in FAILURE_CATEGORIES:
        category_counts.setdefault(category, 0)
    for layer in FAILURE_LAYERS:
        layer_counts.setdefault(layer, 0)
    return {
        "analysis_version": "phase10b-failure-matrix-v1",
        "analyzed_question_count": len(materialized),
        "split_counts": dict(Counter(row["split"] for row in materialized)),
        "holdout_rows_loaded": False,
        "failure_category_counts": dict(sorted(category_counts.items())),
        "failure_layer_counts": dict(sorted(layer_counts.items())),
        "groupings": {
            key: _group_summary(materialized, key)
            for key in ("question_type", "difficulty", "document", "split", "failure_layer")
        },
    }

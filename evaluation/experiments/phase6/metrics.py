"""Phase 6 official E2E metrics (retrieval, citation, rejection, engineering)."""

from __future__ import annotations

from typing import Any

from .config import EVIDENCE_MAPPING_PATH, read_jsonl

NEGATIVE_IDS = ("N001", "N002")


def _fmt(numerator: Any, denominator: Any) -> dict[str, Any]:
    if isinstance(numerator, (int, float)) and isinstance(denominator, (int, float)) and denominator:
        decimal = round(numerator / denominator, 4)
    else:
        decimal = None
    return {
        "numerator": numerator,
        "denominator": denominator,
        "decimal": decimal,
        "percentage": round(decimal * 100, 2) if decimal is not None else None,
    }


def gold_sets() -> tuple[dict[str, set[tuple[str, int]]], dict[str, set[str]]]:
    from evaluation.experiments.parser_backend.metrics import load_gold

    gold = load_gold()
    pages = {
        case.case_id: {(c.source_file, c.page_number) for c in case.expected_citations}
        for case in gold
    }
    import json

    mapping = json.loads(EVIDENCE_MAPPING_PATH.read_text(encoding="utf-8"))
    mapped: dict[str, set[str]] = {}
    for entry in mapping["entries"]:
        if entry["mapped"]:
            mapped.setdefault(entry["case_id"], set()).update(entry["mapped_child_ids"])
    return pages, mapped


def retrieval_metrics(
    rows: list[dict[str, Any]],
    *,
    gold_pages: dict[str, set[tuple[str, int]]],
    mapped: dict[str, set[str]],
) -> dict[str, Any]:
    answerable = [r for r in rows if r["question_id"] not in NEGATIVE_IDS]
    hits = {1: 0, 3: 0, 5: 0}
    mrr = 0.0
    gold_doc = gold_page = gold_ev = 0
    for row in answerable:
        ids = row.get("retrieved_chunk_ids") or []
        expected_ids = mapped.get(row["question_id"], set())
        for kk in (1, 3, 5):
            if any(i in expected_ids for i in ids[:kk]):
                hits[kk] += 1
        for rank, cid in enumerate(ids[:5], start=1):
            if cid in expected_ids:
                mrr += 1.0 / rank
                break
        pages = {(doc, page) for doc, page in (row.get("retrieved_pages") or [])}
        expected_pages = gold_pages.get(row["question_id"], set())
        expected_docs = {doc for doc, _ in expected_pages}
        gold_doc += int(any(doc in expected_docs for doc in row.get("retrieved_documents") or set()))
        gold_page += int(bool(pages & expected_pages))
        gold_ev += int(bool(set(ids) & expected_ids))
    n = len(answerable)
    return {
        "recall_at_1": _fmt(hits[1], n),
        "recall_at_3": _fmt(hits[3], n),
        "recall_at_5": _fmt(hits[5], n),
        "mrr": _fmt(round(mrr, 4), n),
        "gold_document_recall": _fmt(gold_doc, n),
        "gold_page_recall": _fmt(gold_page, n),
        "gold_evidence_recall": _fmt(gold_ev, n),
        "answerable_questions": n,
    }


def citation_metrics(
    rows: list[dict[str, Any]],
    *,
    gold_pages: dict[str, set[tuple[str, int]]],
    mapped: dict[str, set[str]],
) -> dict[str, Any]:
    answerable = [r for r in rows if r["question_id"] not in NEGATIVE_IDS]
    negatives = [r for r in rows if r["question_id"] in NEGATIVE_IDS]
    n = len(answerable)
    correct_rows = 0
    precision_sum = 0.0
    recall_sum = 0.0
    traceable_rows = 0
    total_citations = 0
    gold_citations = 0
    false_rejections = 0
    answered_without_evidence = 0
    for row in answerable:
        citations = row.get("citations") or []
        expected = gold_pages.get(row["question_id"], set())
        pairs = {(c.get("document_name"), c.get("page")) for c in citations}
        correct = len(pairs & expected)
        if citations:
            correct_rows += int(correct >= 1)
            precision_sum += correct / len(citations)
            total_citations += len(citations)
            gold_citations += correct
            traceable_rows += int(
                all(c.get("chunk_id") and c.get("document_name") and c.get("page") for c in citations)
            )
        else:
            answered_without_evidence += int(not row["refusal"])
        if row["refusal"]:
            false_rejections += 1
        if expected:
            recall_sum += correct / len(expected)
    n_neg = len(negatives)
    neg_refusals = sum(1 for r in negatives if r["refusal"])
    return {
        "answer_citation_accuracy": _fmt(correct_rows, n),
        "answer_citation_precision": _fmt(round(precision_sum, 4), n),
        "answer_citation_recall": _fmt(round(recall_sum, 4), n),
        "citation_traceability": _fmt(traceable_rows, n),
        "non_gold_citation_reference_rate": _fmt(total_citations - gold_citations, total_citations),
        "gold_citation_reference_rate": _fmt(gold_citations, total_citations),
        "false_rejection_rate": _fmt(false_rejections, n),
        "insufficient_evidence_rejection_rate": _fmt(neg_refusals, n_neg),
        "negative_unsupported_answer_rate": _fmt(n_neg - neg_refusals, n_neg),
        "answered_without_evidence_rate": _fmt(answered_without_evidence, n),
    }


def engineering(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [r.get("total_latency") or 0 for r in rows if r.get("total_latency") is not None]
    ordered = sorted(latencies)
    return {
        "request_count": len(rows),
        "success_count": sum(1 for r in rows if r.get("http_status") == 200),
        "error_count": sum(1 for r in rows if r.get("error_code")),
        "average_latency": round(sum(latencies) / len(latencies), 3) if latencies else 0,
        "p50_latency": float(ordered[len(ordered) // 2]) if ordered else 0,
        "p95_latency": (
            float(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]) if ordered else 0
        ),
        "max_latency": max(latencies) if latencies else 0,
        "input_tokens": sum(r.get("input_tokens") or 0 for r in rows),
        "output_tokens": sum(r.get("output_tokens") or 0 for r in rows),
        "total_tokens": sum(r.get("total_tokens") or 0 for r in rows),
        "fallback_count": 0,
        "cache_hit_count": sum(1 for r in rows if r.get("cache_hit")),
    }

"""Golden-set evidence mapping and deterministic retrieval metrics."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from industrial_rag.evaluation import GoldenCase, load_golden_cases

from .common import normalized_overlap, read_jsonl
from .config import GOLDEN_DOCUMENTS_JSONL, GOLDEN_SET_PATH, QUESTION_CATEGORIES

OVERLAP_THRESHOLD = 0.35


def gold_text_map() -> dict[str, str]:
    """chunk_id -> text from the corpus used to build the golden set."""
    mapping: dict[str, str] = {}
    for row in read_jsonl(GOLDEN_DOCUMENTS_JSONL):
        cid = row.get("chunk_id")
        text = row.get("text")
        if isinstance(cid, str) and isinstance(text, str) and text.strip():
            mapping[cid] = text
    return mapping


def load_gold() -> tuple[GoldenCase, ...]:
    return load_golden_cases(GOLDEN_SET_PATH)


def children_by_file_and_page(child_rows: list[dict[str, Any]]) -> dict[tuple[str, int], list[dict[str, Any]]]:
    out: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in child_rows:
        page = row.get("page_start")
        if isinstance(page, int):
            out[(row.get("document_name", ""), page)].append(row)
    return out


def build_evidence_mapping(
    children: list[dict[str, Any]],
    *,
    gold: tuple[GoldenCase, ...] | None = None,
) -> dict[str, Any]:
    """Map each gold citation to experiment child chunks by page + text overlap.

    The original golden set is never modified; this is a separate
    parser-specific mapping file.
    """
    gold = gold or load_gold()
    texts = gold_text_map()
    by_page = children_by_file_and_page(children)
    entries: list[dict[str, Any]] = []
    mapped_count = 0
    for case in gold:
        for citation in case.expected_citations:
            gold_text = texts.get(citation.chunk_id, "")
            candidates = by_page.get((citation.source_file, citation.page_number), [])
            scored = []
            for child in candidates:
                content = str(child.get("embedding_content") or child.get("content") or "")
                score = normalized_overlap(gold_text, content) if gold_text else 0.0
                if score >= OVERLAP_THRESHOLD:
                    scored.append((score, child.get("chunk_id")))
            scored.sort(key=lambda item: (-item[0], str(item[1])))
            mapped = bool(scored)
            mapped_count += int(mapped)
            entries.append(
                {
                    "case_id": case.case_id,
                    "source_file": citation.source_file,
                    "page_number": citation.page_number,
                    "gold_chunk_id": citation.chunk_id,
                    "gold_text_present": bool(gold_text),
                    "mapped": mapped,
                    "mapped_child_ids": [str(cid) for _, cid in scored[:5]],
                    "mapping_scores": [round(score, 3) for score, _ in scored[:5]],
                    "unmapped_reason": None if mapped else ("gold text missing" if not gold_text else "no page/text overlap"),
                }
            )
    return {
        "total_gold_citations": len(entries),
        "mapped_citations": mapped_count,
        "mapping_rate": round(mapped_count / len(entries), 4) if entries else 0.0,
        "entries": entries,
    }


def _gold_pages(gold: tuple[GoldenCase, ...]) -> dict[str, list[tuple[str, int, str]]]:
    out: dict[str, list[tuple[str, int, str]]] = {}
    for case in gold:
        out[case.case_id] = [
            (c.source_file, c.page_number, c.chunk_id) for c in case.expected_citations
        ]
    return out


def retrieval_metrics(
    results: list[dict[str, Any]],
    *,
    gold: tuple[GoldenCase, ...] | None = None,
    mapping: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute deterministic retrieval metrics from per-question result rows.

    Each result row: {case_id, retrieved: [{file, page, chunk_id, rank}], ...}
    """
    gold = gold or load_gold()
    gold_pages = _gold_pages(gold)
    mapped_ids: dict[str, set[str]] = {}
    if mapping:
        for entry in mapping["entries"]:
            if entry["mapped"]:
                mapped_ids.setdefault(entry["case_id"], set()).update(entry["mapped_child_ids"])

    evidence_cases = [case for case in gold if case.expects_evidence]
    rr_sum = 0.0
    hit_at: dict[int, int] = {1: 0, 3: 0, 5: 0}
    gold_doc_hits = 0
    gold_page_hits = 0
    gold_evidence_hits = 0
    no_result = 0
    wrong_doc = 0
    top1_doc_correct = 0
    top5_page_covered = 0
    evidence_precision_sum = 0.0
    evidence_denom = 0
    total_expected_pages = 0
    total_expected_docs = 0
    total_expected_evidence = 0

    result_by_id = {row["case_id"]: row for row in results}

    for case in evidence_cases:
        row = result_by_id.get(case.case_id)
        retrieved = (row or {}).get("retrieved", [])
        expected = gold_pages[case.case_id]
        expected_docs = {doc for doc, _, _ in expected}
        expected_pages = {(doc, page) for doc, page, _ in expected}
        total_expected_docs += len(expected_docs)
        total_expected_pages += len(expected_pages)
        total_expected_evidence += len(expected)
        if not retrieved:
            no_result += 1
            continue
        top_docs = {item.get("file") for item in retrieved[:5]}
        top_pages = {(item.get("file"), item.get("page")) for item in retrieved[:5]}
        if top_docs & expected_docs:
            gold_doc_hits += 1
            if retrieved[0].get("file") in expected_docs:
                top1_doc_correct += 1
        if top_pages & expected_pages:
            gold_page_hits += 1
        if top_pages & expected_pages:
            top5_page_covered += 1
        if top_docs and not (top_docs & expected_docs):
            wrong_doc += 1

        # Evidence-level: any top-K retrieved chunk id in the mapped set.
        expected_ids = mapped_ids.get(case.case_id, set())
        for k in (1, 3, 5):
            if any(item.get("chunk_id") in expected_ids for item in retrieved[:k]):
                hit_at[k] += 1
        for rank, item in enumerate(retrieved[:5], start=1):
            if item.get("chunk_id") in expected_ids:
                rr_sum += 1.0 / rank
                break
        if any(item.get("chunk_id") in expected_ids for item in retrieved):
            gold_evidence_hits += 1

        # Evidence precision @5 over mapped ids
        top5 = [item.get("chunk_id") for item in retrieved[:5]]
        if top5:
            evidence_precision_sum += sum(1 for cid in top5 if cid in expected_ids) / len(top5)
            evidence_denom += 1

    n = len(evidence_cases)
    return {
        "evidence_case_count": n,
        "recall_at_1": round(hit_at[1] / n, 4) if n else None,
        "recall_at_3": round(hit_at[3] / n, 4) if n else None,
        "recall_at_5": round(hit_at[5] / n, 4) if n else None,
        "mrr": round(rr_sum / n, 4) if n else None,
        "gold_document_recall": round(gold_doc_hits / n, 4) if n else None,
        "gold_page_recall": round(gold_page_hits / n, 4) if n else None,
        "gold_evidence_recall": round(gold_evidence_hits / n, 4) if n else None,
        "evidence_precision_at_5": round(evidence_precision_sum / evidence_denom, 4) if evidence_denom else None,
        "no_result_rate": round(no_result / n, 4) if n else None,
        "wrong_document_recall_rate": round(wrong_doc / n, 4) if n else None,
        "top1_document_accuracy": round(top1_doc_correct / n, 4) if n else None,
        "top5_page_coverage": round(top5_page_covered / n, 4) if n else None,
    }


def citation_metrics(results: list[dict[str, Any]], *, gold: tuple[GoldenCase, ...] | None = None) -> dict[str, Any]:
    """Deterministic citation accuracy/precision/recall/traceability."""
    gold = gold or load_gold()
    gold_pages = _gold_pages(gold)
    by_id = {row["case_id"]: row for row in results}
    total_precision = 0.0
    total_recall = 0.0
    question_with_correct = 0
    traceable = 0
    unsupported = 0
    answered = 0
    denom = 0
    rejection = 0
    no_evidence_cases = [case for case in gold if not case.expects_evidence]
    for case in gold:
        if not case.expects_evidence:
            row = by_id.get(case.case_id, {})
            if row.get("refused") is True and not row.get("citations"):
                rejection += 1
            continue
        row = by_id.get(case.case_id, {})
        citations = row.get("citations", [])
        expected = gold_pages[case.case_id]
        expected_ids = {(doc, page) for doc, page, _ in expected}
        citation_ids = {(c.get("source_file"), c.get("page_number")) for c in citations}
        if citations:
            answered += 1
            denom += 1
            correct = len(citation_ids & expected_ids)
            total_precision += correct / len(citation_ids)
            total_recall += correct / len(expected)
            if correct >= 1:
                question_with_correct += 1
            if all(c.get("chunk_id") for c in citations):
                traceable += 1
            else:
                unsupported += 1
        else:
            total_recall += 0.0
            denom += 1
    return {
        "citation_accuracy": round(question_with_correct / denom, 4) if denom else None,
        "citation_precision": round(total_precision / denom, 4) if denom else None,
        "citation_recall": round(total_recall / denom, 4) if denom else None,
        "citation_traceability": round(traceable / denom, 4) if denom else None,
        "unsupported_citation_rate": round(unsupported / denom, 4) if denom else None,
        "insufficient_evidence_rejection_rate": (
            round(rejection / len(no_evidence_cases), 4) if no_evidence_cases else None
        ),
    }


def category_breakdown(
    results: list[dict[str, Any]],
    metrics: dict[str, Any],
    *,
    gold: tuple[GoldenCase, ...] | None = None,
    mapping: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    gold = gold or load_gold()
    gold_pages = _gold_pages(gold)
    mapped_ids: dict[str, set[str]] = {}
    if mapping:
        for entry in mapping["entries"]:
            if entry["mapped"]:
                mapped_ids.setdefault(entry["case_id"], set()).update(entry["mapped_child_ids"])
    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        by_cat[QUESTION_CATEGORIES.get(row["case_id"], "未分类")].append(row)
    out: dict[str, dict[str, Any]] = {}
    for category, rows in by_cat.items():
        page_hits = 0
        evidence_hits = 0
        no_result = sum(1 for r in rows if not r.get("retrieved"))
        for r in rows:
            retrieved = r.get("retrieved", [])
            expected = gold_pages.get(r["case_id"], [])
            expected_pages = {(doc, page) for doc, page, _ in expected}
            top_pages = {(item.get("file"), item.get("page")) for item in retrieved[:5]}
            if top_pages & expected_pages:
                page_hits += 1
            ids = {item.get("chunk_id") for item in retrieved[:5]}
            if ids & mapped_ids.get(r["case_id"], set()):
                evidence_hits += 1
        count = len(rows)
        out[category] = {
            "question_count": count,
            "gold_page_recall": round(page_hits / count, 4),
            "gold_evidence_recall": round(evidence_hits / count, 4),
            "no_result_count": no_result,
            "case_ids": [r["case_id"] for r in rows],
        }
    return out

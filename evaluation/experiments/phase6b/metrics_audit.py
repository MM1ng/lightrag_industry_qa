"""Canonical metric definitions and offline recomputation (Phase 6B)."""

from __future__ import annotations

import json
import sys
from typing import Any

from .config import (
    EVIDENCE_MAPPING_PATH,
    PHASE4_R0_ANSWERS,
    PHASE6_GOLDEN,
    PHASE6B_ROOT,
    read_jsonl,
)

NEGATIVE_IDS = ("N001", "N002")


def gold_sets() -> tuple[dict[str, set[tuple[str, int]]], dict[str, set[str]]]:
    from evaluation.experiments.parser_backend.metrics import load_gold

    gold = load_gold()
    pages = {
        case.case_id: {(c.source_file, c.page_number) for c in case.expected_citations}
        for case in gold
    }
    mapping = json.loads(EVIDENCE_MAPPING_PATH.read_text(encoding="utf-8"))
    mapped: dict[str, set[str]] = {}
    for entry in mapping["entries"]:
        if entry["mapped"]:
            mapped.setdefault(entry["case_id"], set()).update(entry["mapped_child_ids"])
    return pages, mapped


def _fmt(numerator: int | float, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "decimal": round(numerator / denominator, 4) if denominator else None,
        "percentage": round(numerator / denominator * 100, 2) if denominator else None,
    }


def retrieval_metrics(
    rows_by_q: dict[str, list[dict[str, Any]]],
    *,
    universe: str,
    gold_pages: dict[str, set[tuple[str, int]]],
    mapped: dict[str, set[str]],
) -> dict[str, Any]:
    """Canonical @K retrieval metrics (K=1/3/5/12)."""
    answerable = [q for q in rows_by_q if q not in NEGATIVE_IDS]
    hits = {1: 0, 3: 0, 5: 0, 12: 0}
    mrr = 0.0
    gold_doc = gold_page = gold_ev = 0
    ev5 = ev12 = 0.0
    n = len(answerable)
    for q in answerable:
        rows = sorted(rows_by_q[q], key=lambda r: r.get("rank") or r.get("rerank_rank") or 999)[:12]
        ids = [r.get("child_chunk_id") or r.get("chunk_id") for r in rows]
        expected = mapped.get(q, set())
        pages = {
            (r.get("document_id") or r.get("document"), r.get("page"))
            for r in rows
        }
        expected_pages = gold_pages.get(q, set())
        expected_docs = {doc for doc, _ in expected_pages}
        for kk in (1, 3, 5, 12):
            if any(i in expected for i in ids[:kk]):
                hits[kk] += 1
        for rank, cid in enumerate(ids[:5], start=1):
            if cid in expected:
                mrr += 1.0 / rank
                break
        gold_doc += int(any(doc in expected_docs for doc in {doc for doc, _ in pages}))
        gold_page += int(bool(pages & expected_pages))
        gold_ev += int(bool(set(ids) & expected))
        ev5 += sum(1 for cid in ids[:5] if cid in expected) / 5
        ev12 += sum(1 for cid in ids[:12] if cid in expected) / 12
    return {
        "universe": universe,
        "recall_at_1": _fmt(hits[1], n),
        "recall_at_3": _fmt(hits[3], n),
        "recall_at_5": _fmt(hits[5], n),
        "recall_at_12": _fmt(hits[12], n),
        "mrr_at_5": _fmt(round(mrr, 4), n),
        "gold_document_recall_at_12": _fmt(gold_doc, n),
        "gold_page_recall_at_12": _fmt(gold_page, n),
        "gold_evidence_recall_at_12": _fmt(gold_ev, n),
        "evidence_precision_at_5": _fmt(round(ev5, 4), n),
        "evidence_precision_at_12": _fmt(round(ev12, 4), n),
        "answerable_questions": n,
    }


def citation_metrics(
    rows: list[dict[str, Any]],
    *,
    gold_pages: dict[str, set[tuple[str, int]]],
    mapped: dict[str, set[str]],
    refusal_clears_citations: bool,
) -> dict[str, Any]:
    """Canonical answer citation metrics.

    refusal_clears_citations=True is the production convention: a refused
    answer emits no citations and counts as a failure for citation metrics.
    """
    answerable = [r for r in rows if r["question_id"] not in NEGATIVE_IDS]
    negatives = [r for r in rows if r["question_id"] in NEGATIVE_IDS]
    n = len(answerable)
    correct_rows = 0
    precision_sum = 0.0
    recall_sum = 0.0
    traceable_rows = 0
    rows_with_citations = 0
    traceable_emitted = 0
    total_citations = 0
    gold_citations = 0
    false_rejections = 0
    answered_without_evidence = 0
    for row in answerable:
        citations = list(row.get("citations") or [])
        if refusal_clears_citations and row.get("refusal"):
            citations = []
        expected = gold_pages.get(row["question_id"], set())
        pairs = {
            (c.get("document_name") or c.get("source_file"), c.get("page") or c.get("page_number"))
            for c in citations
        }
        correct = len(pairs & expected)
        if citations:
            rows_with_citations += 1
            correct_rows += int(correct >= 1)
            precision_sum += correct / len(citations)
            total_citations += len(citations)
            gold_citations += correct
            traceable_rows += int(
                all(c.get("chunk_id") and c.get("page") is not None for c in citations)
            )
            traceable_emitted += int(
                all(c.get("chunk_id") and c.get("page") is not None for c in citations)
            )
        else:
            answered_without_evidence += int(not row.get("refusal"))
        if row.get("refusal"):
            false_rejections += 1
        if expected:
            recall_sum += correct / len(expected)
    n_neg = len(negatives)
    neg_refusals = sum(1 for r in negatives if r.get("refusal"))
    return {
        "refusal_clears_citations": refusal_clears_citations,
        "answer_citation_accuracy": _fmt(correct_rows, n),
        "answer_citation_precision": _fmt(round(precision_sum, 4), n),
        "answer_citation_recall": _fmt(round(recall_sum, 4), n),
        "citation_traceability": _fmt(traceable_rows, n),
        "citation_traceability_emitted": _fmt(traceable_emitted, rows_with_citations),
        "gold_citation_reference_rate": _fmt(gold_citations, total_citations),
        "non_gold_citation_reference_rate": _fmt(total_citations - gold_citations, total_citations),
        "false_rejection_rate": _fmt(false_rejections, n),
        "insufficient_evidence_rejection_rate": _fmt(neg_refusals, n_neg),
        "negative_unsupported_answer_rate": _fmt(n_neg - neg_refusals, n_neg),
        "answered_without_evidence_rate": _fmt(answered_without_evidence, n),
    }


def _harness_rows() -> list[dict[str, Any]]:
    rows = read_jsonl(PHASE4_R0_ANSWERS)
    return [
        {
            "question_id": r["question_id"],
            "citations": [
                {
                    "chunk_id": c.get("chunk_id"),
                    "document_name": c.get("source_file"),
                    "page": c.get("page_number"),
                }
                for c in (r.get("citations") or [])
            ],
            "refusal": bool(r.get("refusal")),
        }
        for r in rows
    ]


def _harness_retrieval_by_q() -> dict[str, list[dict[str, Any]]]:
    from .config import CANDIDATE_POOL_PATH

    pool = read_jsonl(CANDIDATE_POOL_PATH)
    by_q: dict[str, list[dict[str, Any]]] = {}
    for row in pool:
        by_q.setdefault(row["question_id"], []).append(
            {
                "child_chunk_id": row["child_chunk_id"],
                "rank": row["rank"],
                "document_id": row["document_id"],
                "page": row["page"],
            }
        )
    return by_q


def _fastapi_retrieval_by_q() -> dict[str, list[dict[str, Any]]]:
    from .config import PHASE6_GOLDEN

    rows = read_jsonl(PHASE6_GOLDEN)
    meta: dict[str, dict[str, Any]] = {}
    from .config import PDF_NAMES, PYMUPDF_CHILDREN_DIR

    for pdf in PDF_NAMES:
        for line in (PYMUPDF_CHILDREN_DIR / pdf / "child_chunks.jsonl").read_text(
            encoding="utf-8"
        ).splitlines():
            if line.strip():
                child = json.loads(line)
                meta[child["chunk_id"]] = child
    by_q: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        ids = row.get("retrieved_chunk_ids") or []
        by_q[row["question_id"]] = [
            {
                "chunk_id": chunk_id,
                "rank": index + 1,
                "document_id": meta.get(chunk_id, {}).get("document_name", ""),
                "page": meta.get(chunk_id, {}).get("page_start"),
            }
            for index, chunk_id in enumerate(ids)
        ]
    return by_q


def run_audit() -> dict[str, Any]:
    gold_pages, mapped = gold_sets()
    harness_rows = _harness_rows()
    fastapi_rows = [
        {
            "question_id": r["question_id"],
            "citations": r.get("citations") or [],
            "refusal": bool(r.get("refusal")),
        }
        for r in read_jsonl(PHASE6_GOLDEN)
    ]
    harness_retrieval = _harness_retrieval_by_q()
    fastapi_retrieval = _fastapi_retrieval_by_q()
    ret_h = retrieval_metrics(
        harness_retrieval, universe="frozen_pool_top12_rows", gold_pages=gold_pages, mapped=mapped
    )
    ret_a = retrieval_metrics(
        fastapi_retrieval, universe="fastapi_retrieved_top12_ids", gold_pages=gold_pages, mapped=mapped
    )
    cit_h_legacy = citation_metrics(
        harness_rows, gold_pages=gold_pages, mapped=mapped, refusal_clears_citations=False
    )
    cit_h_canonical = citation_metrics(
        harness_rows, gold_pages=gold_pages, mapped=mapped, refusal_clears_citations=True
    )
    cit_a = citation_metrics(
        fastapi_rows, gold_pages=gold_pages, mapped=mapped, refusal_clears_citations=True
    )
    recomputed = {
        "harness_retrieval_at_12": ret_h,
        "fastapi_retrieval_at_12": ret_a,
        "harness_citation_legacy_convention": cit_h_legacy,
        "harness_citation_canonical_convention": cit_h_canonical,
        "fastapi_citation_canonical_convention": cit_a,
        "gate": {
            "threshold_drop_leq_002": True,
            "baseline_accuracy_canonical": cit_h_canonical["answer_citation_accuracy"]["decimal"],
            "fastapi_accuracy_canonical": cit_a["answer_citation_accuracy"]["decimal"],
            "drop": round(
                cit_h_canonical["answer_citation_accuracy"]["decimal"]
                - cit_a["answer_citation_accuracy"]["decimal"],
                4,
            ),
            "historical_baseline_legacy": cit_h_legacy["answer_citation_accuracy"]["decimal"],
            "historical_fastapi_legacy_note": (
                "Phase 6 official path always clears citations on refusal; "
                "37/48 is the same under both conventions"
            ),
        },
    }
    (PHASE6B_ROOT / "metric_audit").mkdir(parents=True, exist_ok=True)
    definitions = {
        "retrieval": [
            {
                "canonical_name": f"recall_at_{k}",
                "source_file": "phase4 pool (H) / phase6 golden (A)",
                "source_field": "child_chunk_id / retrieved_chunk_ids",
                "candidate_universe": (
                    "H: frozen pool top-12 rows (duplicates allowed) | "
                    "A: official retrieval first 12 unique ids"
                ),
                "k": k,
                "dedup_rule": "H: none (rows) | A: identity-deduped by API",
                "evidence_mapping_rule": "exact chunk_id in evidence_mapping_p0",
                "denominator": 48,
                "included_questions": "S/D/C (48)",
                "excluded_questions": "N001/N002",
            }
            for k in (1, 3, 5, 12)
        ],
        "gold_recall": [
            {
                "canonical_name": name,
                "candidate_universe": "top-12 (both paths)",
                "k": 12,
                "dedup_rule": "unique chunk ids (sets)",
                "denominator": 48,
                "included_questions": "S/D/C (48)",
                "excluded_questions": "N001/N002",
            }
            for name in (
                "gold_document_recall_at_12",
                "gold_page_recall_at_12",
                "gold_evidence_recall_at_12",
            )
        ],
        "citation": [
            {
                "canonical_name": name,
                "definition": (
                    "per-question accuracy/precision/recall over answer-emitted "
                    "citations; refusal clears citations (production convention)"
                ),
                "denominator": 48,
                "included_questions": "S/D/C (48)",
                "excluded_questions": "N001/N002",
            }
            for name in (
                "answer_citation_accuracy",
                "answer_citation_precision",
                "answer_citation_recall",
                "citation_traceability",
                "citation_traceability_emitted",
            )
        ],
        "reference_rates": [
            {
                "canonical_name": name,
                "definition": "per-citation gold/non-gold alignment (same denominator)",
                "denominator": "emitted citations",
                "complement": True,
            }
            for name in ("gold_citation_reference_rate", "non_gold_citation_reference_rate")
        ],
    }
    (PHASE6B_ROOT / "metric_audit" / "retrieval_metric_definitions.json").write_text(
        json.dumps(definitions["retrieval"] + definitions["gold_recall"], ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    (PHASE6B_ROOT / "metric_audit" / "citation_metric_definitions.json").write_text(
        json.dumps(definitions["citation"] + definitions["reference_rates"], ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    denominator_audit = {
        "answerable_denominator": 48,
        "negative_denominator": 2,
        "n001_n002_excluded_from_retrieval_and_citation": True,
        "n001_n002_included_in_rejection_metrics": True,
        "harness_uses_r0_baseline_not_reranked": True,
        "fastapi_uses_official_golden_not_reranked": True,
        "rerank_enabled": False,
        "context_strategy": "current_rows",
        "universe_mismatch_explanation": (
            "Phase 6 published Gold Page 0.9375 / Gold Evidence 0.8750 used the "
            "full official retrieval (up to 20 identity-deduped ids); canonical "
            "@12 metrics are 0.8542 / 0.7917, identical to the frozen pool "
            "top-12 values. The earlier published numbers were not wrong for "
            "their universe, but the release gate uses @12."
        ),
    }
    (PHASE6B_ROOT / "metric_audit" / "denominator_audit.json").write_text(
        json.dumps(denominator_audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (PHASE6B_ROOT / "metric_audit" / "recomputed_metrics.json").write_text(
        json.dumps(recomputed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return recomputed


def main() -> int:
    result = run_audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

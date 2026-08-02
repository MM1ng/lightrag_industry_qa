"""Phase 7 acceptance metrics for the 20-question golden subset.

Canonical denominators (Phase 6B): answerable metrics use the 18 answerable
questions only; negative metrics use N001/N002 only. Refusal clears citations
(production convention).
"""

from __future__ import annotations

import json
from typing import Any

NEGATIVE_IDS = ("N001", "N002")


def golden_subset_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from evaluation.experiments.parser_backend.metrics import load_gold

    gold_pages = {
        case.case_id: {(c.source_file, c.page_number) for c in case.expected_citations}
        for case in load_gold()
    }
    from .config import PROJECT_ROOT

    mapping = json.loads(
        (
            PROJECT_ROOT
            / "evaluation"
            / "experiments"
            / "parser_backend"
            / "fixed_model"
            / "comparison"
            / "evidence_mapping_p0.json"
        ).read_text(encoding="utf-8")
    )
    mapped: dict[str, set[str]] = {}
    for entry in mapping["entries"]:
        if entry["mapped"]:
            mapped.setdefault(entry["case_id"], set()).update(entry["mapped_child_ids"])

    answerable = [r for r in rows if r["question_id"] not in NEGATIVE_IDS]
    negatives = [r for r in rows if r["question_id"] in NEGATIVE_IDS]
    n = len(answerable)
    n_neg = len(negatives)
    ok = sum(1 for r in rows if r["http_status"] == 200)
    latencies = sorted(r["total_latency"] for r in rows)
    correct_rows = 0
    precision_sum = 0.0
    recall_sum = 0.0
    gold_page_rows = 0
    gold_evidence_rows = 0
    total_citations = 0
    gold_citations = 0
    answered_without_evidence = 0
    false_rejections = 0
    traceable_emitted = 0
    rows_with_citations = 0
    for row in answerable:
        citations = list(row.get("citations") or [])
        if row.get("refusal"):
            citations = []
        expected = gold_pages.get(row["question_id"], set())
        pairs = {
            (c.get("document_name") or c.get("source_file"), c.get("page") or c.get("page_number"))
            for c in citations
        }
        chunk_ids = {c.get("chunk_id") for c in citations}
        correct = len(pairs & expected)
        if citations:
            rows_with_citations += 1
            correct_rows += int(correct >= 1)
            precision_sum += correct / len(citations)
            total_citations += len(citations)
            gold_citations += correct
            gold_page_rows += int(bool(pairs & expected))
            gold_evidence_rows += int(bool(chunk_ids & mapped.get(row["question_id"], set())))
            traceable_emitted += int(
                all(
                    c.get("chunk_id") and c.get("document_name") and c.get("page")
                    for c in citations
                )
            )
        else:
            answered_without_evidence += int(not row.get("refusal"))
        if row.get("refusal"):
            false_rejections += 1
        if expected:
            recall_sum += correct / len(expected)
    neg_refused = sum(1 for r in negatives if r["refusal"])
    return {
        "questions": len(rows),
        "answerable_questions": n,
        "negative_questions": n_neg,
        "http_success_rate": ok / len(rows) if rows else 0.0,
        "answer_citation_accuracy": correct_rows / n if n else 0.0,
        "answer_citation_precision": precision_sum / n if n else 0.0,
        "answer_citation_recall": recall_sum / n if n else 0.0,
        "gold_page_citation_rate": gold_page_rows / n if n else 0.0,
        "gold_evidence_citation_rate": gold_evidence_rows / n if n else 0.0,
        "citation_traceability_emitted": (
            traceable_emitted / rows_with_citations if rows_with_citations else 0.0
        ),
        "gold_citation_reference_rate": gold_citations / total_citations if total_citations else 0.0,
        "non_gold_citation_reference_rate": (
            (total_citations - gold_citations) / total_citations if total_citations else 0.0
        ),
        "false_rejection_rate": false_rejections / n if n else 0.0,
        "answered_without_evidence_rate": answered_without_evidence / n if n else 0.0,
        "insufficient_evidence_rejection_rate": (
            neg_refused / n_neg if n_neg else 0.0
        ),
        "negative_unsupported_answer_rate": (
            (n_neg - neg_refused) / n_neg if n_neg else 0.0
        ),
        "request_trace_id_complete_rate": (
            sum(1 for r in rows if r.get("request_id") and r.get("trace_id"))
            / len(rows)
            if rows
            else 0.0
        ),
        "p95_latency": (
            float(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))])
            if latencies
            else 0.0
        ),
        "error_rate": sum(1 for r in rows if r.get("error")) / len(rows) if rows else 0.0,
        "refusals": [r["question_id"] for r in rows if r["refusal"]],
    }

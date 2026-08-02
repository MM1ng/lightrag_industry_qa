"""Phase 7 acceptance metrics for the 20-question golden subset."""

from __future__ import annotations

from typing import Any


def golden_subset_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from evaluation.experiments.parser_backend.metrics import load_gold

    gold_pages = {
        case.case_id: {(c.source_file, c.page_number) for c in case.expected_citations}
        for case in load_gold()
    }
    n = len(rows)
    ok = sum(1 for r in rows if r["http_status"] == 200)
    latencies = sorted(r["total_latency"] for r in rows)
    emitted = [r for r in rows if r["citations"]]
    correct = sum(
        1
        for r in rows
        if not r["refusal"]
        and {
            (c.get("document_name"), c.get("page")) for c in r["citations"]
        }
        & gold_pages.get(r["question_id"], set())
    )
    traceable_emitted = (
        sum(1 for r in emitted if r["citations"]) / len(emitted) if emitted else 0.0
    )
    refusals = [r for r in rows if r["refusal"]]
    negatives = [r for r in rows if r["question_id"] in ("N001", "N002")]
    neg_refused = sum(1 for r in negatives if r["refusal"])
    return {
        "questions": n,
        "http_success_rate": ok / n if n else 0.0,
        "answer_citation_accuracy": correct / n if n else 0.0,
        "citation_traceability_emitted": round(traceable_emitted, 4),
        "false_rejection_rate": sum(1 for r in rows if r["refusal"]) / n if n else 0.0,
        "insufficient_evidence_rejection_rate": (
            neg_refused / len(negatives) if negatives else 0.0
        ),
        "negative_unsupported_answer_rate": (
            (len(negatives) - neg_refused) / len(negatives) if negatives else 0.0
        ),
        "request_trace_id_complete_rate": (
            sum(1 for r in rows if r.get("request_id") and r.get("trace_id")) / n if n else 0.0
        ),
        "p95_latency": (
            float(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))])
            if latencies
            else 0.0
        ),
        "error_rate": sum(1 for r in rows if r.get("error")) / n if n else 0.0,
        "refusals": [r["question_id"] for r in refusals],
    }

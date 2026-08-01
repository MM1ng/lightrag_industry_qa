"""Offline R0 baseline (frozen order) and R1 gate for Phase 4D."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .config import (
    CANDIDATE_POOL_PATH,
    EXPERIMENT_ROOT,
    PROJECT_ROOT,
)


def _mapped_ids() -> dict[str, set[str]]:
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
    out: dict[str, set[str]] = {}
    for entry in mapping["entries"]:
        if entry["mapped"]:
            out.setdefault(entry["case_id"], set()).update(entry["mapped_child_ids"])
    return out


def _gold() -> tuple[dict[str, set[tuple[str, int]]], dict[str, bool]]:
    from evaluation.experiments.parser_backend.metrics import load_gold

    gold = load_gold()
    pages = {
        case.case_id: {(c.source_file, c.page_number) for c in case.expected_citations}
        for case in gold
    }
    expects = {case.case_id: case.expects_evidence for case in gold}
    return pages, expects


def metrics_for_topk(
    rows_by_q: dict[str, list[dict[str, Any]]],
    k: int,
    *,
    mapped: dict[str, set[str]],
    gold_pages: dict[str, set[tuple[str, int]]],
) -> dict[str, Any]:
    evidence = [q for q, rows in rows_by_q.items() if q not in ("N001", "N002") and gold_pages.get(q)]
    hits = {1: 0, 3: 0, 5: 0, k: 0}
    mrr = 0.0
    gold_doc = gold_page = gold_ev = 0
    ev_prec5 = ev_prec12 = 0.0
    top1_doc = 0
    top5_page = 0
    for q in evidence:
        rows = sorted(rows_by_q[q], key=lambda r: r.get("rank") or 999)[:k]
        ids = [r["child_chunk_id"] for r in rows]
        expected_ids = mapped.get(q, set())
        pages = {(r.get("document_id"), r.get("page")) for r in rows}
        expected_pages = gold_pages.get(q, set())
        expected_docs = {doc for doc, _ in expected_pages}
        for kk in (1, 3, 5, k):
            if any(i in expected_ids for i in ids[:kk]):
                hits[kk] += 1
        for rank, cid in enumerate(ids[:5], start=1):
            if cid in expected_ids:
                mrr += 1.0 / rank
                break
        gold_doc += int(any(doc in expected_docs for doc in {r.get("document_id") for r in rows}))
        gold_page += int(bool(pages & expected_pages))
        gold_ev += int(bool(set(ids) & expected_ids))
        ev_prec5 += sum(1 for cid in ids[:5] if cid in expected_ids) / 5
        ev_prec12 += sum(1 for cid in ids[:12] if cid in expected_ids) / 12
        top1_doc += int(rows and rows[0].get("document_id") in expected_docs)
        top5_page += int(bool({(r.get("document_id"), r.get("page")) for r in rows[:5]} & expected_pages))
    n = len(evidence)
    return {
        "evidence_questions": n,
        "recall_at_1": round(hits[1] / n, 4),
        "recall_at_3": round(hits[3] / n, 4),
        "recall_at_5": round(hits[5] / n, 4),
        f"recall_at_{k}": round(hits[k] / n, 4),
        "mrr": round(mrr / n, 4),
        "gold_document_recall": round(gold_doc / n, 4),
        "gold_page_recall": round(gold_page / n, 4),
        "gold_evidence_recall": round(gold_ev / n, 4),
        "evidence_precision_at_5": round(ev_prec5 / n, 4),
        "evidence_precision_at_12": round(ev_prec12 / n, 4),
        "top1_document_accuracy": round(top1_doc / n, 4),
        "top5_page_coverage": round(top5_page / n, 4),
    }


def main() -> int:
    rows = [
        json.loads(line)
        for line in CANDIDATE_POOL_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_q: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_q.setdefault(r["question_id"], []).append(r)
    mapped = _mapped_ids()
    gold_pages, _ = _gold()
    baseline = metrics_for_topk(by_q, 12, mapped=mapped, gold_pages=gold_pages)
    out_dir = EXPERIMENT_ROOT / "results" / "offline"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "baseline.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "question_id": q,
                    "original_rank": r.get("rank"),
                    "chunk_id": r.get("child_chunk_id"),
                    "document": r.get("document_id"),
                    "page": r.get("page"),
                    "gold_match": int(r.get("child_chunk_id") in mapped.get(q, set())),
                }
            )
            + "\n"
            for q in sorted(by_q)
            for r in sorted(by_q[q], key=lambda x: x.get("rank") or 999)
        ),
        encoding="utf-8",
    )
    (out_dir / "baseline_metrics.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(baseline, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

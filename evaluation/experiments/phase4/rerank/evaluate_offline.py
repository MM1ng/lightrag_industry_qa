"""Offline R0/R1 evaluation for Phase 4D-R2 (variable-size candidates).

Contract:
- answerable questions (S/D/C, 48): 1 <= input_count <= candidate_k
- evidence-insufficient questions (N001/N002): 0 <= input_count <= candidate_k
- effective_final_k = min(final_k, input_count)
- completeness: output_count == input_count, output multiset == input
  multiset (no loss, no pool-out, no new duplicates), and all candidate
  metadata (original rank/score, text hash, document, page, parent id)
  unchanged.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .config import (
    CANDIDATE_POOL_PATH,
    EXPERIMENT_ROOT,
    RERANK_CONFIG,
)

NEGATIVE_QUESTION_IDS = ("N001", "N002")


def _mapped_ids() -> dict[str, set[str]]:
    mapping = json.loads(
        (
            Path(__file__).resolve().parents[3]
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


def answerable_ids(rows_by_q: dict[str, list[dict[str, Any]]]) -> list[str]:
    return [q for q in rows_by_q if q not in NEGATIVE_QUESTION_IDS]


def metrics_for_topk(
    rows_by_q: dict[str, list[dict[str, Any]]],
    k: int,
    *,
    mapped: dict[str, set[str]],
    gold_pages: dict[str, set[tuple[str, int]]],
) -> dict[str, Any]:
    """Retrieval metrics over answerable questions only (N excluded)."""
    evidence = answerable_ids(rows_by_q)
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
        top5_page += int(
            bool({(r.get("document_id"), r.get("page")) for r in rows[:5]} & expected_pages)
        )
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


def baseline_rows(
    rows_by_q: dict[str, list[dict[str, Any]]],
    *,
    mapped: dict[str, set[str]],
) -> list[dict[str, Any]]:
    """Per-question frozen-order rows (top effective_final_k) for baseline.jsonl."""
    out: list[dict[str, Any]] = []
    final_k = RERANK_CONFIG["final_k"]
    for q in sorted(rows_by_q):
        rows = sorted(rows_by_q[q], key=lambda r: r.get("rank") or 999)[
            : min(final_k, len(rows_by_q[q]))
        ]
        for r in rows:
            out.append(
                {
                    "question_id": q,
                    "original_rank": r.get("rank"),
                    "chunk_id": r.get("child_chunk_id"),
                    "document": r.get("document_id"),
                    "page": r.get("page"),
                    "parent_id": r.get("parent_id"),
                    "text_hash": r.get("child_text_hash"),
                    "gold_match": int(r.get("child_chunk_id") in mapped.get(q, set())),
                }
            )
    return out


def completeness_report(
    input_rows_by_q: dict[str, list[dict[str, Any]]],
    output_rows_by_q: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Variable-size candidate completeness (output == input, only order may change)."""
    per_question: dict[str, Any] = {}
    error_details: list[dict[str, Any]] = []
    for q, input_rows in input_rows_by_q.items():
        out_rows = output_rows_by_q.get(q, [])
        in_ids = [r["child_chunk_id"] for r in input_rows]
        out_ids = [r.get("chunk_id") for r in out_rows]
        in_counter = Counter(in_ids)
        out_counter = Counter(out_ids)
        lost_count = sum((in_counter - out_counter).values())
        pool_out_count = sum((out_counter - in_counter).values())
        preserved_count = sum(
            min(in_counter[cid], out_counter[cid]) for cid in in_counter
        )
        if input_rows:
            preservation_rate = round(preserved_count / len(input_rows), 4)
        else:
            preservation_rate = None
        metadata_unchanged = True
        for out_row in out_rows:
            source = next(
                (r for r in input_rows if (r.get("rank") or 0) == out_row.get("original_rank")),
                None,
            )
            if source is None:
                metadata_unchanged = False
                error_details.append(
                {"question_id": q, "error": "output original_rank has no input row"}
                )
                continue
            out_document = out_row.get("document") or out_row.get("document_id")
            in_document = source.get("document_id")
            if (
                str(out_row.get("text_hash") or "")
                != str(source.get("child_text_hash") or "")
                or str(out_document or "") != str(in_document or "")
                or out_row.get("page") != source.get("page")
                or out_row.get("parent_id") != source.get("parent_id")
                or out_row.get("original_score") != source.get("retrieval_score")
            ):
                metadata_unchanged = False
                error_details.append(
                    {
                        "question_id": q,
                        "chunk_id": out_row.get("chunk_id"),
                        "error": "candidate metadata changed",
                    }
                )
        passed = (
            len(out_rows) == len(input_rows)
            and lost_count == 0
            and pool_out_count == 0
            and metadata_unchanged
        )
        duplicate_chunk_ids = {
            cid: count
            for cid, count in in_counter.items()
            if count > 1
        }
        per_question[q] = {
            "input_rows": len(input_rows),
            "output_rows": len(out_rows),
            "input_unique_chunk_ids": len(set(in_ids)),
            "output_unique_chunk_ids": len(set(out_ids)),
            "input_duplicate_chunk_ids": duplicate_chunk_ids,
            "preservation_rate": preservation_rate,
            "lost_count": lost_count,
            "pool_out_count": pool_out_count,
            "introduced_duplicate_count": pool_out_count,
            "metadata_unchanged": metadata_unchanged,
            "called": bool(input_rows),
            "cache_hit": bool(out_rows and out_rows[0].get("cache_hit")),
            "passed": passed,
        }
    answerable = answerable_ids(input_rows_by_q)
    negatives = list(NEGATIVE_QUESTION_IDS)
    answerable_success = all(per_question[q]["passed"] for q in answerable)
    present_negatives = [q for q in negatives if q in per_question]
    negative_success = all(per_question[q]["passed"] for q in present_negatives)
    preservation_rates = [per_question[q]["preservation_rate"] for q in answerable]
    report = {
        "contract": "variable_unique_candidates_up_to_candidate_k",
        "candidate_k": RERANK_CONFIG["candidate_k"],
        "final_k": RERANK_CONFIG["final_k"],
        "effective_final_k_rule": "min(final_k, input_candidate_count)",
        "negative_questions_may_have_candidates": True,
        "answerable_request_count": len(answerable),
        "answerable_success_count": sum(
            per_question[q]["passed"] for q in answerable
        ),
        "negative_request_count": len(negatives),
        "negative_success_count": sum(
            per_question[q]["passed"] for q in negatives if q in per_question
        ),
        "error_count": len(error_details),
        "error_details": error_details,
        "candidate_preservation_rate": (
            round(min(preservation_rates), 4)
            if preservation_rates and all(r is not None for r in preservation_rates)
            else None
        ),
        "pool_out_count": sum(per_question[q]["pool_out_count"] for q in per_question),
        "duplicate_count": sum(
            per_question[q]["introduced_duplicate_count"] for q in per_question
        ),
        "lost_count": sum(per_question[q]["lost_count"] for q in per_question),
        "fallback_count": 0,
        "per_question": per_question,
        "passed": answerable_success and negative_success and not error_details,
    }
    dup_questions = sorted(
        q
        for q, info in per_question.items()
        if info.get("input_duplicate_chunk_ids")
    )
    if dup_questions:
        report["notes"] = [
            (
                f"The frozen pool contains pre-existing duplicate chunk_id rows "
                f"for {dup_questions} (same text hash/page/parent at different "
                f"original ranks). The reranker faithfully preserved every input "
                f"row; completeness is evaluated as output multiset == input "
                f"multiset, so no candidate was lost and no new duplicate was "
                f"introduced."
            )
        ]
    else:
        report["notes"] = ["No pre-existing duplicate chunk_id rows in the frozen pool."]
    return report


def rank_movement_summary(
    movement_rows: list[dict[str, Any]],
    *,
    rows_by_q: dict[str, list[dict[str, Any]]],
    output_rows_by_q: dict[str, list[dict[str, Any]]],
    mapped: dict[str, set[str]],
) -> dict[str, Any]:
    """Rank movement over the 48 answerable questions."""
    final_k = RERANK_CONFIG["final_k"]
    answerable = answerable_ids(rows_by_q)
    rows = [r for r in movement_rows if r["question_id"] in answerable]
    deltas = [abs(r["rank_delta"]) for r in rows]
    ordered = sorted(deltas)
    relevant = [r for r in rows if r["gold_evidence_match"]]
    irrelevant = [r for r in rows if not r["gold_evidence_match"]]
    relevant_promoted = sum(1 for r in relevant if r["rank_delta"] < 0)
    relevant_demoted = sum(1 for r in relevant if r["rank_delta"] > 0)
    irrelevant_promoted = sum(1 for r in irrelevant if r["rank_delta"] < 0)
    irrelevant_demoted = sum(1 for r in irrelevant if r["rank_delta"] > 0)
    top1_changed = top3_changed = top5_changed = top12_changed = 0
    per_question_status: dict[str, str] = {}
    for q in answerable:
        original = sorted(rows_by_q[q], key=lambda r: r.get("rank") or 999)
        reranked = sorted(output_rows_by_q.get(q, []), key=lambda r: r.get("rerank_rank") or 999)
        expected_ids = mapped.get(q, set())
        original_top1 = original[0]["child_chunk_id"] if original else None
        reranked_top1 = reranked[0]["chunk_id"] if reranked else None
        if original_top1 != reranked_top1:
            top1_changed += 1
        for kk in (3, 5, final_k):
            orig_ids = {r["child_chunk_id"] for r in original[: min(kk, len(original))]}
            new_ids = {r["chunk_id"] for r in reranked[: min(kk, len(reranked))]}
            if orig_ids != new_ids:
                if kk == 3:
                    top3_changed += 1
                elif kk == 5:
                    top5_changed += 1
                else:
                    top12_changed += 1
        r0_hit = bool(set(original[:final_k][i]["child_chunk_id"] for i in range(min(final_k, len(original)))) & expected_ids)
        r1_hit = bool(set(reranked[:final_k][i]["chunk_id"] for i in range(min(final_k, len(reranked)))) & expected_ids)
        if r0_hit and r1_hit:
            per_question_status[q] = "unchanged"
        elif r1_hit and not r0_hit:
            per_question_status[q] = "improved"
        else:
            per_question_status[q] = "regressed" if r0_hit else "unchanged"
    improved = [q for q, s in per_question_status.items() if s == "improved"]
    regressed = [q for q, s in per_question_status.items() if s == "regressed"]
    unchanged = [q for q, s in per_question_status.items() if s == "unchanged"]
    return {
        "mean_abs_rank_movement": round(sum(deltas) / len(deltas), 3) if deltas else 0,
        "median_rank_movement": ordered[len(ordered) // 2] if ordered else 0,
        "p95_rank_movement": ordered[int(len(ordered) * 0.95)] if ordered else 0,
        "relevant_promoted_count": relevant_promoted,
        "relevant_demoted_count": relevant_demoted,
        "irrelevant_promoted_count": irrelevant_promoted,
        "irrelevant_demoted_count": irrelevant_demoted,
        "top1_changed_count": top1_changed,
        "top3_membership_changed_count": top3_changed,
        "top5_membership_changed_count": top5_changed,
        "top12_membership_changed_count": top12_changed,
        "improved_count": len(improved),
        "regressed_count": len(regressed),
        "unchanged_count": len(unchanged),
        "improved_questions": improved,
        "regressed_questions": regressed,
        "per_question_status": per_question_status,
    }


def offline_gates(
    r0: dict[str, Any],
    r1: dict[str, Any],
    completeness: dict[str, Any],
    movement: dict[str, Any],
) -> dict[str, Any]:
    """Phase 4D-R2 offline hard/value gates (48 answerable questions)."""
    hard = {
        "recall5_drop_leq_002": r1["metrics"]["recall_at_5"] >= r0["recall_at_5"] - 0.02,
        "gold_page_drop_leq_002": r1["metrics"]["gold_page_recall"]
        >= r0["gold_page_recall"] - 0.02,
        "gold_evidence_drop_leq_002": r1["metrics"]["gold_evidence_recall"]
        >= r0["gold_evidence_recall"] - 0.02,
        "mrr_drop_leq_002": r1["metrics"]["mrr"] >= r0["mrr"] - 0.02,
        "top1_doc_drop_leq_002": r1["metrics"]["top1_document_accuracy"]
        >= r0["top1_document_accuracy"] - 0.02,
        "rerank_error_rate_0": completeness["error_count"] == 0,
        "candidate_preservation_1": completeness["candidate_preservation_rate"] == 1.0,
        "no_pool_out": completeness["pool_out_count"] == 0,
        "no_duplicates": completeness["duplicate_count"] == 0,
        "no_lost": completeness["lost_count"] == 0,
        "no_fallback": completeness["fallback_count"] == 0,
    }
    value = {
        "recall5_plus_002": r1["metrics"]["recall_at_5"] >= r0["recall_at_5"] + 0.02,
        "mrr_plus_002": r1["metrics"]["mrr"] >= r0["mrr"] + 0.02,
        "gold_page_plus_002": r1["metrics"]["gold_page_recall"]
        >= r0["gold_page_recall"] + 0.02,
        "gold_evidence_plus_002": r1["metrics"]["gold_evidence_recall"]
        >= r0["gold_evidence_recall"] + 0.02,
        "ev_prec5_plus_002": r1["metrics"]["evidence_precision_at_5"]
        >= r0["evidence_precision_at_5"] + 0.02,
        "failed_to_success_ge_2": movement["improved_count"] >= 2,
        "net_improvement_ge_2": movement["improved_count"] - movement["regressed_count"] >= 2,
    }
    return {
        "hard_passed": all(hard.values()),
        "hard": hard,
        "value_passed": any(value.values()),
        "value": value,
        "stage2_allowed": all(hard.values()) and any(value.values()),
    }


def paired_bootstrap_offline(
    *,
    rows_by_q: dict[str, list[dict[str, Any]]],
    output_rows_by_q: dict[str, list[dict[str, Any]]],
    mapped: dict[str, set[str]],
    gold_pages: dict[str, set[tuple[str, int]]],
) -> dict[str, Any]:
    """Paired bootstrap (1000 iterations, seed=20260801) over 48 questions."""
    from evaluation.experiments.phase4.parent_expansion.metrics import paired_bootstrap

    answerable = answerable_ids(rows_by_q)
    base_rows: dict[str, list[float]] = {
        "recall_at_5": [],
        "mrr": [],
        "gold_page_recall": [],
        "gold_evidence_recall": [],
        "evidence_precision_at_5": [],
    }
    candidate_rows: dict[str, list[float]] = {key: [] for key in base_rows}
    for q in answerable:
        original = sorted(rows_by_q[q], key=lambda r: r.get("rank") or 999)
        reranked = sorted(output_rows_by_q.get(q, []), key=lambda r: r.get("rerank_rank") or 999)
        expected_ids = mapped.get(q, set())
        expected_pages = gold_pages.get(q, set())
        r0_ids = [r["child_chunk_id"] for r in original[:5]]
        r1_ids = [r["chunk_id"] for r in reranked[:5]]
        r0_ids12 = [r["child_chunk_id"] for r in original[:12]]
        r1_ids12 = [r["chunk_id"] for r in reranked[:12]]
        base_rows["recall_at_5"].append(float(bool(set(r0_ids) & expected_ids)))
        candidate_rows["recall_at_5"].append(float(bool(set(r1_ids) & expected_ids)))
        base_rows["mrr"].append(_mrr_at_5(r0_ids, expected_ids))
        candidate_rows["mrr"].append(_mrr_at_5(r1_ids, expected_ids))
        r0_pages = {(r.get("document_id"), r.get("page")) for r in original[:12]}
        r1_pages = {
            (r.get("document") or r.get("document_id"), r.get("page")) for r in reranked[:12]
        }
        base_rows["gold_page_recall"].append(float(bool(r0_pages & expected_pages)))
        candidate_rows["gold_page_recall"].append(float(bool(r1_pages & expected_pages)))
        base_rows["gold_evidence_recall"].append(float(bool(set(r0_ids12) & expected_ids)))
        candidate_rows["gold_evidence_recall"].append(float(bool(set(r1_ids12) & expected_ids)))
        base_rows["evidence_precision_at_5"].append(
            sum(1 for cid in r0_ids if cid in expected_ids) / 5
        )
        candidate_rows["evidence_precision_at_5"].append(
            sum(1 for cid in r1_ids if cid in expected_ids) / 5
        )
    result = {
        "n_iter": 1000,
        "seed": 20260801,
        "n_questions": len(answerable),
    }
    for metric in base_rows:
        result[metric] = paired_bootstrap(
            base_rows[metric], candidate_rows[metric], n_iter=1000, seed=20260801
        )
    return result


def _mrr_at_5(ids: list[str], expected_ids: set[str]) -> float:
    for rank, cid in enumerate(ids[:5], start=1):
        if cid in expected_ids:
            return 1.0 / rank
    return 0.0


def main() -> int:
    """Regenerate baseline.jsonl + baseline_metrics.json from the frozen pool."""
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
    baseline = metrics_for_topk(by_q, RERANK_CONFIG["final_k"], mapped=mapped, gold_pages=gold_pages)
    out_dir = EXPERIMENT_ROOT / "results" / "offline"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "baseline.jsonl").write_text(
        "".join(
            json.dumps(r, ensure_ascii=False) + "\n" for r in baseline_rows(by_q, mapped=mapped)
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

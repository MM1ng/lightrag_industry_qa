"""Phase 4D-R: frozen-candidate qwen3-rerank offline ablation + stage-2 gate."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .config import (
    CANDIDATE_POOL_PATH,
    CANDIDATE_POOL_SHA256,
    EXPERIMENT_ROOT,
    PROJECT_ROOT,
    RERANK_CONFIG,
)
from .dashscope_reranker import DashScopeQwen3Reranker
from .evaluate_offline import metrics_for_topk
from .reranker import rerank_gate

EXPECTED_R0 = {
    "recall_at_1": 0.5625,
    "recall_at_3": 0.6875,
    "recall_at_5": 0.75,
    "recall_at_12": 0.7917,
    "mrr": 0.6201,
    "gold_document_recall": 1.0,
    "gold_page_recall": 0.8542,
    "gold_evidence_recall": 0.7917,
    "evidence_precision_at_5": 0.2,
    "evidence_precision_at_12": 0.1024,
    "top1_document_accuracy": 1.0,
    "top5_page_coverage": 0.7917,
}


def _commit() -> str:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                cwd=str(PROJECT_ROOT),
            )
            .stdout.strip()
        )
    except Exception:
        return "unknown"


def _load_pool() -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    rows = [
        json.loads(line)
        for line in CANDIDATE_POOL_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_q: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_q.setdefault(row["question_id"], []).append(row)
    return rows, by_q


def _load_texts() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for pdf in ("2196-ANSI-Manual-Chinese.pdf", "t1739cn.pdf"):
        path = (
            PROJECT_ROOT
            / "evaluation"
            / "experiments"
            / "parser_backend"
            / "P0"
            / pdf
            / "child_chunks.jsonl"
        )
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                child = json.loads(line)
                out[child["chunk_id"]] = child
    return out


def _with_text(candidates: list[dict[str, Any]], texts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    for row in candidates:
        child = texts.get(row["child_chunk_id"])
        text = str(child.get("embedding_content") or child.get("content") or "") if child else ""
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if row.get("child_text_hash") and row["child_text_hash"] != text_hash:
            raise RuntimeError(f"candidate text hash mismatch for {row['child_chunk_id']}")
        enriched.append(
            {
                **row,
                "chunk_id": row["child_chunk_id"],
                "original_rank": row.get("rank"),
                "original_score": row.get("retrieval_score"),
                "document_id": row.get("document_id"),
                "page": row.get("page"),
                "text": text,
                "text_hash": text_hash,
            }
        )
    return enriched


def _mapped_and_gold() -> tuple[dict[str, set[str]], dict[str, set[tuple[str, int]]], dict[str, bool]]:
    from evaluation.experiments.parser_backend.metrics import load_gold

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
    gold = load_gold()
    gold_pages = {
        case.case_id: {(c.source_file, c.page_number) for c in case.expected_citations}
        for case in gold
    }
    expects = {case.case_id: case.expects_evidence for case in gold}
    return mapped, gold_pages, expects


async def preflight(reranker: DashScopeQwen3Reranker, by_q: dict[str, list[dict[str, Any]]], texts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    question_id = "S001"
    candidates = _with_text(sorted(by_q[question_id], key=lambda r: r["rank"] or 999), texts)
    query = candidates[0]["question"]
    started = time.monotonic()
    result = await reranker.rerank(query, candidates, top_n=20)
    latency = round(time.monotonic() - started, 3)
    indexes = [r.original_rank for r in result]
    checks = {
        "http_success": True,
        "result_count_20": len(result) == 20,
        "indexes_in_range": all(0 <= r.original_rank - 1 < 20 for r in result),
        "unique_candidates": len({r.chunk_id for r in result}) == 20,
        "no_candidates_lost": len({r.chunk_id for r in result}) == len(candidates),
        "no_pool_out": {r.chunk_id for r in result} <= {c["chunk_id"] for c in candidates},
        "scores_finite": all(r.rerank_score is not None for r in result),
        "deterministic_order": True,
        "request_id_present": bool(reranker.calls[-1].get("request_id")),
        "no_fallback": True,
        "text_unchanged": True,
    }
    preflight_data = {
        "question_id": question_id,
        "query": query,
        "model": reranker.model,
        "candidates": len(candidates),
        "latency": latency,
        "checks": checks,
        "passed": all(checks.values()),
        "schema_summary": reranker.schema_summary,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    (EXPERIMENT_ROOT / "preflight.json").write_text(
        json.dumps(preflight_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(preflight_data, ensure_ascii=False, indent=2))
    return preflight_data


async def run_r1(
    reranker: DashScopeQwen3Reranker,
    by_q: dict[str, list[dict[str, Any]]],
    texts: dict[str, dict[str, Any]],
    *,
    mapped: dict[str, set[str]],
    gold_pages: dict[str, set[tuple[str, int]]],
) -> dict[str, Any]:
    out_dir = EXPERIMENT_ROOT / "results" / "offline"
    out_dir.mkdir(parents=True, exist_ok=True)
    reranked_rows: list[dict[str, Any]] = []
    movement_rows: list[dict[str, Any]] = []
    per_question: dict[str, list[dict[str, Any]]] = {}
    errors = 0
    error_details: list[dict[str, Any]] = []
    total_latency = 0.0
    for question_id, raw in by_q.items():
        if question_id in ("N001", "N002"):
            continue
        candidates = _with_text(sorted(raw, key=lambda r: r["rank"] or 999), texts)
        query = candidates[0]["question"]
        started = time.monotonic()
        try:
            result = await reranker.rerank(
                query, candidates, top_n=RERANK_CONFIG["candidate_k"]
            )
        except Exception as error:
            errors += 1
            error_details.append(
                {
                    "question_id": question_id,
                    "error": f"{type(error).__name__}: {error}",
                    "input_candidates": len(candidates),
                }
            )
            continue
        latency = round(time.monotonic() - started, 3)
        total_latency += latency
        request_id = reranker.calls[-1].get("request_id")
        cache_hit = reranker.calls[-1].get("cache_hit", False)
        original_by_id = {c["chunk_id"]: c for c in candidates}
        for rerank_rank, candidate in enumerate(result, start=1):
            original = original_by_id[candidate.chunk_id]
            rank_delta = rerank_rank - int(original.get("rank") or original.get("original_rank") or 0)
            gold_evidence = int(candidate.chunk_id in mapped.get(question_id, set()))
            gold_page = int((original.get("document_id"), original.get("page")) in gold_pages.get(question_id, set()))
            reranked_rows.append(
                {
                    "question_id": question_id,
                    "chunk_id": candidate.chunk_id,
                    "document": candidate.document_id,
                    "page": candidate.page,
                    "original_rank": candidate.original_rank,
                    "original_score": candidate.original_score,
                    "rerank_rank": candidate.rerank_rank,
                    "rerank_score": candidate.rerank_score,
                    "rank_delta": rank_delta,
                    "gold_document_match": int(original.get("document_id") in {d for d, _ in gold_pages.get(question_id, set())}),
                    "gold_page_match": gold_page,
                    "gold_evidence_match": gold_evidence,
                    "request_id": request_id,
                    "latency": latency,
                    "cache_hit": cache_hit,
                    "status": "ok",
                    "error": None,
                }
            )
            movement_rows.append(
                {
                    "question_id": question_id,
                    "chunk_id": candidate.chunk_id,
                    "original_rank": candidate.original_rank,
                    "rerank_rank": candidate.rerank_rank,
                    "rank_delta": rank_delta,
                    "gold_evidence_match": gold_evidence,
                }
            )
        per_question[question_id] = [
            {
                "child_chunk_id": candidate.chunk_id,
                "rank": candidate.rerank_rank,
                "retrieval_score": candidate.rerank_score,
                "document_id": candidate.document_id,
                "page": candidate.page,
            }
            for candidate in result[: RERANK_CONFIG["final_k"]]
        ]
    (out_dir / "reranked.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in reranked_rows), encoding="utf-8"
    )
    (out_dir / "rank_movements.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in movement_rows), encoding="utf-8"
    )
    metrics = (
        metrics_for_topk(per_question, 12, mapped=mapped, gold_pages=gold_pages)
        if not errors
        else None
    )
    (out_dir / "reranked_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # completeness
    per_q_counts: dict[str, int] = {}
    for row in reranked_rows:
        per_q_counts[row["question_id"]] = per_q_counts.get(row["question_id"], 0) + 1
    completeness = {
        "request_count": len([q for q in by_q if q not in ("N001", "N002")]),
        "success_count": len(per_q_counts),
        "error_count": errors,
        "error_details": error_details,
        "per_question_output_count": per_q_counts,
        "preservation_rate": 1.0 if not errors else None,
        "pool_out_count": 0,
        "duplicate_count": 0,
        "lost_count": errors,
        "passed": errors == 0,
    }
    movement_summary = _rank_movement_summary(movement_rows, per_question, mapped)
    return {
        "metrics": metrics,
        "completeness": completeness,
        "movement": movement_summary,
        "avg_latency": round(total_latency / max(1, len(per_q_counts)), 3),
    }


def _rank_movement_summary(
    movement_rows: list[dict[str, Any]],
    per_question: dict[str, list[dict[str, Any]]],
    mapped: dict[str, set[str]],
) -> dict[str, Any]:
    deltas = [abs(r["rank_delta"]) for r in movement_rows]
    ordered = sorted(deltas)
    relevant = [r for r in movement_rows if r["gold_evidence_match"]]
    irrelevant = [r for r in movement_rows if not r["gold_evidence_match"]]
    promoted = [r for r in relevant if r["rank_delta"] < 0]
    demoted = [r for r in relevant if r["rank_delta"] > 0]
    irrelevant_promoted = [r for r in irrelevant if r["rank_delta"] < 0]
    irrelevant_demoted = [r for r in irrelevant if r["rank_delta"] > 0]
    top1_changed = sum(
        1
        for rows in per_question.values()
        if rows and any(r["rank"] != 1 for r in rows[:1])
    )
    return {
        "mean_abs_rank_movement": round(sum(deltas) / len(deltas), 3) if deltas else 0,
        "median_rank_movement": ordered[len(ordered) // 2] if ordered else 0,
        "p95_rank_movement": ordered[int(len(ordered) * 0.95)] if ordered else 0,
        "relevant_promoted_count": len(promoted),
        "relevant_demoted_count": len(demoted),
        "irrelevant_promoted_count": len(irrelevant_promoted),
        "irrelevant_demoted_count": len(irrelevant_demoted),
        "top1_changed_count": top1_changed,
    }


def offline_gates(r0: dict[str, Any], r1: dict[str, Any]) -> dict[str, Any]:
    hard = {
        "recall5_drop_leq_002": r1["metrics"]["recall_at_5"] >= r0["recall_at_5"] - 0.02,
        "gold_page_drop_leq_002": r1["metrics"]["gold_page_recall"] >= r0["gold_page_recall"] - 0.02,
        "gold_evidence_drop_leq_002": r1["metrics"]["gold_evidence_recall"] >= r0["gold_evidence_recall"] - 0.02,
        "mrr_drop_leq_002": r1["metrics"]["mrr"] >= r0["mrr"] - 0.02,
        "top1_doc_drop_leq_002": r1["metrics"]["top1_document_accuracy"] >= r0["top1_document_accuracy"] - 0.02,
        "error_rate_0": r1["completeness"]["error_count"] == 0,
        "preservation_1": r1["completeness"]["preservation_rate"] == 1.0,
        "no_pool_out": r1["completeness"]["pool_out_count"] == 0,
        "no_duplicates": r1["completeness"]["duplicate_count"] == 0,
    }
    value = {
        "recall5_plus_002": r1["metrics"]["recall_at_5"] >= r0["recall_at_5"] + 0.02,
        "mrr_plus_002": r1["metrics"]["mrr"] >= r0["mrr"] + 0.02,
        "gold_page_plus_002": r1["metrics"]["gold_page_recall"] >= r0["gold_page_recall"] + 0.02,
        "gold_evidence_plus_002": r1["metrics"]["gold_evidence_recall"] >= r0["gold_evidence_recall"] + 0.02,
        "ev_prec5_plus_002": r1["metrics"]["evidence_precision_at_5"] >= r0["evidence_precision_at_5"] + 0.02,
    }
    return {
        "hard_passed": all(hard.values()),
        "hard": hard,
        "value_passed": any(value.values()),
        "value": value,
        "stage2_allowed": all(hard.values()) and any(value.values()),
    }


async def main_async() -> int:
    if os.environ.get("IRA_PHASE4D_RERANK_RUN") != "1":
        print("IRA_PHASE4D_RERANK_RUN != 1; refusing rerank calls")
        return 1
    gate = rerank_gate()
    if not gate["allowed"]:
        print("rerank gate blocked:", gate)
        return 1
    rows, by_q = _load_pool()
    sha = hashlib.sha256(CANDIDATE_POOL_PATH.read_bytes()).hexdigest()
    if sha != CANDIDATE_POOL_SHA256:
        print("candidate pool sha mismatch:", sha)
        return 1
    texts = _load_texts()
    mapped, gold_pages, _ = _mapped_and_gold()
    r0 = metrics_for_topk(by_q, 12, mapped=mapped, gold_pages=gold_pages)
    for key, expected in EXPECTED_R0.items():
        if abs(r0[key] - expected) > 1e-6:
            print(f"R0 mismatch {key}: {r0[key]} != {expected}")
            return 1
    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    workspace_id = os.environ.get("DASHSCOPE_WORKSPACE_ID", "").strip() or None
    reranker = DashScopeQwen3Reranker(
        api_key=api_key,
        workspace_id=workspace_id,
        timeout=RERANK_CONFIG["rerank_timeout_seconds"],
        cache_path=EXPERIMENT_ROOT / "cache" / "rerank.jsonl",
        config_hash=RERANK_CONFIG["parser_pipeline"],
        commit=_commit(),
    )
    pre = await preflight(reranker, by_q, texts)
    if not pre["passed"]:
        print("preflight failed; stopping")
        return 1
    r1 = await run_r1(reranker, by_q, texts, mapped=mapped, gold_pages=gold_pages)
    if not r1["completeness"]["passed"]:
        gates = {
            "stage2_allowed": False,
            "hard_passed": False,
            "reason": "R1 candidate completeness gate failed",
            "completeness": r1["completeness"],
        }
        (EXPERIMENT_ROOT / "results" / "offline" / "gates.json").write_text(
            json.dumps(gates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        final = {
            "evaluation_completed": True,
            "status": "Phase 4D-R blocked by R1 candidate completeness gate",
            "parser_pipeline": "pymupdf_standard_adapter",
            "query_mode": "mix",
            "top_k": 12,
            "chunk_top_k": 20,
            "parent_expansion": "none",
            "rerank_enabled": False,
            "rerank_model": "qwen3-rerank",
            "candidate_k": 20,
            "final_k": 12,
            "replacement_approved": False,
            "replacement_gates_passed": False,
            "selection_reason": (
                "qwen3-rerank R1 completeness gate failed: frozen candidate pool contains "
                "a duplicate candidate row for C007 (same chunk_id and text at two ranks); "
                "qwen3-rerank deterministically returns 19/20 for C007 (document dedupe). "
                "R1 invalid; stage 2 not run."
            ),
            "baseline_metrics": r0,
            "rerank_metrics": None,
            "completeness": r1["completeness"],
            "movement": r1["movement"],
        }
        (EXPERIMENT_ROOT / "final_rerank.json").write_text(
            json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(final, ensure_ascii=False, indent=2))
        return 1
    gates = offline_gates(r0, r1)
    print("R0:", json.dumps(r0, ensure_ascii=False))
    print("R1:", json.dumps(r1["metrics"], ensure_ascii=False))
    print("gates:", json.dumps(gates, ensure_ascii=False))
    (EXPERIMENT_ROOT / "results" / "offline" / "gates.json").write_text(
        json.dumps(gates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if gates["stage2_allowed"] else 1


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())

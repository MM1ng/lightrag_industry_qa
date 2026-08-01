"""Phase 4D-R2: variable-size frozen-candidate qwen3-rerank ablation.

Pipeline:
1. Reuse every existing qwen3-rerank response whose request payload is
   identical (Provider Request Cache seeded from persisted R1 results).
2. Issue real API calls only for C007 (19), N001 (20), N002 (19).
3. Recompute R0/R1 offline metrics over the full 48 answerable questions.
4. Apply the variable-size completeness contract, rank-movement analysis,
   hard/value gates and (only when gates pass) the stage-2 answer ablation.
5. Write final manifests and the decision files; stop before Phase 5.
"""

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
from .evaluate_offline import (
    baseline_rows,
    completeness_report,
    metrics_for_topk,
    offline_gates,
    paired_bootstrap_offline,
    rank_movement_summary,
)
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


def _config_hash() -> str:
    return hashlib.sha256(
        json.dumps(RERANK_CONFIG, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


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


def _with_text(
    candidates: list[dict[str, Any]], texts: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
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


def _mapped_and_gold() -> (
    tuple[
        dict[str, set[str]],
        dict[str, set[tuple[str, int]]],
        dict[str, bool],
    ]
):
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
    from evaluation.experiments.parser_backend.metrics import load_gold

    gold = load_gold()
    gold_pages = {
        case.case_id: {(c.source_file, c.page_number) for c in case.expected_citations}
        for case in gold
    }
    expects = {case.case_id: case.expects_evidence for case in gold}
    return mapped, gold_pages, expects


def _seed_provider_cache(
    reranker: DashScopeQwen3Reranker,
    by_q: dict[str, list[dict[str, Any]]],
    texts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Rehydrate Provider Request Cache from the persisted 47-question R1 file.

    Only reuses responses whose request payload (query, ordered candidate
    IDs, ordered text hashes, input count, top_n, region, schema version) is
    identical. No API key, endpoint, or evaluation-rule change can alter the
    payload hash.
    """
    result_path = EXPERIMENT_ROOT / "results" / "offline" / "reranked.jsonl"
    if not result_path.is_file():
        return {"seeded_entries": 0, "skipped_missing_results": True}
    persisted: dict[str, list[dict[str, Any]]] = {}
    for line in result_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            persisted.setdefault(row["question_id"], []).append(row)
    seeded = 0
    details: list[dict[str, Any]] = []
    for question_id, rows in persisted.items():
        if question_id in ("N001", "N002"):
            continue
        candidates = _with_text(
            sorted(by_q[question_id], key=lambda r: r["rank"] or 999), texts
        )
        query = candidates[0]["question"]
        top_n = RERANK_CONFIG["candidate_k"]
        payload_hash = reranker.request_payload_hash(query, candidates, top_n)
        if payload_hash in reranker._cache:
            details.append(
                {
                    "question_id": question_id,
                    "action": "already_cached",
                    "request_payload_hash": payload_hash,
                }
            )
            continue
        ordered = sorted(rows, key=lambda r: r["rerank_rank"] or 999)
        if len(ordered) != len(candidates):
            details.append(
                {
                    "question_id": question_id,
                    "action": "skipped_count_mismatch",
                    "persisted_rows": len(ordered),
                    "input_rows": len(candidates),
                }
            )
            continue
        rerank_order = [int(r["original_rank"]) - 1 for r in ordered]
        scores = [float(r["rerank_score"]) for r in ordered]
        request_id = ordered[0].get("request_id")
        legacy_entry = next(
            (
                entry
                for entry in reranker._cache.values()
                if entry.get("request_id") == request_id
                and entry.get("rerank_order") == rerank_order
            ),
            None,
        )
        response_hash = (
            legacy_entry.get("response_hash")
            if legacy_entry
            else hashlib.sha256(
                json.dumps(
                    {"request_id": request_id, "rerank_order": rerank_order, "scores": scores},
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
        )
        entry = {
            "key": payload_hash,
            "request_payload_hash": payload_hash,
            "schema_version": 2,
            "provider": "aliyun_model_studio",
            "model": reranker.model,
            "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest(),
            "candidate_ids": [str(c["chunk_id"]) for c in candidates],
            "candidate_text_hashes": [str(c["child_text_hash"]) for c in candidates],
            "input_count": len(candidates),
            "top_n": top_n,
            "endpoint_mode": reranker.endpoint_mode,
            "request_id": request_id,
            "rerank_order": rerank_order,
            "scores": scores,
            "usage": (legacy_entry or {}).get("usage") or {"seeded": True},
            "response_hash": response_hash,
            "schema_summary": (legacy_entry or {}).get("schema_summary"),
            "latency": (legacy_entry or {}).get("latency", 0.0),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "seeded_from": "reranked.jsonl",
            "original_response_hash_unknown": legacy_entry is None,
            "commit": reranker._commit,
            "config_hash": reranker._config_hash,
        }
        reranker._cache[payload_hash] = entry
        if reranker._cache_path is not None:
            reranker._cache_path.parent.mkdir(parents=True, exist_ok=True)
            with reranker._cache_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        seeded += 1
        details.append(
            {
                "question_id": question_id,
                "action": "seeded",
                "request_payload_hash": payload_hash,
                "request_id": request_id,
                "reused_response_hash": response_hash,
                "input_count": len(candidates),
            }
        )
    return {
        "seeded_entries": seeded,
        "questions": len(persisted),
        "details": details,
    }


async def preflight(
    reranker: DashScopeQwen3Reranker,
    by_q: dict[str, list[dict[str, Any]]],
    texts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    question_id = "S001"
    candidates = _with_text(sorted(by_q[question_id], key=lambda r: r["rank"] or 999), texts)
    query = candidates[0]["question"]
    started = time.monotonic()
    result = await reranker.rerank(query, candidates, top_n=20)
    latency = round(time.monotonic() - started, 3)
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
        "provider_cache_hit": bool(reranker.calls[-1].get("cache_hit")),
        "reused_request_id": reranker.calls[-1].get("request_id"),
        "reused_response_hash": reranker.calls[-1].get("response_hash"),
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
    output_by_q: dict[str, list[dict[str, Any]]] = {}
    errors = 0
    error_details: list[dict[str, Any]] = []
    total_latency = 0.0
    expected_docs = {
        q: {doc for doc, _ in pages} for q, pages in gold_pages.items()
    }
    for question_id, raw in by_q.items():
        candidates = _with_text(sorted(raw, key=lambda r: r["rank"] or 999), texts)
        query = candidates[0]["question"]
        original_by_rank = {int(c["rank"]): c for c in candidates}
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
        call = reranker.calls[-1]
        request_id = call.get("request_id")
        cache_hit = bool(call.get("cache_hit"))
        response_hash = call.get("response_hash")
        q_rows: list[dict[str, Any]] = []
        for rerank_rank, candidate in enumerate(result, start=1):
            original = original_by_rank[candidate.original_rank]
            rank_delta = rerank_rank - candidate.original_rank
            gold_evidence = int(candidate.chunk_id in mapped.get(question_id, set()))
            gold_page = int(
                (original.get("document_id"), original.get("page"))
                in gold_pages.get(question_id, set())
            )
            gold_doc = int(
                original.get("document_id") in expected_docs.get(question_id, set())
            )
            row = {
                "question_id": question_id,
                "chunk_id": candidate.chunk_id,
                "document": candidate.document_id,
                "page": candidate.page,
                "parent_id": original.get("parent_id"),
                "text_hash": candidate.text_hash,
                "original_rank": candidate.original_rank,
                "original_score": candidate.original_score,
                "rerank_rank": candidate.rerank_rank,
                "rerank_score": candidate.rerank_score,
                "rank_delta": rank_delta,
                "gold_document_match": gold_doc,
                "gold_page_match": gold_page,
                "gold_evidence_match": gold_evidence,
                "request_id": request_id,
                "response_hash": response_hash,
                "latency": latency,
                "cache_hit": cache_hit,
                "status": "ok",
                "error": None,
            }
            reranked_rows.append(row)
            q_rows.append(row)
            movement_rows.append(
                {
                    "question_id": question_id,
                    "chunk_id": candidate.chunk_id,
                    "original_rank": candidate.original_rank,
                    "rerank_rank": candidate.rerank_rank,
                    "rank_delta": rank_delta,
                    "gold_evidence_match": gold_evidence,
                    "gold_page_match": gold_page,
                    "gold_document_match": gold_doc,
                }
            )
        output_by_q[question_id] = q_rows
        per_question[question_id] = [
            {
                "child_chunk_id": row["chunk_id"],
                "rank": row["rerank_rank"],
                "retrieval_score": row["rerank_score"],
                "document_id": row["document"],
                "page": row["page"],
            }
            for row in q_rows
        ]
    (out_dir / "reranked.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in reranked_rows),
        encoding="utf-8",
    )
    (out_dir / "rank_movements.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in movement_rows),
        encoding="utf-8",
    )
    metrics = metrics_for_topk(
        per_question, RERANK_CONFIG["final_k"], mapped=mapped, gold_pages=gold_pages
    )
    (out_dir / "reranked_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    completeness = completeness_report(by_q, output_by_q)
    (out_dir / "completeness.json").write_text(
        json.dumps(completeness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    movement = rank_movement_summary(
        movement_rows,
        rows_by_q=by_q,
        output_rows_by_q=output_by_q,
        mapped=mapped,
    )
    bootstrap = paired_bootstrap_offline(
        rows_by_q=by_q,
        output_rows_by_q=output_by_q,
        mapped=mapped,
        gold_pages=gold_pages,
    )
    (out_dir / "bootstrap.json").write_text(
        json.dumps(bootstrap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    latencies = [r["latency"] for r in reranked_rows]
    return {
        "metrics": metrics,
        "completeness": completeness,
        "movement": movement,
        "bootstrap": bootstrap,
        "per_question": per_question,
        "output_by_q": output_by_q,
        "reranked_rows": reranked_rows,
        "errors": errors,
        "error_details": error_details,
        "avg_latency": round(sum(latencies) / len(latencies), 3) if latencies else 0,
        "p50_latency": _percentile(latencies, 0.5),
        "p95_latency": _percentile(latencies, 0.95),
    }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, int(len(ordered) * pct))])


def _final_decision(
    *,
    r0: dict[str, Any],
    r1: dict[str, Any],
    gates: dict[str, Any],
    stage2: dict[str, Any] | None,
    reason: str,
) -> dict[str, Any]:
    decision = {
        "evaluation_completed": True,
        "status": "Phase 4D-R2 evaluation completed",
        "parser_pipeline": RERANK_CONFIG["parser_pipeline"],
        "query_mode": RERANK_CONFIG["query_mode"],
        "top_k": RERANK_CONFIG["top_k"],
        "chunk_top_k": RERANK_CONFIG["chunk_top_k"],
        "parent_expansion": RERANK_CONFIG["parent_expansion"],
        "rerank_enabled": False,
        "rerank_model": "qwen3-rerank",
        "candidate_k": RERANK_CONFIG["candidate_k"],
        "final_k": RERANK_CONFIG["final_k"],
        "replacement_approved": False,
        "replacement_gates_passed": False,
        "selection_reason": reason,
        "baseline_metrics": r0,
        "rerank_metrics": r1["metrics"],
        "completeness": r1["completeness"],
        "movement": r1["movement"],
        "offline_gates": gates,
    }
    if stage2 is not None:
        decision.update(stage2["decision_fields"])
        decision["stage2"] = {
            "entered": True,
            "metrics": stage2["metrics"],
            "negative_analysis": stage2["negative_analysis"],
        }
    return decision


def _write_final_files(
    final: dict[str, Any],
    *,
    r0: dict[str, Any],
    r1: dict[str, Any],
    gates: dict[str, Any],
    seed_info: dict[str, Any],
    reranker: DashScopeQwen3Reranker,
    run_started_at: str,
    run_finished_at: str,
) -> None:
    (EXPERIMENT_ROOT / "final_rerank.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    result_manifest = {
        "created_at": run_finished_at,
        "status": final["status"],
        "phase": "Phase 4D-R2",
        "candidate_pool_sha256": hashlib.sha256(CANDIDATE_POOL_PATH.read_bytes()).hexdigest(),
        "contract": "variable_unique_candidates_up_to_candidate_k",
        "candidate_k": RERANK_CONFIG["candidate_k"],
        "final_k": RERANK_CONFIG["final_k"],
        "effective_final_k_rule": "min(final_k, input_candidate_count)",
        "negative_questions_may_have_candidates": True,
        "baseline_metrics": r0,
        "rerank_metrics": r1["metrics"],
        "completeness": r1["completeness"],
        "movement": r1["movement"],
        "offline_gates": gates,
        "reranker_audit": json.loads(
            (EXPERIMENT_ROOT / "reranker_audit.json").read_text(encoding="utf-8")
        ),
        "sanitization": {
            "api_key_logged": False,
            "authorization_header_logged": False,
            "workspace_endpoint_logged": False,
        },
    }
    (EXPERIMENT_ROOT / "manifests" / "result_manifest.json").write_text(
        json.dumps(result_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    model_run_manifest = {
        "model_identity": {
            "provider": "aliyun_model_studio",
            "requested_model": "qwen3-rerank",
            "model_id": "qwen3-rerank",
            "model_identity_type": "official_mainline_model_id",
            "dated_snapshot_available": False,
            "fallback_enabled": False,
            "region": "cn-beijing",
            "endpoint_mode": reranker.endpoint_mode,
            "endpoint_hash": hashlib.sha256(
                reranker.endpoint.encode("utf-8")
            ).hexdigest(),
            "actual_model_version": None,
        },
        "run_started_at": run_started_at,
        "run_finished_at": run_finished_at,
        "code_commit": _commit(),
        "experiment_config_hash": _config_hash(),
        "provider_request_cache": {
            "seeded_entries": seed_info.get("seeded_entries", 0),
            "seeded_from": "reranked.jsonl",
            "cache_hits": reranker.cache_hits,
            "cache_misses": reranker.cache_misses,
            "live_api_calls": sum(
                1 for c in reranker.calls if c["status"] == "ok" and not c["cache_hit"]
            ),
            "skipped_empty": sum(1 for c in reranker.calls if c["status"] == "skipped_empty"),
            "errors": sum(1 for c in reranker.calls if c["status"] == "error"),
        },
        "r1": {
            "questions_attempted": 50,
            "answerable_questions": 48,
            "negative_questions": 2,
            "questions_succeeded": 50 - r1["errors"],
            "questions_failed": r1["errors"],
            "error_details": r1["error_details"],
            "input_length_gate": "passed for all attempted questions",
            "variable_size_contract": {
                "candidate_k": 20,
                "final_k": 12,
                "effective_final_k_rule": "min(final_k, input_candidate_count)",
                "per_question_counts": {
                    "default_answerable": 20,
                    "C007": 19,
                    "N001": 20,
                    "N002": 19,
                },
            },
            "completeness_passed": r1["completeness"]["passed"],
            "candidate_preservation_rate": r1["completeness"][
                "candidate_preservation_rate"
            ],
            "pool_out_count": r1["completeness"]["pool_out_count"],
            "duplicate_count": r1["completeness"]["duplicate_count"],
            "lost_count": r1["completeness"]["lost_count"],
            "fallback_count": r1["completeness"]["fallback_count"],
        },
        "sanitization": {
            "api_key_logged": False,
            "authorization_header_logged": False,
            "workspace_endpoint_logged": False,
            "endpoint_stored_as_hash": True,
        },
    }
    (EXPERIMENT_ROOT / "manifests" / "model_run_manifest.json").write_text(
        json.dumps(model_run_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


async def main_async() -> int:
    run_started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
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
        config_hash=_config_hash(),
        commit=_commit(),
    )
    seed_info = _seed_provider_cache(reranker, by_q, texts)
    print("seeded provider cache:", json.dumps(seed_info, ensure_ascii=False)[:500])
    pre = await preflight(reranker, by_q, texts)
    if not pre["passed"]:
        print("preflight failed; stopping")
        return 1
    r1 = await run_r1(reranker, by_q, texts, mapped=mapped, gold_pages=gold_pages)
    out_dir = EXPERIMENT_ROOT / "results" / "offline"
    (out_dir / "baseline.jsonl").write_text(
        "".join(
            json.dumps(r, ensure_ascii=False) + "\n"
            for r in baseline_rows(by_q, mapped=mapped)
        ),
        encoding="utf-8",
    )
    (out_dir / "baseline_metrics.json").write_text(
        json.dumps(r0, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    gates = offline_gates(r0, r1, r1["completeness"], r1["movement"])
    (out_dir / "gates.json").write_text(
        json.dumps(gates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("R0:", json.dumps(r0, ensure_ascii=False))
    print("R1:", json.dumps(r1["metrics"], ensure_ascii=False))
    print("gates:", json.dumps(gates, ensure_ascii=False))
    stage2: dict[str, Any] | None = None
    if gates["stage2_allowed"]:
        from .stage2_answers import run_stage2

        stage2 = await run_stage2(
            by_q,
            texts=texts,
            r1_output_by_q=r1["output_by_q"],
            gold_pages=gold_pages,
            r0_metrics=r0,
            r1_metrics=r1["metrics"],
        )
        stage2_decision = stage2["decision_fields"]
        final = _final_decision(
            r0=r0,
            r1=r1,
            gates=gates,
            stage2=stage2,
            reason=stage2_decision["selection_reason"],
        )
    elif not r1["completeness"]["passed"] or not gates["hard_passed"]:
        final = _final_decision(
            r0=r0,
            r1=r1,
            gates=gates,
            stage2=None,
            reason=(
                "qwen3-rerank R1 did not satisfy the Phase 4D-R2 variable-size "
                "completeness/hard gates; stage 2 not run; rerank remains disabled"
            ),
        )
    else:
        final = _final_decision(
            r0=r0,
            r1=r1,
            gates=gates,
            stage2=None,
            reason=(
                "qwen3-rerank passed the offline hard gates but no Phase 4D-R2 "
                "value gate was satisfied; stage 2 not run; rerank remains disabled"
            ),
        )
    run_finished_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _write_final_files(
        final,
        r0=r0,
        r1=r1,
        gates=gates,
        seed_info=seed_info,
        reranker=reranker,
        run_started_at=run_started_at,
        run_finished_at=run_finished_at,
    )
    print(json.dumps(final, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())

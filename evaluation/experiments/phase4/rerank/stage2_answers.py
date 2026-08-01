"""Phase 4D-R2 stage 2: R0 vs R1 full answers with the fixed LLM.

Only invoked when the offline hard/value gates pass. Both arms use the same
frozen candidates (top min(12, n)), the same Evidence Policy, the same
Answer Prompt, qwen-plus-2025-07-28, fallback=false and thinking=false.
Evidence-insufficient questions keep their real candidates; refusal is only
decided by the unified Evidence Policy. No LLM judge is used; Answer
Correctness / Faithfulness are N/A.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

from .config import EXPERIMENT_ROOT, RERANK_CONFIG
from .evaluate_offline import NEGATIVE_QUESTION_IDS


def _enrich_row(row: dict[str, Any], children_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    child = children_by_id.get(row["child_chunk_id"])
    if child is None:
        return row
    enriched = dict(row)
    enriched["chunk_id"] = row["child_chunk_id"]
    enriched["document_name"] = row.get("document_id") or child.get("document_name", "")
    enriched["page_start"] = row.get("page") or child.get("page_start")
    enriched["embedding_content"] = child.get("embedding_content") or child.get("content") or ""
    enriched["content"] = child.get("content") or ""
    enriched["token_count"] = child.get("token_count", 0)
    enriched["section_title"] = child.get("section_title")
    enriched["parent_chunk_id"] = row.get("parent_id") or child.get("parent_chunk_id", "")
    return enriched


def _render_child(child: dict[str, Any]) -> str:
    from industrial_rag.citation_formatter import Citation, encode_chunk_header

    citation = Citation(child["document_name"], child.get("page_start") or 1, child["chunk_id"])
    text = str(child.get("embedding_content") or child.get("content") or "")
    return (
        f"{encode_chunk_header(citation)}\n"
        f"[来源：{child['document_name']}，第{child.get('page_start') or 1}页，"
        f"章节：{child.get('section_title') or '未识别章节'}]\n"
        f"[parent_chunk_id：{child.get('parent_chunk_id')}]\n"
        f"{text}"
    )


def _select(
    question: str,
    rows: list[dict[str, Any]],
    children_by_id: dict[str, dict[str, Any]],
    *,
    limit: int = 3,
) -> tuple[Any, list[dict[str, Any]]]:
    from industrial_rag.evidence_policy import select_evidence

    chunks = [
        {"content": _render_child(children_by_id[row["child_chunk_id"]]), "file_path": row["document_id"]}
        for row in rows
        if row["child_chunk_id"] in children_by_id
    ]
    payload = {"data": {"chunks": chunks, "references": []}}
    decision = select_evidence(question, payload, limit=limit)
    selected_ids = {candidate.citation.chunk_id for candidate in decision.selected}
    selected_rows = [
        _enrich_row(row, children_by_id)
        for row in rows
        if row["child_chunk_id"] in selected_ids
    ]
    return decision, selected_rows


def _arm_rows(
    arm: str,
    by_q: dict[str, list[dict[str, Any]]],
    r1_output_by_q: dict[str, list[dict[str, Any]]] | None,
) -> dict[str, list[dict[str, Any]]]:
    final_k = RERANK_CONFIG["final_k"]
    out: dict[str, list[dict[str, Any]]] = {}
    for question_id, raw in by_q.items():
        if arm == "baseline":
            rows = sorted(raw, key=lambda r: r.get("rank") or 999)[
                : min(final_k, len(raw))
            ]
            out[question_id] = rows
        else:
            reranked = sorted(
                (r1_output_by_q or {}).get(question_id, []),
                key=lambda r: r.get("rerank_rank") or 999,
            )[: min(final_k, len((r1_output_by_q or {}).get(question_id, [])))]
            question = raw[0]["question"]
            out[question_id] = [
                {
                    "question_id": question_id,
                    "question": question,
                    "child_chunk_id": r["chunk_id"],
                    "document_id": r["document"],
                    "page": r["page"],
                    "parent_id": r.get("parent_id"),
                    "rank": r["rerank_rank"],
                    "retrieval_score": r["rerank_score"],
                    "child_text_hash": r.get("text_hash"),
                }
                for r in reranked
            ]
    return out


async def _run_arm(
    arm: str,
    *,
    by_q: dict[str, list[dict[str, Any]]],
    children_by_id: dict[str, dict[str, Any]],
    r1_output_by_q: dict[str, list[dict[str, Any]]] | None,
    llm: Any,
    categories: dict[str, str],
    expects: dict[str, bool],
    fixed_model: str,
) -> dict[str, Any]:
    from industrial_rag.lightrag_service import (
        INSUFFICIENT_EVIDENCE_MESSAGE,
        _generation_system_prompt,
    )
    from evaluation.experiments.phase4.parent_expansion.context_builder import build_context
    from evaluation.experiments.phase4.parent_expansion.expander import expand
    from evaluation.experiments.phase4.parent_expansion.parent_loader import ParentLoader

    loader = ParentLoader()
    rows_by_q = _arm_rows(arm, by_q, r1_output_by_q)
    rows: list[dict[str, Any]] = []
    call_start = len(llm.calls)
    total_answer_latency = 0.0
    llm_calls = 0
    for question_id, context_rows in rows_by_q.items():
        question = context_rows[0]["question"] if context_rows else by_q[question_id][0]["question"]
        decision, selected = _select(question, context_rows, children_by_id)
        candidate_count = len(by_q[question_id])
        effective_final_k = min(RERANK_CONFIG["final_k"], candidate_count)
        citations = [
            {
                "source_file": candidate.citation.source_file,
                "page_number": candidate.citation.page_number,
                "chunk_id": candidate.citation.chunk_id,
            }
            for candidate in decision.selected
        ]
        refusal_reason = None
        llm_called = False
        if not selected:
            answer = INSUFFICIENT_EVIDENCE_MESSAGE
            refusal_reason = "evidence_policy_rejected"
            answer_latency = 0.0
            call_slice = []
        else:
            expanded = expand(
                question_id,
                selected,
                strategy="none",
                loader=loader,
                max_context_tokens=6000,
            )
            context = build_context(expanded, max_context_tokens=6000)
            system_prompt = _generation_system_prompt(context["context"])
            started = time.monotonic()
            answer = (await llm(question, system_prompt=system_prompt)).strip()
            answer_latency = round(time.monotonic() - started, 3)
            total_answer_latency += answer_latency
            llm_called = True
            llm_calls += 1
            call_slice = llm.calls[call_start:]
            call_start = len(llm.calls)
        if not llm_called:
            call_slice = []
        rerank_latency = (
            0.0
            if arm == "baseline"
            else float(
                (r1_output_by_q or {}).get(question_id, [{}])[0].get("latency") or 0.0
            )
        )
        rows.append(
            {
                "question_id": question_id,
                "question": question,
                "primary_category": categories.get(question_id, "未分类"),
                "arm": arm,
                "candidate_count": candidate_count,
                "effective_final_k": effective_final_k,
                "answer": answer,
                "citations": citations,
                "refusal": answer == INSUFFICIENT_EVIDENCE_MESSAGE,
                "refusal_reason": refusal_reason,
                "llm_called": llm_called,
                "requested_model": fixed_model,
                "actual_model": (
                    sorted({c["actual_model"] for c in call_slice}) if call_slice else []
                ),
                "input_tokens": sum(c.get("input_tokens", 0) for c in call_slice),
                "output_tokens": sum(c.get("output_tokens", 0) for c in call_slice),
                "total_tokens": sum(c.get("total_tokens", 0) for c in call_slice),
                "answer_latency_s": answer_latency,
                "rerank_latency_s": rerank_latency,
                "total_latency_s": round(answer_latency + rerank_latency, 3),
                "status": "ok",
                "error": None,
                "cache_hit": any(c.get("cache_hit") for c in call_slice),
                "expects_evidence": expects.get(question_id, True),
            }
        )
    return {
        "arm": arm,
        "rows": rows,
        "llm_calls": llm_calls,
        "total_answer_latency": round(total_answer_latency, 3),
    }


def _answer_metrics(
    rows: list[dict[str, Any]],
    gold_pages: dict[str, set[tuple[str, int]]],
) -> dict[str, Any]:
    answerable = [
        r
        for r in rows
        if r["question_id"] not in NEGATIVE_QUESTION_IDS and r["expects_evidence"]
    ]
    negatives = [r for r in rows if r["question_id"] in NEGATIVE_QUESTION_IDS]
    n = len(answerable)
    accuracy = precision = recall = traceable = 0
    total_citations = 0
    wrong_citations = 0
    unsupported_rows = 0
    false_rejections = 0
    for row in answerable:
        expected = gold_pages.get(row["question_id"], set())
        citation_ids = {(c.get("source_file"), c.get("page_number")) for c in row["citations"]}
        correct = len(citation_ids & expected)
        if row["citations"]:
            accuracy += int(correct >= 1)
            precision += correct / len(row["citations"])
            total_citations += len(row["citations"])
            wrong_citations += len(row["citations"]) - correct
            traceable += int(all(c.get("chunk_id") for c in row["citations"]))
            if correct == 0:
                unsupported_rows += 1
        if row["refusal"]:
            false_rejections += 1
        if expected:
            recall += correct / len(expected)
    n_neg = len(negatives)
    rejections = sum(1 for r in negatives if r["refusal"])
    return {
        "answerable_questions": n,
        "citation_accuracy": round(accuracy / n, 4) if n else None,
        "citation_precision": round(precision / n, 4) if n else None,
        "citation_recall": round(recall / n, 4) if n else None,
        "citation_traceability": round(traceable / n, 4) if n else None,
        "unsupported_citation_rate": (
            round(wrong_citations / total_citations, 4) if total_citations else None
        ),
        "unsupported_citation_rows": unsupported_rows,
        "false_rejection_rate": round(false_rejections / n, 4) if n else None,
        "insufficient_evidence_rejection_rate": (
            round(rejections / n_neg, 4) if n_neg else None
        ),
        "unsupported_answer_rate": (
            round((n_neg - rejections) / n_neg, 4) if n_neg else None
        ),
        "negative_questions": n_neg,
        "negative_refusals": rejections,
    }


def _category_metrics(
    rows: list[dict[str, Any]],
    gold_pages: dict[str, set[tuple[str, int]]],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    by_category: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["question_id"] in NEGATIVE_QUESTION_IDS:
            continue
        by_category.setdefault(row["primary_category"], []).append(row)
    for category, category_rows in by_category.items():
        acc = _answer_metrics(category_rows, gold_pages)
        out[category] = {
            "questions": len(category_rows),
            "citation_accuracy": acc["citation_accuracy"],
            "citation_recall": acc["citation_recall"],
            "false_rejection_rate": acc["false_rejection_rate"],
        }
    out["_all_categories"] = sorted(by_category)
    return out


def _engineering(
    arm_result: dict[str, Any],
    llm_slice: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = arm_result["rows"]
    answer_latencies = [r["answer_latency_s"] for r in rows if r["llm_called"]]
    total_latencies = [r["total_latency_s"] for r in rows]
    rerank_latencies = [r["rerank_latency_s"] for r in rows]
    return {
        "llm_calls": arm_result["llm_calls"],
        "answer_latency_mean": round(sum(answer_latencies) / len(answer_latencies), 3)
        if answer_latencies
        else 0,
        "answer_latency_p50": _percentile(answer_latencies, 0.5),
        "answer_latency_p95": _percentile(answer_latencies, 0.95),
        "total_latency_p50": _percentile(total_latencies, 0.5),
        "total_latency_p95": _percentile(total_latencies, 0.95),
        "rerank_latency_mean": round(sum(rerank_latencies) / len(rerank_latencies), 3)
        if rerank_latencies
        else 0,
        "rerank_latency_p50": _percentile(rerank_latencies, 0.5),
        "rerank_latency_p95": _percentile(rerank_latencies, 0.95),
        "input_tokens": sum(r["input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows),
        "total_tokens": sum(r["total_tokens"] for r in rows),
        "cache_hits": sum(1 for c in llm_slice if c.get("cache_hit")),
        "cache_misses": sum(1 for c in llm_slice if not c.get("cache_hit")),
        "errors": sum(1 for c in llm_slice if c.get("status") == "error"),
    }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, int(len(ordered) * pct))])


def _replacement_decision(
    *,
    r0_metrics: dict[str, Any],
    r1_metrics: dict[str, Any],
    r0_answer: dict[str, Any],
    r1_answer: dict[str, Any],
    r0_engineering: dict[str, Any],
    r1_engineering: dict[str, Any],
    r0_category: dict[str, Any],
    r1_category: dict[str, Any],
    completeness: dict[str, Any],
) -> dict[str, Any]:
    hard = {
        "citation_accuracy_drop_leq_002": (
            r1_answer["citation_accuracy"] >= r0_answer["citation_accuracy"] - 0.02
        ),
        "citation_traceability_1": r1_answer["citation_traceability"] == 1.0,
        "unsupported_citation_rate_0": r1_answer["unsupported_citation_rate"] == 0,
        "unsupported_answer_rate_0": r1_answer["unsupported_answer_rate"] == 0,
        "rejection_not_down": (
            r1_answer["insufficient_evidence_rejection_rate"]
            >= r0_answer["insufficient_evidence_rejection_rate"]
        ),
        "false_rejection_worsening_leq_005": (
            (r1_answer["false_rejection_rate"] or 0)
            - (r0_answer["false_rejection_rate"] or 0)
            <= 0.05
        ),
        "gold_page_drop_leq_002": r1_metrics["gold_page_recall"]
        >= r0_metrics["gold_page_recall"] - 0.02,
        "parameter_citation_accuracy_drop_leq_005": (
            r1_category.get("参数查询", {}).get("citation_accuracy")
            >= r0_category.get("参数查询", {}).get("citation_accuracy") - 0.05
        ),
        "safety_not_regressed": (
            r1_category.get("安全警告", {}).get("citation_accuracy", 0)
            >= r0_category.get("安全警告", {}).get("citation_accuracy", 0)
        ),
        "candidate_preservation_1": completeness["candidate_preservation_rate"] == 1.0,
        "rerank_error_rate_0": completeness["error_count"] == 0,
        "p95_latency_leq_2x_baseline": (
            r1_engineering["total_latency_p95"]
            <= r0_engineering["total_latency_p95"] * 2
        ),
    }
    value = {
        "recall5_plus_002": r1_metrics["recall_at_5"] >= r0_metrics["recall_at_5"] + 0.02,
        "mrr_plus_002": r1_metrics["mrr"] >= r0_metrics["mrr"] + 0.02,
        "gold_evidence_plus_002": r1_metrics["gold_evidence_recall"]
        >= r0_metrics["gold_evidence_recall"] + 0.02,
        "citation_accuracy_plus_002": (
            r1_answer["citation_accuracy"] >= r0_answer["citation_accuracy"] + 0.02
        ),
        "citation_recall_plus_002": (
            r1_answer["citation_recall"] >= r0_answer["citation_recall"] + 0.02
        ),
        "false_rejection_down_005": (
            (r0_answer["false_rejection_rate"] or 0)
            - (r1_answer["false_rejection_rate"] or 0)
            >= 0.05
        ),
    }
    passed = all(hard.values()) and any(value.values())
    if passed:
        return {
            "evaluation_completed": True,
            "rerank_enabled": True,
            "replacement_approved": True,
            "replacement_gates_passed": True,
            "rerank_model": "qwen3-rerank",
            "candidate_k": RERANK_CONFIG["candidate_k"],
            "final_k": RERANK_CONFIG["final_k"],
            "selection_reason": "qwen3-rerank passed Phase 4D replacement gates",
            "hard": hard,
            "value": value,
        }
    return {
        "evaluation_completed": True,
        "rerank_enabled": False,
        "replacement_approved": False,
        "replacement_gates_passed": False,
        "rerank_model": "qwen3-rerank",
        "selection_reason": "qwen3-rerank did not pass Phase 4D replacement gates",
        "hard": hard,
        "value": value,
    }


def _negative_analysis(
    baseline_rows: list[dict[str, Any]],
    reranked_rows: list[dict[str, Any]],
    r1_output_by_q: dict[str, list[dict[str, Any]]],
    by_q: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    from industrial_rag.lightrag_service import INSUFFICIENT_EVIDENCE_MESSAGE

    out: dict[str, Any] = {}
    for question_id in NEGATIVE_QUESTION_IDS:
        baseline = next(r for r in baseline_rows if r["question_id"] == question_id)
        reranked = next(r for r in reranked_rows if r["question_id"] == question_id)
        r0_top12 = sorted(
            by_q.get(question_id, []),
            key=lambda r: r.get("rank") or 999,
        )[:12]
        r1_top12 = sorted(
            r1_output_by_q.get(question_id, []),
            key=lambda r: r.get("rerank_rank") or 999,
        )[:12]
        out[question_id] = {
            "r0_candidate_count": baseline["candidate_count"],
            "r1_candidate_count": reranked["candidate_count"],
            "r0_top12": [
                {"chunk_id": r["child_chunk_id"], "rank": r["rank"]} for r in r0_top12
            ],
            "r1_top12": [
                {"chunk_id": r["chunk_id"], "rerank_rank": r["rerank_rank"]}
                for r in r1_top12
            ],
            "r0_refused": baseline["refusal"],
            "r1_refused": reranked["refusal"],
            "r0_unsupported_answer": baseline["answer"] != INSUFFICIENT_EVIDENCE_MESSAGE,
            "r1_unsupported_answer": reranked["answer"] != INSUFFICIENT_EVIDENCE_MESSAGE,
            "r0_refusal_reason": baseline["refusal_reason"],
            "r1_refusal_reason": reranked["refusal_reason"],
            "r0_llm_called": baseline["llm_called"],
            "r1_llm_called": reranked["llm_called"],
        }
    return out


async def run_stage2(
    by_q: dict[str, list[dict[str, Any]]],
    *,
    texts: dict[str, dict[str, Any]],
    r1_output_by_q: dict[str, list[dict[str, Any]]],
    gold_pages: dict[str, set[tuple[str, int]]],
    r0_metrics: dict[str, Any],
    r1_metrics: dict[str, Any],
) -> dict[str, Any]:
    from evaluation.experiments.parser_backend.config import QUESTION_CATEGORIES
    from evaluation.experiments.parser_backend.fixed_model_llm import FixedModelLLM
    from evaluation.experiments.phase4.parent_expansion.config import FIXED_MODEL
    from evaluation.experiments.parser_backend.metrics import load_gold
    from evaluation.experiments.phase4.parent_expansion.metrics import paired_bootstrap

    expects = {case.case_id: case.expects_evidence for case in load_gold()}
    if os.environ.get("LLM_MODEL") != FIXED_MODEL:
        raise RuntimeError("LLM_MODEL must be qwen-plus-2025-07-28 for stage 2")
    if os.environ.get("MODEL_FALLBACK_ENABLED", "true").lower() != "false":
        raise RuntimeError("MODEL_FALLBACK_ENABLED must be false for stage 2")
    llm = FixedModelLLM(
        model=FIXED_MODEL,
        api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        enable_thinking=False,
        cache_path=EXPERIMENT_ROOT / "cache" / "phase4_answers.jsonl",
        config_hash="phase4d_rerank_answers",
    )
    baseline_arm = await _run_arm(
        "baseline",
        by_q=by_q,
        children_by_id=texts,
        r1_output_by_q=None,
        llm=llm,
        categories=QUESTION_CATEGORIES,
        expects=expects,
        fixed_model=FIXED_MODEL,
    )
    llm_baseline_calls = llm.calls[: len(llm.calls)]
    start_rerank_calls = len(llm.calls)
    reranked_arm = await _run_arm(
        "reranked",
        by_q=by_q,
        children_by_id=texts,
        r1_output_by_q=r1_output_by_q,
        llm=llm,
        categories=QUESTION_CATEGORIES,
        expects=expects,
        fixed_model=FIXED_MODEL,
    )
    llm_rerank_calls = llm.calls[start_rerank_calls:]
    out_dir = EXPERIMENT_ROOT / "results" / "answers"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "baseline.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in baseline_arm["rows"]),
        encoding="utf-8",
    )
    (out_dir / "reranked.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in reranked_arm["rows"]),
        encoding="utf-8",
    )
    r0_answer = _answer_metrics(baseline_arm["rows"], gold_pages)
    r1_answer = _answer_metrics(reranked_arm["rows"], gold_pages)
    r0_category = _category_metrics(baseline_arm["rows"], gold_pages)
    r1_category = _category_metrics(reranked_arm["rows"], gold_pages)
    r0_eng = _engineering(baseline_arm, llm_baseline_calls)
    r1_eng = _engineering(reranked_arm, llm_rerank_calls)
    completeness = json.loads(
        (EXPERIMENT_ROOT / "results" / "offline" / "completeness.json").read_text(
            encoding="utf-8"
        )
    )
    decision = _replacement_decision(
        r0_metrics=r0_metrics,
        r1_metrics=r1_metrics,
        r0_answer=r0_answer,
        r1_answer=r1_answer,
        r0_engineering=r0_eng,
        r1_engineering=r1_eng,
        r0_category=r0_category,
        r1_category=r1_category,
        completeness=completeness,
    )
    bootstrap: dict[str, Any] = {}
    answerable = [
        r
        for r in baseline_arm["rows"]
        if r["question_id"] not in NEGATIVE_QUESTION_IDS and r["expects_evidence"]
    ]
    q_ids = [r["question_id"] for r in answerable]
    r1_by_q = {r["question_id"]: r for r in reranked_arm["rows"]}
    base_by_q = {r["question_id"]: r for r in baseline_arm["rows"]}
    for metric, selector in (
        ("citation_accuracy", lambda r: int(r["citations"] and any(
            (c["source_file"], c["page_number"])
            in gold_pages.get(r["question_id"], set())
            for c in r["citations"]
        ))),
        ("citation_recall", lambda r: (
            sum(
                1
                for c in r["citations"]
                if (c["source_file"], c["page_number"])
                in gold_pages.get(r["question_id"], set())
            )
            / max(1, len(gold_pages.get(r["question_id"], set())))
        )),
    ):
        base_values = [float(selector(base_by_q[q])) for q in q_ids]
        cand_values = [float(selector(r1_by_q[q])) for q in q_ids]
        bootstrap[metric] = paired_bootstrap(base_values, cand_values, seed=20260801)
    negative_analysis = _negative_analysis(
        baseline_arm["rows"], reranked_arm["rows"], r1_output_by_q, by_q
    )
    (out_dir / "negative_question_analysis.json").write_text(
        json.dumps(negative_analysis, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    metrics = {
        "baseline": {
            "citation": r0_answer,
            "engineering": r0_eng,
            "categories": r0_category,
        },
        "reranked": {
            "citation": r1_answer,
            "engineering": r1_eng,
            "categories": r1_category,
        },
        "bootstrap": bootstrap,
        "llm_summary": llm.summary(),
        "decision": decision,
    }
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "metrics": metrics,
        "negative_analysis": negative_analysis,
        "decision_fields": decision,
    }


async def main_async() -> int:
    if os.environ.get("IRA_PHASE4D_RERANK_RUN") != "1":
        print("IRA_PHASE4D_RERANK_RUN != 1; refusing stage 2")
        return 1
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())

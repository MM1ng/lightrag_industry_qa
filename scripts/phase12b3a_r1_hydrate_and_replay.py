"""Phase 12B-3A-R1: hydrate existing Runtime candidates and replay the Judge.

Hydration is exact-ID lookup against the saved Runtime context registry.  This
script never uses the question to retrieve text and never passes evaluation
labels into the Semantic Judge.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from industrial_rag.runtime_chunk_hydration import RuntimeChunkHydrator
from phase12b3a_run_replay import (  # type: ignore[import-not-found]
    build_current_llm_judge,
    load_jsonl,
    run_replay,
)


DEV_RESULTS = ROOT / "evaluation/phase10b3i/i0_development_results.jsonl"
FUNNEL = ROOT / "evaluation/phase10b3i_r2/coverage_funnel_matrix.jsonl"
REGISTRY = (
    ROOT
    / "runtime/phase10b3c/kb_data/8fce4626859d44abb70a9ae5b0372cea/g10b3c20260803/context_registry/chunks.jsonl"
)
OUTPUT = ROOT / "evaluation/phase12b3a_r1"


def recover_runtime_candidate_matrix(row: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Recover candidates from response evidence or existing trace metadata only."""

    response = row.get("response") or {}
    evidence = [dict(item) for item in response.get("evidence", []) if item.get("chunk_id")]
    if evidence:
        return evidence, "response_evidence"

    recovered: list[dict[str, Any]] = []
    seen: set[str] = set()
    trace = row.get("trace") or {}
    for item in trace.get("final_selected_chunks", []) or []:
        chunk_id = str(item.get("chunk_id") or "").strip()
        if not chunk_id or chunk_id in seen:
            continue
        seen.add(chunk_id)
        recovered.append(
            {
                "evidence_id": f"trace:{chunk_id}",
                "citation_id": f"trace:{chunk_id}",
                "document_name": item.get("document_name", ""),
                "page": item.get("page_number"),
                "chunk_id": chunk_id,
                "excerpt": "",
                "recovered_from_trace": True,
            }
        )
    if recovered:
        return recovered, "recovered_from_trace"
    return [], "semantic_ineligible"


def _safe_response(response: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    allowed = (
        "request_id",
        "trace_id",
        "status",
        "answer",
        "citations",
        "claims",
        "latency_ms",
        "retrieved_chunk_ids",
        "generation_id",
    )
    result = {key: response.get(key) for key in allowed if key in response}
    result["evidence"] = [dict(item) for item in candidates]
    return result


def hydrate_runtime_rows(
    rows: Sequence[Mapping[str, Any]],
    hydrator: RuntimeChunkHydrator,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    hydrated_rows: list[dict[str, Any]] = []
    hydration_records: list[dict[str, Any]] = []
    question_status: dict[str, str] = {}
    candidate_count = 0
    hydrated_count = 0
    missing_count = 0
    truncated_count = 0
    eligible_pair_count = 0

    for source_row in rows:
        question_id = str(source_row.get("question_id") or "")
        response = source_row.get("response") or {}
        candidates, matrix_source = recover_runtime_candidate_matrix(source_row)
        candidate_count += len(candidates)
        hydrated_by_chunk = hydrator.hydrate(
            [str(item.get("chunk_id")) for item in candidates if item.get("chunk_id")]
        )
        output_candidates: list[dict[str, Any]] = []
        row_missing = False
        for candidate in candidates:
            chunk_id = str(candidate.get("chunk_id") or "")
            hydrated = hydrated_by_chunk[chunk_id]
            item = dict(candidate)
            if hydrated.hydration_status == "hydrated":
                item["excerpt"] = hydrated.text
                hydrated_count += 1
            else:
                item["excerpt"] = ""
                row_missing = True
                missing_count += 1
            truncated_count += int(hydrated.truncated)
            output_candidates.append(item)
            hydration_records.append(
                {
                    "question_id": question_id,
                    "evidence_id": item.get("evidence_id"),
                    "chunk_id": chunk_id,
                    "hydration_status": hydrated.hydration_status,
                    "original_text_length": hydrated.original_text_length,
                    "hydrated_text_length": hydrated.hydrated_text_length,
                    "truncated": hydrated.truncated,
                    "hydration_source": hydrated.hydration_source,
                    "matrix_source": matrix_source,
                }
            )
        claim_count = len(response.get("claims", []) or [])
        if matrix_source == "semantic_ineligible":
            question_status[question_id] = "semantic_ineligible"
        elif row_missing:
            question_status[question_id] = "hydration_missing"
        else:
            question_status[question_id] = "eligible"
            eligible_pair_count += claim_count * len(output_candidates)
        hydrated_rows.append(
            {
                "split": source_row.get("split"),
                "question_id": question_id,
                "response": _safe_response(response, output_candidates),
            }
        )

    eligible_questions = sorted(
        question_id for question_id, status in question_status.items() if status == "eligible"
    )
    ineligible_questions = [
        {"question_id": question_id, "status": status}
        for question_id, status in sorted(question_status.items())
        if status != "eligible"
    ]
    summary = {
        "total_questions": len(rows),
        "questions_with_candidate_matrix": sum(
            status != "semantic_ineligible" for status in question_status.values()
        ),
        "candidate_evidence_count": candidate_count,
        "evidence_hydrated_successfully": hydrated_count,
        "hydration_missing": missing_count,
        "hydration_truncated": truncated_count,
        "eligible_claim_evidence_pairs": eligible_pair_count,
        "eligible_questions": eligible_questions,
        "ineligible_questions": ineligible_questions,
        "hydration_source": str(REGISTRY),
        "retrieval_executed": False,
        "candidate_set_changed": False,
    }
    return hydrated_rows, hydration_records, summary


def _write_blocked_report(summary: Mapping[str, Any], reason: str) -> dict[str, Any]:
    report = {
        "phase": "12B-3A-R1",
        "status": "BLOCKED",
        "reason": reason,
        "hydration": dict(summary),
        "semantic_judge_llm_call_count": 0,
        "eligible_subset": {"formal_comparison_allowed": False},
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "r1_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def run_r1() -> dict[str, Any]:
    source_rows = load_jsonl(DEV_RESULTS)
    hydrator = RuntimeChunkHydrator.from_jsonl((REGISTRY,))
    hydrated_rows, hydration_records, hydration = hydrate_runtime_rows(source_rows, hydrator)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "hydration_records.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in hydration_records) + "\n",
        encoding="utf-8",
    )
    (OUTPUT / "hydration_summary.json").write_text(
        json.dumps(hydration, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "hydrated_runtime_rows.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in hydrated_rows) + "\n",
        encoding="utf-8",
    )

    if hydration["hydration_missing"] or not hydration["eligible_questions"]:
        return _write_blocked_report(hydration, "hydration completeness gate failed")

    eligible_ids = set(hydration["eligible_questions"])
    funnel = [
        row
        for row in load_jsonl(FUNNEL)
        if str(row.get("question_id")) in eligible_ids
    ]
    judge = build_current_llm_judge()
    if judge is None:
        return _write_blocked_report(hydration, "current LLM is not configured")

    replay = run_replay(
        judge=judge,
        rows_override=hydrated_rows,
        funnel_override=funnel,
        output_dir=OUTPUT,
    )
    if replay["experiment_status"] == "blocked":
        final_status = "BLOCKED"
    else:
        final_status = "PASS" if replay["pass"] else "FAIL"
    semantic_metrics = replay["semantic_judge"]
    oracle_metrics = replay["oracle_upper_bound"]
    diff_rows = load_jsonl(OUTPUT / "semantic_judge_diff.jsonl")
    judgement_counts: dict[str, int] = {}
    error_counts: dict[str, int] = {}
    for row in diff_rows:
        judgement = str(row.get("semantic_judgement") or "")
        judgement_counts[judgement] = judgement_counts.get(judgement, 0) + 1
        if row.get("judge_error"):
            error = str(row["judge_error"])
            error_counts[error] = error_counts.get(error, 0) + 1
    semantic_judge_error_analysis = {
        "judge_status_counts": replay.get("judge_status_counts", {}),
        "pair_judgement_counts": judgement_counts,
        "pair_error_counts": error_counts,
        "invalid_judge_response_policy": "fail_closed_as_uncertain",
    }
    report = {
        "phase": "12B-3A-R1",
        "status": final_status,
        "replay_experiment_status": replay["experiment_status"],
        "hydration": hydration,
        "semantic_judge_llm_call_count": replay["llm_call_count"],
        "judge_status_counts": replay.get("judge_status_counts", {}),
        "valid_judgement_count": replay["valid_judgement_count"],
        "semantic_judge_error_analysis": semantic_judge_error_analysis,
        "eligible_subset": {
            "eligible_question_count": len(eligible_ids),
            "ineligible_question_count": len(hydration["ineligible_questions"]),
            "eligible_claim_evidence_pairs": hydration["eligible_claim_evidence_pairs"],
            "formal_comparison_allowed": True,
            "question_ids": sorted(eligible_ids),
        },
        "eligible_baseline": replay["baseline"],
        "eligible_runtime_lexical": replay["runtime_lexical"],
        "eligible_semantic_judge": semantic_metrics,
        "eligible_oracle_upper_bound": oracle_metrics,
        "semantic_to_oracle_gap": oracle_metrics["citation_precision"]["value"]
        - semantic_metrics["citation_precision"]["value"],
        "guardrails": replay["guardrails"],
        "success_standard": replay["success_standard"],
        "top_case_review": "evaluation/phase12b3a_r1/top_case_review.json",
        "evaluation_label_usage": "scoring only after judge output",
        "retrieval_executed": False,
        "candidate_set_changed": False,
    }
    (OUTPUT / "r1_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_report(report)
    return report


def _write_report(report: Mapping[str, Any]) -> None:
    hydration = report["hydration"]
    lines = [
        "# Phase 12B-3A-R1 Runtime Evidence Hydration & Semantic Replay",
        "",
        f"## Status: {report['status']}",
        "",
        "本阶段只对已有 Runtime candidate chunk 做 exact-ID hydration，没有重新 Retrieval、Rerank、Context assembly 或 Generation。",
        "",
        "## Hydration",
        "",
        f"- 来源：`{hydration['hydration_source']}`",
        f"- 总问题数：{hydration['total_questions']}",
        f"- 有 candidate matrix：{hydration['questions_with_candidate_matrix']}",
        f"- candidate evidence 数：{hydration['candidate_evidence_count']}",
        f"- hydration 成功：{hydration['evidence_hydrated_successfully']}",
        f"- hydration missing：{hydration['hydration_missing']}",
        f"- hydration truncated：{hydration['hydration_truncated']}",
        f"- eligible claim-evidence pairs：{hydration['eligible_claim_evidence_pairs']}",
        "",
        "## Semantic Judge",
        "",
        f"- LLM 调用次数：{report['semantic_judge_llm_call_count']}",
        f"- 回放状态：`{report['replay_experiment_status']}`",
        f"- Judge 状态计数：`{json.dumps(report['judge_status_counts'], ensure_ascii=False)}`",
        f"- 有效完整 Judge 批次：{report['valid_judgement_count']}",
        f"- Semantic Judge 错误分析：`{json.dumps(report['semantic_judge_error_analysis'], ensure_ascii=False)}`",
        "- Judge Prompt 和 Semantic Judge 算法未修改。",
        "- Evaluation labels 仅在 Judge 输出之后用于评分。",
        "",
        "## Eligible subset metrics",
        "",
        "| Version | Citation Precision | Supporting Recall | Question Citation Accuracy | Expected Coverage |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, key in (
        ("Baseline", "eligible_baseline"),
        ("Runtime Lexical", "eligible_runtime_lexical"),
        ("Semantic Judge", "eligible_semantic_judge"),
        ("Oracle Upper Bound", "eligible_oracle_upper_bound"),
    ):
        metrics = report[key]
        lines.append(
            f"| {label} | {metrics['citation_precision']['value']:.4f} | "
            f"{metrics['supporting_recall']['value']:.4f} | "
            f"{metrics['question_citation_accuracy']['value']:.4f} | "
            f"{metrics['expected_coverage']['value']:.4f} |"
        )
    lines.extend(
        [
            "",
            f"Semantic → Oracle Citation Precision gap：{report['semantic_to_oracle_gap']:.4f}",
            "",
            "## Non-target verification",
            "",
            "- 未修改线上 Runtime API、Citation Selector、Grounding、Retrieval、Context、Generation、Refusal、Rerank 或 Prompt。",
            "- 未读取 Validation、Holdout、Golden citation label 或 Oracle diff 作为 Judge 输入。",
            "- 不自动进入 Runtime Integration。",
        ]
    )
    (OUTPUT / "phase-12b3a-r1-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    report = run_r1()
    print(json.dumps({"status": report["status"], "llm_calls": report["semantic_judge_llm_call_count"]}, ensure_ascii=False))
    return {"PASS": 0, "FAIL": 2, "BLOCKED": 3}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())

"""Ragas experiment orchestration and artifact/report builders."""

from __future__ import annotations

import asyncio
import json
import statistics
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from ragas import Dataset, experiment
from ragas.backends.local_jsonl import LocalJSONLBackend

from .conversation_e2e_adapter import run_case
from .conversation_e2e_contracts import DatasetFingerprint, JudgeConfig
from .conversation_e2e_metrics import aggregate_arm, paired_case_counts, score_case
from .conversation_e2e_semantic import score_semantic_rows

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = PROJECT_ROOT / "evaluation/ragas/experiments"


def build_blocked_report(fingerprint: DatasetFingerprint, reason_code: str, reason: str) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "reason_code": reason_code,
        "reason": reason,
        "dataset_fingerprint": fingerprint.to_dict(),
        "case_count": fingerprint.case_count,
        "development_only_guard": True,
        "metrics_available": False,
    }


def _semantic_summary(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    values = [row[arm][name] for row in rows for arm in ("baseline", "candidate") if row.get(arm, {}).get(name) is not None]
    by_arm = {
        arm: [row[arm][name] for row in rows if row.get(arm, {}).get(name) is not None]
        for arm in ("baseline", "candidate")
    }
    return {
        "mean": statistics.fmean(values) if values else None,
        "median": statistics.median(values) if values else None,
        "baseline_mean": statistics.fmean(by_arm["baseline"]) if by_arm["baseline"] else None,
        "candidate_mean": statistics.fmean(by_arm["candidate"]) if by_arm["candidate"] else None,
        "case_level_delta": [
            {"case_id": row["case_id"], "delta": row["candidate"].get(name) - row["baseline"].get(name)}
            for row in rows
            if row.get("candidate", {}).get(name) is not None and row.get("baseline", {}).get(name) is not None
        ],
    }


def build_report(
    *,
    cases: list[dict[str, Any]],
    fingerprint: DatasetFingerprint,
    runtime_fingerprint: dict[str, Any],
    judge_config: JudgeConfig,
    semantic_rows: list[dict[str, Any]],
    experiment_artifact: str,
    semantic_blocked_reason: str | None = None,
) -> dict[str, Any]:
    scored = [score_case(case) for case in cases]
    baseline = aggregate_arm(scored, "baseline")
    candidate = aggregate_arm(scored, "candidate")
    semantic = {
        "faithfulness": _semantic_summary(semantic_rows, "faithfulness"),
        "response_relevancy": _semantic_summary(semantic_rows, "response_relevancy"),
    }
    judge_errors = sum(
        bool(row.get(arm, {}).get("judge_error"))
        for row in semantic_rows
        for arm in ("baseline", "candidate")
    ) + int(bool(semantic_blocked_reason))
    return {
        "status": "BLOCKED" if judge_errors or semantic_blocked_reason else "R3_PASS",
        "case_count": len(cases),
        "dataset_fingerprint": {**fingerprint.to_dict(), "case_ids": [case["case_id"] for case in cases]},
        "runtime_config_fingerprint": runtime_fingerprint,
        "judge_config": judge_config.to_dict(),
        "ragas_version": "0.3.9",
        "baseline": baseline,
        "candidate": candidate,
        "semantic": semantic,
        "semantic_execution": {
            "status": "BLOCKED" if semantic_blocked_reason else "READY",
            "reason": semantic_blocked_reason,
            "formal_case_scoring_executed": not bool(semantic_blocked_reason),
        },
        "judge_errors": judge_errors,
        "paired_case_counts": paired_case_counts(scored),
        "failure_layer_distribution": dict(Counter(layer for case in cases if (layer := case.get("failure_layer")))),
        "experiment_artifact": experiment_artifact,
        "cases": cases,
        "semantic_cases": semantic_rows,
        "created_at": datetime.now(UTC).isoformat(),
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    status = report.get("status", "BLOCKED")
    lines = [
        "# Phase 10 Conversation E2E Ragas Development Report",
        "",
        f"Status: **{status}**",
        f"Ragas: `{report.get('ragas_version', '0.3.9')}`",
        f"Cases: `{report.get('case_count', 0)}`",
        "",
        "## Dataset and judge",
        "",
        f"- Dataset fingerprint: `{report.get('dataset_fingerprint', {})}`",
        f"- Judge config: `{report.get('judge_config', {})}`",
        "",
        "## BASELINE → CANDIDATE",
        "",
    ]
    for name in ("hit_recall_at_5", "mrr_at_5", "supporting_recall", "false_rejection_rate", "question_level_citation_accuracy", "unsupported_answer_rate", "expected_answer_coverage"):
        baseline = report.get("baseline", {}).get(name)
        candidate = report.get("candidate", {}).get(name)
        lines.append(f"- {name}: `{baseline}` → `{candidate}`")
    lines.extend([
        f"- Faithfulness: `{report.get('semantic', {}).get('faithfulness', {})}`",
        f"- Response Relevancy: `{report.get('semantic', {}).get('response_relevancy', {})}`",
        f"- Semantic execution: `{report.get('semantic_execution', {})}`",
        f"- Improved / unchanged / regressed: `{report.get('paired_case_counts', {})}`",
        f"- Judge errors: `{report.get('judge_errors', 0)}`",
        f"- Failure layers: `{report.get('failure_layer_distribution', {})}`",
        "",
        "## Next phase recommendation",
        "",
        "Do not start the next phase from this report; review the paired guardrails and semantic case-level deltas first.",
    ])
    return "\n".join(lines) + "\n"


def write_artifacts(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown_report(report), encoding="utf-8")


class _ExperimentRow(BaseModel):
    case_id: str
    baseline_query: str
    candidate_query: str
    baseline_context_hash: str | None
    candidate_context_hash: str | None


async def run_development_experiment(
    *,
    service: Any,
    cases: list[dict[str, Any]],
    mode: str,
    top_k: int,
    chunk_top_k: int,
    runtime_fingerprint: dict[str, Any],
    dataset_fingerprint: DatasetFingerprint,
    judge_config: JudgeConfig,
    faithfulness: Any | None = None,
    relevancy: Any | None = None,
    semantic_blocked_reason: str | None = None,
) -> dict[str, Any]:
    runtime_rows: dict[str, dict[str, Any]] = {}
    semaphore = asyncio.Semaphore(max(1, judge_config.max_concurrency))

    async def execute(case: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            row = await run_case(
                service,
                case,
                mode=mode,
                top_k=top_k,
                chunk_top_k=chunk_top_k,
            )
            runtime_rows[row["case_id"]] = row
            return row

    await asyncio.gather(*(execute(case) for case in cases))
    semantic_input = [
        {
            "case_id": row["case_id"],
            "standalone_query": row["standalone_query"],
            "baseline": row["baseline"],
            "candidate": row["candidate"],
        }
        for row in (runtime_rows[case["case_id"]] for case in cases)
    ]
    semantic_rows = (
        []
        if semantic_blocked_reason
        else await score_semantic_rows(
            semantic_input,
            judge_config,
            faithfulness=faithfulness,
            relevancy=relevancy,
        )
    )
    experiment_name = "conversation-e2e-development-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    experiment_path = EXPERIMENT_ROOT / "experiments" / f"industrial-energy-{experiment_name}.jsonl"
    experiment_path.parent.mkdir(parents=True, exist_ok=True)
    dataset = Dataset(
        name=experiment_name,
        backend=LocalJSONLBackend(str(EXPERIMENT_ROOT)),
        data=[
            {
                "case_id": case["case_id"],
                "baseline_query": runtime_rows[case["case_id"]]["baseline"]["runtime_query"],
                "candidate_query": runtime_rows[case["case_id"]]["candidate"]["runtime_query"],
                "baseline_context_hash": runtime_rows[case["case_id"]]["baseline"]["provider_context_hash"],
                "candidate_context_hash": runtime_rows[case["case_id"]]["candidate"]["provider_context_hash"],
            }
            for case in cases
        ],
    )

    @experiment(_ExperimentRow, name_prefix="industrial-energy")
    async def persist_row(row: dict[str, Any]) -> _ExperimentRow:
        return _ExperimentRow(**row)

    experiment_view = await persist_row.arun(
        dataset,
        name=experiment_name,
        backend=LocalJSONLBackend(str(EXPERIMENT_ROOT)),
    )
    report = build_report(
        cases=[runtime_rows[case["case_id"]] for case in cases],
        fingerprint=dataset_fingerprint,
        runtime_fingerprint=runtime_fingerprint,
        judge_config=judge_config,
        semantic_rows=semantic_rows,
        experiment_artifact=str(experiment_path.relative_to(PROJECT_ROOT)),
        semantic_blocked_reason=semantic_blocked_reason,
    )
    report["experiment_row_count"] = len(experiment_view)
    report["validation_holdout_accessed"] = False
    report["deterministic_case_metrics"] = [score_case(runtime_rows[case["case_id"]]) for case in cases]
    return report

"""Ragas Development migration runner and strict canonical parity gate."""

from __future__ import annotations

import asyncio
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from ragas import Experiment, experiment
from ragas.backends.local_jsonl import LocalJSONLBackend

from industrial_rag.conversation.retrieval_evaluation import compare_retrieval_metrics
from industrial_rag.conversation.retrieval_evaluation import ranked_chunk_ids
from industrial_rag.lightrag_service import QueryOptions, _extract_retrieved
from industrial_rag.query_normalization import normalize_query
from scripts.evaluate_conversation_retrieval_development import (
    DATASET_PATH,
)

from .conversation_adapter import PROJECT_ROOT, build_ragas_dataset
from .conversation_metrics import score_official_id_metrics, score_retrieval_metric_results

CANONICAL_REPORT_PATH = PROJECT_ROOT / "evaluation/phase10/conversation_retrieval_development_report.json"
MIGRATION_REPORT_PATH = PROJECT_ROOT / "evaluation/phase10/ragas_migration_development_report.json"
EXPERIMENT_ROOT = PROJECT_ROOT / "evaluation/ragas/experiments"
TOLERANCE = 1e-9
METRIC_NAMES = (
    "hit_recall_at_5",
    "evidence_recall_at_5",
    "mrr_at_5",
    "hit_recall_at_10",
    "evidence_recall_at_10",
    "mrr_at_10",
)


class RagasExperimentRow(BaseModel):
    case_id: str
    source_question_id: str
    dependent_query: str
    rewritten_query: str
    retrieved_chunk_ids: list[str]
    reference_context_ids: list[str]
    metrics: dict[str, Any]
    metric_debug: dict[str, Any]
    trace: dict[str, Any]


def _case_ids(report: dict[str, Any]) -> list[str]:
    return [str(row["case_id"]) for row in report.get("cases", [])]


def _classification_sets(report: dict[str, Any]) -> dict[str, list[str]]:
    regressed = [
        row["case_id"] if isinstance(row, dict) else row
        for row in report.get("regressed_cases", [])
    ]
    return {
        "improved": list(report.get("improved_cases", [])),
        "unchanged": list(report.get("unchanged_cases", [])),
        "regressed": regressed,
    }


def compare_with_canonical(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    if old.get("dataset", {}).get("case_count") != new.get("case_count"):
        mismatches.append({"kind": "case_count", "old": old.get("dataset", {}).get("case_count"), "new": new.get("case_count")})
    old_ids = _case_ids(old)
    if old_ids != new.get("case_ids"):
        mismatches.append({"kind": "case_ids", "old": old_ids, "new": new.get("case_ids")})
    for phase in ("before", "after"):
        for metric in METRIC_NAMES:
            old_value = old.get(phase, {}).get(metric)
            new_value = new.get(phase, {}).get(metric)
            delta = None if old_value is None or new_value is None else abs(float(new_value) - float(old_value))
            if delta is None or delta > TOLERANCE:
                mismatches.append({"kind": "metric", "phase": phase, "metric": metric, "old": old_value, "new": new_value, "absolute_delta": delta})
    old_sets = _classification_sets(old)
    new_sets = {key: list(new.get(f"{key}_cases", [])) for key in old_sets}
    if old_sets != new_sets:
        mismatches.append({"kind": "case_classification", "old": old_sets, "new": new_sets})
    return {
        "passed": not mismatches,
        "tolerance": TOLERANCE,
        "denominator_parity": old.get("dataset", {}).get("case_count") == new.get("case_count"),
        "case_classification_parity": old_sets == new_sets,
        "metric_deltas": {
            phase: {
                metric: float(new[phase][metric]) - float(old[phase][metric])
                for metric in METRIC_NAMES
            }
            for phase in ("before", "after")
        },
        "mismatches": mismatches,
    }


def build_blocked_report(*, reason_code: str, reason: str, case_count: int, fingerprint: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "reason_code": reason_code,
        "reason": reason,
        "dataset": {"case_count": case_count, "development_only_guard": True},
        "dataset_fingerprint": fingerprint,
        "metrics_available": False,
    }


def no_metrics_in_blocked_report(report: dict[str, Any]) -> bool:
    return report.get("status") == "BLOCKED" and not any(key in report for key in ("before", "after", "delta"))


def _baseline_source_sha256(baseline_head: str) -> str | None:
    try:
        content = subprocess.check_output(
            ["git", "show", f"{baseline_head}:data/evaluation/conversation_retrieval_development.jsonl"],
            cwd=PROJECT_ROOT,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    import hashlib

    return hashlib.sha256(content).hexdigest()


def _experiment_name() -> str:
    return "conversation-retrieval-development-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def run_ragas_experiment(
    backend: Any,
    *,
    cases: list[dict[str, Any]],
    config: QueryOptions,
    fingerprint: dict[str, Any],
    baseline_head: str,
) -> dict[str, Any]:
    bundle = build_ragas_dataset(DATASET_PATH)
    old = json.loads(CANONICAL_REPORT_PATH.read_text(encoding="utf-8"))
    if old.get("status") != "READY":
        raise RuntimeError("canonical Conversation Retrieval Development artifact is not READY")
    experiment_name = _experiment_name()
    rows_by_case: dict[str, dict[str, Any]] = {}

    @experiment(RagasExperimentRow, name_prefix="industrial-energy")
    async def retrieval_row(row: dict[str, Any]) -> dict[str, Any]:
        case = next(item for item in cases if item["case_id"] == row["case_id"])
        started = asyncio.get_running_loop().time()
        from industrial_rag.conversation.query_rewriter import QueryRewriter

        rewrite = await QueryRewriter().rewrite(case["dependent_query"], case["history"])
        if rewrite.status != "rewritten":
            raise ValueError(f"rewrite failed for {case['case_id']}: {rewrite.status}")
        rewritten = normalize_query(rewrite.standalone_query or "").normalized_query
        expected = normalize_query(case["expected_standalone_query"]).normalized_query
        if rewritten != expected:
            raise ValueError(f"rewrite gold mismatch for {case['case_id']}")
        before_query = normalize_query(case["dependent_query"]).normalized_query
        before_raw = _extract_retrieved(await backend.aquery_data(before_query, config))
        after_raw = _extract_retrieved(await backend.aquery_data(rewritten, config))
        comparison = compare_retrieval_metrics(before_raw, after_raw, case["gold_chunk_ids"])
        after_ids = list(ranked_chunk_ids(after_raw))
        before_ids = list(ranked_chunk_ids(before_raw))
        before_custom = score_retrieval_metric_results(before_ids, case["gold_chunk_ids"])
        custom = score_retrieval_metric_results(after_ids, case["gold_chunk_ids"])
        official = {
            f"at_{k}": await score_official_id_metrics(after_ids, case["gold_chunk_ids"], k)
            for k in (5, 10)
        }
        latency_ms = (asyncio.get_running_loop().time() - started) * 1000
        row_result = {
            "case_id": case["case_id"],
            "source_question_id": case["source_question_id"],
            "category": case["category"],
            "dependent_query": case["dependent_query"],
            "rewritten_query": rewrite.standalone_query,
            "gold_chunk_ids": case["gold_chunk_ids"],
            "before_query": before_query,
            "after_query": rewritten,
            "rewrite_status": rewrite.status,
            "before_ranks": comparison["before_ranks"],
            "after_ranks": comparison["after_ranks"],
            "before": comparison["before"],
            "after": comparison["after"],
            "delta": comparison["delta"],
            "improved": comparison["improved"],
            "regressed": comparison["regressed"],
            "unchanged": comparison["unchanged"],
            "ragas_metrics": {name: result.value for name, result in custom.items()},
            "ragas_before_metrics": {name: result.value for name, result in before_custom.items()},
            "ragas_before_metric_debug": {
                name: {**result.to_dict(), "traces": result.traces}
                for name, result in before_custom.items()
            },
            "ragas_metric_debug": {
                name: {**result.to_dict(), "traces": result.traces}
                for name, result in custom.items()
            },
            "official_id_metrics": official,
            "trace": {
                "case_id": case["case_id"],
                "input_query": before_query,
                "rewritten_query": rewritten,
                "retrieved_chunk_ids": after_ids,
                "retrieved_ranks": {str(index): chunk_id for index, chunk_id in enumerate(after_ids, start=1)},
                "gold_ids": list(case["gold_chunk_ids"]),
                "first_gold_rank": min(comparison["after_ranks"].values(), default=None),
                "latency_ms": latency_ms,
                "rewrite_metadata": {
                    "status": rewrite.status,
                    "reason": rewrite.rewrite_reason,
                    "history_message_count": rewrite.history_message_count,
                    "history_used": rewrite.history_used,
                },
            },
        }
        rows_by_case[case["case_id"]] = row_result
        return RagasExperimentRow(
            case_id=case["case_id"],
            source_question_id=case["source_question_id"],
            dependent_query=case["dependent_query"],
            rewritten_query=rewritten,
            retrieved_chunk_ids=after_ids,
            reference_context_ids=list(case["gold_chunk_ids"]),
            metrics={
                "before": {name: float(result.value) for name, result in before_custom.items()},
                "after": {name: float(result.value) for name, result in custom.items()},
            },
            metric_debug={
                name: {**result.to_dict(), "traces": result.traces}
                for name, result in custom.items()
            },
            trace=row_result["trace"],
        )

    experiment_view: Experiment = await retrieval_row.arun(
        bundle.dataset,
        name=experiment_name,
        backend=LocalJSONLBackend(str(EXPERIMENT_ROOT.parent)),
    )
    if len(experiment_view) != len(cases):
        raise RuntimeError(
            f"Ragas experiment row count mismatch: expected {len(cases)}, got {len(experiment_view)}"
        )
    ordered_rows = [rows_by_case[case["case_id"]] for case in cases]
    before_metrics = {
        name: sum(row["ragas_before_metrics"][name] for row in ordered_rows) / len(ordered_rows)
        for name in METRIC_NAMES
    }
    after_metrics = {
        name: sum(row["ragas_metrics"][name] for row in ordered_rows) / len(ordered_rows)
        for name in METRIC_NAMES
    }
    new = {
        "case_count": len(ordered_rows),
        "case_ids": [row["case_id"] for row in ordered_rows],
        "before": before_metrics,
        "after": after_metrics,
        "improved_cases": [row["case_id"] for row in ordered_rows if row["improved"]],
        "unchanged_cases": [row["case_id"] for row in ordered_rows if row["unchanged"]],
        "regressed_cases": [row["case_id"] for row in ordered_rows if row["regressed"]],
    }
    parity = compare_with_canonical(old, new)
    return {
        "status": "MIGRATION_PASS" if parity["passed"] else "MIGRATION_FAIL",
        "baseline_head": baseline_head,
        "ragas_version": "0.3.9",
        "dataset_fingerprint": {
            **bundle.fingerprint,
            "baseline_raw_sha256": _baseline_source_sha256(baseline_head),
            "matches_baseline_raw_sha256": bundle.fingerprint["raw_sha256"] == _baseline_source_sha256(baseline_head),
        },
        "case_count": len(ordered_rows),
        "canonical_artifact": str(CANONICAL_REPORT_PATH.relative_to(PROJECT_ROOT)),
        "experiment_artifact": str((EXPERIMENT_ROOT / f"{experiment_name}.jsonl").relative_to(PROJECT_ROOT)),
        "python_version": platform.python_version(),
        "dependency_management": {
            "pyproject_optional_dependency": "evaluation",
            "requirements_file": "requirements-evaluation.txt",
            "environment_file": "environment.yml",
        },
        "old_metrics": {"before": old["before"], "after": old["after"]},
        "ragas_metrics": {"before": before_metrics, "after": after_metrics},
        "parity": parity,
        "improved_cases": new["improved_cases"],
        "unchanged_cases": new["unchanged_cases"],
        "regressed_cases": new["regressed_cases"],
        "cases": ordered_rows,
        "experiment_row_count": len(experiment_view),
        "no_llm_semantic_metrics": True,
        "validation_holdout_accessed": False,
        "frozen_components": [
            "scripts/evaluate_conversation_retrieval_development.py (legacy/frozen reader)",
            "evaluation/phase10/conversation_retrieval_development_report.json (historical artifact)",
            "src/industrial_rag/conversation/retrieval_evaluation.py (canonical metric definitions)",
            "data/evaluation/conversation_retrieval_development.jsonl (input dataset)",
        ],
    }


def write_report(report: dict[str, Any], path: Path = MIGRATION_REPORT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_blocked_from_exception(error: Exception, bundle: Any) -> dict[str, Any]:
    return build_blocked_report(
        reason_code="ragas_runtime_unavailable",
        reason=f"{type(error).__name__}: {error}",
        case_count=len(bundle.cases),
        fingerprint=bundle.fingerprint,
    )

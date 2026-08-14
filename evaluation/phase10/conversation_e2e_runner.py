"""Ragas experiment orchestration and artifact/report builders."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import statistics
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
from ragas import Dataset, experiment
from ragas.backends.local_jsonl import LocalJSONLBackend

from .conversation_e2e_adapter import run_case
from .conversation_e2e_contracts import DatasetFingerprint, JudgeConfig
from .conversation_e2e_metrics import aggregate_arm, evaluate_gate, paired_case_counts, score_case
from .conversation_e2e_semantic import score_semantic_rows

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = PROJECT_ROOT / "evaluation/ragas/experiments"
SNAPSHOT_SCHEMA_VERSION = 1


class SnapshotValidationError(ValueError):
    """A frozen runtime snapshot cannot safely be used for semantic scoring."""

    def __init__(self, reason_code: str, reason: str) -> None:
        super().__init__(reason)
        self.reason_code = reason_code


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_runtime_snapshot(
    cases: list[dict[str, Any]],
    fingerprint: DatasetFingerprint,
    runtime_fingerprint: dict[str, Any],
) -> dict[str, Any]:
    """Build the Development-only runtime checkpoint before semantic judging."""

    snapshot_sha256 = hashlib.sha256(_canonical_json_bytes(cases)).hexdigest()
    return {
        "manifest": {
            "record_type": "manifest",
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "snapshot_sha256": snapshot_sha256,
            "dataset_fingerprint": fingerprint.to_dict(),
            "runtime_config_fingerprint": runtime_fingerprint,
            "case_count": len(cases),
            "ordered_case_ids": [str(case["case_id"]) for case in cases],
            "created_at": datetime.now(UTC).isoformat(),
        },
        "cases": cases,
    }


def build_runtime_snapshot_from_report(
    report: dict[str, Any],
    fingerprint: DatasetFingerprint,
    runtime_fingerprint: dict[str, Any],
) -> dict[str, Any]:
    """Backfill a checkpoint only from a provenance-matching real runtime report."""

    if report.get("dataset_fingerprint") != fingerprint.to_dict():
        raise SnapshotValidationError("legacy_report_dataset_fingerprint_mismatch", "existing report dataset fingerprint does not match")
    if report.get("runtime_config_fingerprint") != runtime_fingerprint:
        raise SnapshotValidationError("legacy_report_runtime_fingerprint_mismatch", "existing report runtime fingerprint does not match")
    cases = report.get("cases")
    if not isinstance(cases, list) or report.get("case_count") != len(cases) or len(cases) != fingerprint.case_count:
        raise SnapshotValidationError("legacy_report_case_count_mismatch", "existing report does not contain the frozen Development runtime cases")
    snapshot = build_runtime_snapshot(cases, fingerprint, runtime_fingerprint)
    snapshot["manifest"]["backfilled_from_existing_report"] = True
    return snapshot


def write_runtime_snapshot(snapshot: dict[str, Any], path: Path) -> None:
    """Persist a snapshot as a single atomically replaced JSONL artifact."""

    lines = [json.dumps(snapshot["manifest"], ensure_ascii=False)]
    lines.extend(json.dumps({"record_type": "case", "case": case}, ensure_ascii=False) for case in snapshot["cases"])
    atomic_write_text(path, "\n".join(lines) + "\n")


def _validate_snapshot_case(case: dict[str, Any]) -> None:
    for field in ("case_id", "history", "dependent_query", "standalone_query", "gold_chunk_ids", "rewrite"):
        if field not in case:
            raise SnapshotValidationError("snapshot_case_incomplete", f"snapshot case is missing {field}")
    required_arm_fields = (
        "runtime_query", "retrieved_chunk_ids", "retrieved_ranks", "selected_evidence_ids",
        "provider_evidence_ids", "provider_context_ids", "provider_context_hash", "provider_contexts",
        "evaluation_user_input", "answer", "answer_status", "citations", "answer_points", "grounding_removed_points",
        "grounding_failure_categories", "latency_ms", "metric_error",
    )
    for arm_name in ("baseline", "candidate"):
        arm = case.get(arm_name)
        if not isinstance(arm, dict):
            raise SnapshotValidationError("snapshot_case_incomplete", f"snapshot case is missing {arm_name}")
        missing = [field for field in required_arm_fields if field not in arm]
        if missing:
            raise SnapshotValidationError("snapshot_case_incomplete", f"snapshot {arm_name} is missing {', '.join(missing)}")
        contexts = arm["provider_contexts"]
        if not isinstance(contexts, list) or not contexts or not all(isinstance(item, str) and item.strip() for item in contexts):
            raise SnapshotValidationError(
                "snapshot_missing_provider_contexts",
                f"snapshot {arm_name} has no actual provider context text for {case['case_id']}",
            )


def load_runtime_snapshot(
    path: Path,
    fingerprint: DatasetFingerprint,
    runtime_fingerprint: dict[str, Any],
    *,
    expected_snapshot_sha256: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read and validate a snapshot without touching LightRAGService."""

    try:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as error:
        raise SnapshotValidationError("snapshot_unreadable", f"could not read runtime snapshot: {type(error).__name__}: {error}") from error
    if not records or records[0].get("record_type") != "manifest":
        raise SnapshotValidationError("snapshot_manifest_missing", "runtime snapshot has no manifest")
    manifest = records[0]
    cases = [record.get("case") for record in records[1:] if record.get("record_type") == "case"]
    if len(cases) != len(records) - 1 or not all(isinstance(case, dict) for case in cases):
        raise SnapshotValidationError("snapshot_case_incomplete", "runtime snapshot contains invalid case records")
    if manifest.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise SnapshotValidationError("snapshot_schema_mismatch", "runtime snapshot schema version is unsupported")
    if manifest.get("dataset_fingerprint") != fingerprint.to_dict():
        raise SnapshotValidationError("snapshot_dataset_fingerprint_mismatch", "runtime snapshot dataset fingerprint does not match")
    if manifest.get("runtime_config_fingerprint") != runtime_fingerprint:
        raise SnapshotValidationError("snapshot_runtime_fingerprint_mismatch", "runtime snapshot runtime fingerprint does not match")
    case_ids = [str(case.get("case_id")) for case in cases]
    if manifest.get("case_count") != fingerprint.case_count or len(cases) != fingerprint.case_count:
        raise SnapshotValidationError("snapshot_case_count_mismatch", "runtime snapshot case count does not match frozen Development data")
    if manifest.get("ordered_case_ids") != list(fingerprint.case_ids) or case_ids != list(fingerprint.case_ids):
        raise SnapshotValidationError("snapshot_case_order_mismatch", "runtime snapshot case order does not match frozen Development data")
    actual_sha256 = hashlib.sha256(_canonical_json_bytes(cases)).hexdigest()
    if manifest.get("snapshot_sha256") != actual_sha256:
        raise SnapshotValidationError("snapshot_checksum_mismatch", "runtime snapshot checksum does not match its case payload")
    if expected_snapshot_sha256 is not None and actual_sha256 != expected_snapshot_sha256:
        raise SnapshotValidationError("snapshot_checksum_mismatch", "runtime snapshot SHA does not match the frozen canonical SHA")
    for case in cases:
        _validate_snapshot_case(case)
    return cases, manifest


def build_blocked_report(
    fingerprint: DatasetFingerprint,
    reason_code: str,
    reason: str,
    *,
    runtime_fingerprint: dict[str, Any] | None = None,
    judge_config: JudgeConfig | None = None,
    case_count: int | None = None,
    judge_errors: int = 0,
) -> dict[str, Any]:
    """Return a complete, machine-readable BLOCKED artifact payload."""

    return {
        "status": "BLOCKED",
        "reason_code": reason_code,
        "reason": reason,
        "dataset_fingerprint": fingerprint.to_dict(),
        "case_count": fingerprint.case_count if case_count is None else case_count,
        "runtime_config_fingerprint": runtime_fingerprint,
        "judge_config": judge_config.to_dict() if judge_config is not None else None,
        "ragas_version": "0.3.9",
        "semantic_execution": {
            "status": "BLOCKED",
            "reason_code": reason_code,
            "reason": reason,
            "formal_case_scoring_executed": False,
        },
        "judge_errors": judge_errors,
        "semantic": {
            "faithfulness": {"status": "unavailable", "reason": reason},
            "response_relevancy": {"status": "unavailable", "reason": reason},
        },
        "created_at": datetime.now(UTC).isoformat(),
        "development_only_guard": True,
        "metrics_available": False,
    }


def _semantic_summary(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    values = [row[arm][name] for row in rows for arm in ("baseline", "candidate") if row.get(arm, {}).get(name) is not None]
    by_arm = {
        arm: [row[arm][name] for row in rows if row.get(arm, {}).get(name) is not None]
        for arm in ("baseline", "candidate")
    }
    case_level_delta = [
            {"case_id": row["case_id"], "delta": row["candidate"].get(name) - row["baseline"].get(name)}
            for row in rows
            if row.get("candidate", {}).get(name) is not None and row.get("baseline", {}).get(name) is not None
    ]
    baseline_mean = statistics.fmean(by_arm["baseline"]) if by_arm["baseline"] else None
    candidate_mean = statistics.fmean(by_arm["candidate"]) if by_arm["candidate"] else None
    delta = candidate_mean - baseline_mean if baseline_mean is not None and candidate_mean is not None else None
    return {
        "status": "available" if values else "unavailable",
        "mean": statistics.fmean(values) if values else None,
        "median": statistics.median(values) if values else None,
        "baseline_mean": baseline_mean,
        "candidate_mean": candidate_mean,
        "baseline_median": statistics.median(by_arm["baseline"]) if by_arm["baseline"] else None,
        "candidate_median": statistics.median(by_arm["candidate"]) if by_arm["candidate"] else None,
        "delta": delta,
        "interpretation": "NO_CLEAR_SEMANTIC_CHANGE" if delta is not None and abs(delta) < 0.01 else None,
        "case_level_delta": case_level_delta,
        "largest_improvements": sorted(case_level_delta, key=lambda row: row["delta"], reverse=True)[:5],
        "largest_regressions": sorted(case_level_delta, key=lambda row: row["delta"])[:5],
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
    semantic_execution = {
        "status": "BLOCKED" if judge_errors or semantic_blocked_reason else "READY",
        "reason": semantic_blocked_reason,
        "formal_case_scoring_executed": bool(semantic_rows),
    }
    gate = evaluate_gate({
        "baseline": baseline,
        "candidate": candidate,
        "semantic": semantic,
        "semantic_execution": semantic_execution,
        "judge_errors": judge_errors,
    })
    report = {
        "status": gate["status"],
        "case_count": len(cases),
        "dataset_fingerprint": {**fingerprint.to_dict(), "case_ids": [case["case_id"] for case in cases]},
        "runtime_config_fingerprint": runtime_fingerprint,
        "judge_config": judge_config.to_dict(),
        "ragas_version": "0.3.9",
        "baseline": baseline,
        "candidate": candidate,
        "semantic": semantic,
        "semantic_execution": semantic_execution,
        "judge_errors": judge_errors,
        "gate": gate,
        "paired_case_counts": paired_case_counts(scored),
        "failure_layer_distribution": dict(Counter(layer for case in cases if (layer := case.get("failure_layer")))),
        "experiment_artifact": experiment_artifact,
        "cases": cases,
        "semantic_cases": semantic_rows,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if gate["status"] == "BLOCKED":
        report["reason_code"] = "semantic_execution_blocked"
        report["reason"] = "; ".join(gate["reasons"]) or "semantic execution could not complete"
    return report


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
        f"- LightRAGService query calls: `{report.get('light_rag_service_call_count')}`",
        f"- Semantic metrics: `{report.get('semantic_metrics', {})}`",
        f"- Diagnostics: `{report.get('diagnostics', {})}`",
        f"- Semantic scores artifact: `{report.get('semantic_scores_artifact')}`",
        f"- Gate: `{report.get('gate', {})}`",
        "- Unsupported Answer Rate is question-level: a case is unsupported when any answer point has `support_status == unsupported`.",
        "",
        "## Recovery provenance",
        "",
        f"- Previous blocked commit: `{report.get('previous_blocked_commit')}`",
        f"- Implementation audit: `{report.get('implementation_audit', {})}`",
        f"- Runtime snapshot: `{report.get('runtime_snapshot', {})}`",
        f"- Semantic preflight: `{report.get('semantic_preflight', {})}`",
        "",
        "## Next phase recommendation",
        "",
        "Do not start the next phase from this report; review the paired guardrails and semantic case-level deltas first.",
    ])
    return "\n".join(lines) + "\n"


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace an artifact only after its complete contents are durable."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_artifacts(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    atomic_write_text(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(markdown_path, render_markdown_report(report))


def write_semantic_scores(rows: list[dict[str, Any]], path: Path) -> None:
    """Atomically persist one semantic result record per frozen case."""

    atomic_write_text(path, "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n")


def load_semantic_scores(path: Path) -> list[dict[str, Any]]:
    """Load a completed semantic checkpoint without contacting any provider."""

    if not path.exists():
        return []
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as error:
        raise SnapshotValidationError("semantic_scores_unreadable", f"could not read semantic score checkpoint: {error}") from error
    if not all(isinstance(row, dict) and row.get("case_id") for row in rows):
        raise SnapshotValidationError("semantic_scores_invalid", "semantic score checkpoint contains an invalid row")
    if len({str(row["case_id"]) for row in rows}) != len(rows):
        raise SnapshotValidationError("semantic_scores_duplicate_case", "semantic score checkpoint contains duplicate case ids")
    return rows


class _ExperimentRow(BaseModel):
    case_id: str
    baseline_query: str
    candidate_query: str
    baseline_context_hash: str | None
    candidate_context_hash: str | None


async def resolve_runtime_cases(
    service: Any,
    cases: list[dict[str, Any]],
    *,
    mode: str,
    top_k: int,
    chunk_top_k: int,
    frozen_cases: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Use a validated checkpoint verbatim, or execute the paired runtime once."""

    if frozen_cases is not None:
        expected_ids = [str(case["case_id"]) for case in cases]
        actual_ids = [str(case["case_id"]) for case in frozen_cases]
        if actual_ids != expected_ids:
            raise SnapshotValidationError("snapshot_case_order_mismatch", "frozen runtime cases do not match the requested dataset")
        return frozen_cases
    semaphore = asyncio.Semaphore(1)

    async def execute(case: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await run_case(
                service,
                case,
                mode=mode,
                top_k=top_k,
                chunk_top_k=chunk_top_k,
            )

    return list(await asyncio.gather(*(execute(case) for case in cases)))


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
    frozen_cases: list[dict[str, Any]] | None = None,
    enabled_metrics: tuple[str, ...] = ("faithfulness", "response_relevancy"),
    semantic_row_callback: Any | None = None,
) -> dict[str, Any]:
    resolved_cases = await resolve_runtime_cases(
        service,
        cases,
        mode=mode,
        top_k=top_k,
        chunk_top_k=chunk_top_k,
        frozen_cases=frozen_cases,
    )
    runtime_rows = {str(row["case_id"]): row for row in resolved_cases}
    semantic_input = [
        {
            "case_id": row["case_id"],
            "standalone_query": row["standalone_query"],
            "baseline": row["baseline"],
            "candidate": row["candidate"],
        }
        for row in (runtime_rows[case["case_id"]] for case in cases)
    ]
    semantic_rows = await score_semantic_rows(
        semantic_input,
        judge_config,
        faithfulness=faithfulness,
        relevancy=relevancy,
        enabled_metrics=enabled_metrics,
        on_row=semantic_row_callback,
    ) if enabled_metrics else []
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

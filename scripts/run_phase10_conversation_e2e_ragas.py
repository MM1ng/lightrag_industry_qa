"""Run the frozen Development conversation E2E Ragas experiment once."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

DATASET = PROJECT_ROOT / "data/evaluation/conversation_retrieval_development.jsonl"
REPORT_JSON = PROJECT_ROOT / "evaluation/phase10/conversation_e2e_ragas_development_report.json"
REPORT_MD = PROJECT_ROOT / "docs/phase-10-conversation-e2e-ragas-development-report.md"
RUNTIME_SNAPSHOT = PROJECT_ROOT / "evaluation/phase10/conversation_e2e_runtime_snapshot_development.jsonl"
SEMANTIC_SCORES = PROJECT_ROOT / "evaluation/phase10/conversation_e2e_semantic_scores_development.jsonl"
EXPECTED_SNAPSHOT_SHA256 = "8d551a2f02e4141cf0d355c6271a17883617a0519a7b1f80534496784cec0cde"
PREVIOUS_BLOCKED_COMMIT = "dbaf649e6fd59f710def1e99aa46a93cc514484f"
JUDGE_MODEL = "qwen-plus-2025-07-28"
EMBEDDING_MODEL = "text-embedding-v4"


def console_summary(report: dict[str, object]) -> str:
    """Keep CLI output readable on Windows consoles that still use GBK."""

    return json.dumps(
        {
            "status": report.get("status"),
            "case_count": report.get("case_count"),
            "judge_errors": report.get("judge_errors"),
            "report": str(REPORT_JSON.relative_to(PROJECT_ROOT)),
        },
        ensure_ascii=True,
    )


def semantic_preflight_block_reason(preflight: dict[str, object]) -> str:
    """Keep all independently diagnosed preflight failures in the BLOCKED report."""

    components = preflight.get("components", {})
    if not isinstance(components, dict):
        return "semantic preflight returned no component diagnostics"
    reasons = []
    for component in components.values():
        if isinstance(component, dict) and component.get("status") == "BLOCKED":
            reasons.append(f"{component.get('reason_code', 'semantic_preflight_error')}: {component.get('reason', '')}")
    return "; ".join(reasons) or "semantic preflight blocked"


def _semantic_input(runtime_cases: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "case_id": row["case_id"],
            "standalone_query": row["standalone_query"],
            "baseline": row["baseline"],
            "candidate": row["candidate"],
        }
        for row in runtime_cases
    ]


def _merge_metric_rows(
    faithfulness_rows: list[dict[str, object]],
    relevancy_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    relevancy_by_case = {str(row["case_id"]): row for row in relevancy_rows}
    merged: list[dict[str, object]] = []
    for faith_row in faithfulness_rows:
        relevancy_row = relevancy_by_case.get(str(faith_row["case_id"]), {})
        result = {"case_id": faith_row["case_id"], "judge_config": faith_row.get("judge_config")}
        for arm_name in ("baseline", "candidate"):
            faith_arm = dict(faith_row[arm_name])
            relevancy_arm = relevancy_row.get(arm_name, {})
            faith_arm.update({
                "response_relevancy": relevancy_arm.get("response_relevancy"),
                "response_relevancy_status": relevancy_arm.get("response_relevancy_status", "not_run"),
            })
            errors = [*faith_arm.get("judge_errors", []), *relevancy_arm.get("judge_errors", [])]
            faith_arm["judge_errors"] = errors
            faith_arm["judge_error"] = "; ".join(
                f"{item.get('error_type')}: {item.get('message', '')}" for item in errors
            ) or None
            result[arm_name] = faith_arm
        merged.append(result)
    return merged


def _mark_relevancy_blocked(
    rows: list[dict[str, object]],
    preflight: dict[str, object],
) -> list[dict[str, object]]:
    attempts = list(preflight.get("attempts", []))
    reason = str(preflight.get("reason", "ResponseRelevancy preflight blocked"))
    output: list[dict[str, object]] = []
    for row in rows:
        current = {"case_id": row["case_id"], "judge_config": row.get("judge_config")}
        for arm_name in ("baseline", "candidate"):
            arm = dict(row[arm_name])
            errors = [*arm.get("judge_errors", []), {
                "metric": "response_relevancy",
                "error_type": "ResponseRelevancyPreflightError",
                "http_status": attempts[-1].get("http_status") if attempts else None,
                "request_id": attempts[-1].get("request_id") if attempts else None,
                "attempt": attempts[-1].get("attempt") if attempts else 0,
                "message": reason,
            }]
            arm.update({
                "response_relevancy": None,
                "response_relevancy_status": "blocked",
                "judge_errors": errors,
                "judge_error": reason,
            })
            current[arm_name] = arm
        output.append(current)
    return output


def _load_environment() -> None:
    path = PROJECT_ROOT / ".env.local_staging"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


async def run(output_json: Path = REPORT_JSON, output_md: Path = REPORT_MD) -> int:
    _load_environment()
    from evaluation.phase10.conversation_e2e_contracts import (
        JudgeConfig,
        fingerprint_dataset,
        runtime_config_fingerprint,
    )
    from evaluation.phase10.conversation_e2e_runner import (
        SnapshotValidationError,
        build_blocked_report,
        build_report,
        load_runtime_snapshot,
        load_semantic_scores,
        run_development_experiment,
        write_artifacts,
        write_semantic_scores,
    )
    from evaluation.phase10.conversation_e2e_semantic import (
        build_openai_compatible_metrics,
        run_metric_smoke,
        run_semantic_preflight,
        score_semantic_rows,
    )
    from industrial_rag.config import Settings
    from industrial_rag.lightrag_service import QueryOptions
    from industrial_rag.vector_collections import VectorBackend

    fingerprint = fingerprint_dataset(DATASET)
    required = ("QDRANT_URL", "QDRANT_KB_ID", "QDRANT_GENERATION", "LIGHTRAG_WORKING_DIR", "DASHSCOPE_API_KEY")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        report = build_blocked_report(fingerprint, "runtime_config_missing", f"missing staging configuration: {', '.join(missing)}")
        write_artifacts(report, output_json, output_md)
        return 2

    settings = Settings.from_env()
    if settings.vector_backend is not VectorBackend.qdrant:
        report = build_blocked_report(fingerprint, "wrong_vector_backend", "Development E2E requires VECTOR_BACKEND=qdrant")
        write_artifacts(report, output_json, output_md)
        return 2
    query_options = QueryOptions(
        mode=settings.phase10b_query_mode,
        top_k=settings.phase10b_top_k,
        chunk_top_k=settings.phase10b_chunk_top_k,
        enable_rerank=False,
    )
    runtime_fp = runtime_config_fingerprint(settings, query_options=query_options).to_dict()
    judge_config = JudgeConfig(
        ragas_version="0.3.9",
        faithfulness_metric="Faithfulness",
        response_relevancy_metric="ResponseRelevancy",
        judge_provider="openai-compatible-dashscope",
        judge_model=JUDGE_MODEL,
        embedding_provider="openai-compatible-dashscope",
        embedding_model=EMBEDDING_MODEL,
        temperature=0.0,
        seed=None,
        timeout_seconds=60,
        retry=2,
        max_concurrency=1,
    )
    if settings.embedding_model != EMBEDDING_MODEL:
        report = build_blocked_report(
            fingerprint,
            "embedding_model_contract_mismatch",
            f"Development runtime embedding model must remain {EMBEDDING_MODEL}",
            runtime_fingerprint=runtime_fp,
            judge_config=judge_config,
        )
        write_artifacts(report, output_json, output_md)
        return 2

    snapshot_manifest: dict[str, object] | None = None
    runtime_cases: list[dict[str, object]] | None = None
    if not RUNTIME_SNAPSHOT.exists():
        report = build_blocked_report(
            fingerprint,
            "snapshot_missing",
            "R3-S is snapshot-only; the frozen runtime snapshot is required and RAG fallback is forbidden",
            runtime_fingerprint=runtime_fp,
            judge_config=judge_config,
        )
        write_artifacts(report, output_json, output_md)
        return 2
    try:
        runtime_cases, snapshot_manifest = load_runtime_snapshot(
            RUNTIME_SNAPSHOT,
            fingerprint,
            runtime_fp,
            expected_snapshot_sha256=EXPECTED_SNAPSHOT_SHA256,
        )
    except SnapshotValidationError as error:
        report = build_blocked_report(
            fingerprint,
            error.reason_code,
            str(error),
            runtime_fingerprint=runtime_fp,
            judge_config=judge_config,
        )
        write_artifacts(report, output_json, output_md)
        return 2
    try:
        existing_semantic_rows = load_semantic_scores(SEMANTIC_SCORES)
    except SnapshotValidationError as error:
        report = build_blocked_report(
            fingerprint,
            error.reason_code,
            str(error),
            runtime_fingerprint=runtime_fp,
            judge_config=judge_config,
            case_count=len(runtime_cases),
        )
        write_artifacts(report, output_json, output_md)
        return 2

    assert runtime_cases is not None and snapshot_manifest is not None
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.api_key,
        base_url=settings.llm_base_url,
        timeout=judge_config.timeout_seconds,
        max_retries=0,
    )
    faith_preflight: dict[str, object] = {"status": "BLOCKED", "reason": "Faithfulness preflight not executed"}
    relevancy_preflight: dict[str, object] = {"status": "BLOCKED", "reason": "ResponseRelevancy preflight not executed"}
    diagnostics: dict[str, object] = {}
    semantic_rows: list[dict[str, object]] = []
    try:
        faithfulness, relevancy = build_openai_compatible_metrics(
            judge_config,
            base_url=settings.llm_base_url,
            api_key=settings.api_key,
        )
        faith_preflight = await run_semantic_preflight(
            config=judge_config,
            client=client,
            faithfulness=faithfulness,
            relevancy=relevancy,
            enabled_metrics=("faithfulness",),
        )
        runtime_case_ids = [str(row["case_id"]) for row in runtime_cases]
        faith_by_case = {
            str(row["case_id"]): row
            for row in existing_semantic_rows
            if all(
                row.get(arm, {}).get("faithfulness_status") in {"available", "blocked"}
                for arm in ("baseline", "candidate")
            )
        }
        pending_cases = [row for row in runtime_cases if str(row["case_id"]) not in faith_by_case]
        faith_checkpoint: list[dict[str, object]] = [faith_by_case[case_id] for case_id in runtime_case_ids if case_id in faith_by_case]

        async def checkpoint_faithfulness(row: dict[str, object]) -> None:
            faith_by_case[str(row["case_id"])] = row
            faith_checkpoint[:] = [faith_by_case[case_id] for case_id in runtime_case_ids if case_id in faith_by_case]
            write_semantic_scores(faith_checkpoint, SEMANTIC_SCORES)

        if faith_preflight["status"] == "READY" and pending_cases:
            faith_report = await run_development_experiment(
                service=None,
                cases=pending_cases,
                mode=query_options.mode,
                top_k=query_options.top_k,
                chunk_top_k=query_options.chunk_top_k,
                runtime_fingerprint=runtime_fp,
                dataset_fingerprint=fingerprint,
                judge_config=judge_config,
                faithfulness=faithfulness,
                relevancy=relevancy,
                frozen_cases=pending_cases,
                enabled_metrics=("faithfulness",),
                semantic_row_callback=checkpoint_faithfulness,
            )
            faith_rows = faith_checkpoint
        else:
            faith_rows = faith_checkpoint
            faith_report = {
                "experiment_artifact": "evaluation/ragas/experiments/resumed-semantic-scores.jsonl",
                "experiment_row_count": 0,
                "validation_holdout_accessed": False,
                "deterministic_case_metrics": [],
            }
        write_semantic_scores(faith_rows, SEMANTIC_SCORES)

        relevancy_preflight = await run_semantic_preflight(
            config=judge_config,
            client=client,
            faithfulness=faithfulness,
            relevancy=relevancy,
            enabled_metrics=("response_relevancy",),
        )
        if relevancy_preflight["status"] == "BLOCKED":
            semantic_rows = _mark_relevancy_blocked(
                faith_rows,
                relevancy_preflight["response_relevancy"],
            )
            semantic_blocked_reason = semantic_preflight_block_reason(faith_preflight)
            response_reason = semantic_preflight_block_reason(relevancy_preflight)
            semantic_blocked_reason = "; ".join(reason for reason in (semantic_blocked_reason, response_reason) if reason != "semantic preflight blocked")
            _, diagnostic_relevancy = build_openai_compatible_metrics(
                judge_config,
                base_url=settings.llm_base_url,
                api_key=settings.api_key,
                response_relevancy_strictness=1,
            )
            diagnostic_result = await run_metric_smoke(
                diagnostic_relevancy,
                metric_name="response_relevancy_strictness_1_diagnostic",
                config=judge_config,
            )
            diagnostics["response_relevancy_strictness_1"] = {
                "status": diagnostic_result["status"],
                "reason_code": diagnostic_result.get("reason_code"),
                "reason": diagnostic_result.get("reason"),
                "attempts": diagnostic_result.get("attempts", []),
                "formal_metric": False,
                "gate_input": False,
            }
        else:
            relevancy_rows = await score_semantic_rows(
                _semantic_input(runtime_cases),
                judge_config,
                faithfulness=faithfulness,
                relevancy=relevancy,
                enabled_metrics=("response_relevancy",),
            )
            if faith_rows:
                semantic_rows = _merge_metric_rows(faith_rows, relevancy_rows)
            else:
                semantic_rows = relevancy_rows
                for row in semantic_rows:
                    for arm_name in ("baseline", "candidate"):
                        row[arm_name]["faithfulness"] = None
                        row[arm_name]["faithfulness_status"] = "not_run"
            semantic_blocked_reason = semantic_preflight_block_reason(faith_preflight) if faith_preflight["status"] == "BLOCKED" else None
        write_semantic_scores(semantic_rows, SEMANTIC_SCORES)
        report = build_report(
            cases=runtime_cases,
            fingerprint=fingerprint,
            runtime_fingerprint=runtime_fp,
            judge_config=judge_config,
            semantic_rows=semantic_rows,
            experiment_artifact=faith_report["experiment_artifact"],
            semantic_blocked_reason=semantic_blocked_reason,
        )
        for field in ("experiment_row_count", "validation_holdout_accessed", "deterministic_case_metrics"):
            report[field] = faith_report.get(field)
    except Exception as error:
        report = build_blocked_report(
            fingerprint,
            "semantic_execution_unavailable",
            f"{type(error).__name__}: {error}",
            runtime_fingerprint=runtime_fp,
            judge_config=judge_config,
            case_count=len(runtime_cases),
        )
    finally:
        await client.close()
    report["previous_blocked_commit"] = PREVIOUS_BLOCKED_COMMIT
    report["implementation_audit"] = {
        "legacy_report_json": "dbaf649 report audited as valid and non-empty (1325738 bytes)",
        "gate_integration": "build_report invokes evaluate_gate with the runtime aggregate schema",
    }
    report["runtime_snapshot"] = {
        "artifact": str(RUNTIME_SNAPSHOT.relative_to(PROJECT_ROOT)),
        "snapshot_sha256": snapshot_manifest["snapshot_sha256"],
        "case_count": snapshot_manifest["case_count"],
        "ordered_case_ids": snapshot_manifest["ordered_case_ids"],
        "dataset_fingerprint_parity": snapshot_manifest["dataset_fingerprint"] == fingerprint.to_dict(),
        "runtime_config_fingerprint_parity": snapshot_manifest["runtime_config_fingerprint"] == runtime_fp,
    }
    report["light_rag_service_call_count"] = 0
    report["semantic_preflight"] = {
        "status": "BLOCKED" if faith_preflight.get("status") == "BLOCKED" or relevancy_preflight.get("status") == "BLOCKED" else "READY",
        "faithfulness": faith_preflight,
        "response_relevancy": relevancy_preflight,
    }
    faith_summary = report.get("semantic", {}).get("faithfulness", {})
    relevancy_summary = report.get("semantic", {}).get("response_relevancy", {})
    report["semantic_metrics"] = {
        "faithfulness": {
            "preflight": faith_preflight.get("status"),
            "formal": "READY" if faith_summary.get("status") == "available" else "BLOCKED" if faith_preflight.get("status") == "BLOCKED" else "NOT_RUN",
            **faith_summary,
        },
        "response_relevancy": {
            "preflight": relevancy_preflight.get("status"),
            "formal": "READY" if relevancy_summary.get("status") == "available" else "NOT_RUN",
            **relevancy_summary,
        },
    }
    report["diagnostics"] = diagnostics
    report["semantic_scores_artifact"] = str(SEMANTIC_SCORES.relative_to(PROJECT_ROOT))
    write_artifacts(report, output_json, output_md)
    print(console_summary(report))
    return 0 if report.get("status") in {"R3_PASS", "R3_MIXED"} else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-json", type=Path, default=REPORT_JSON)
    parser.add_argument("--output-md", type=Path, default=REPORT_MD)
    args = parser.parse_args()
    return asyncio.run(run(args.output_json, args.output_md))


if __name__ == "__main__":
    raise SystemExit(main())

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
        build_runtime_snapshot,
        build_runtime_snapshot_from_report,
        load_runtime_snapshot,
        resolve_runtime_cases,
        run_development_experiment,
        write_artifacts,
        write_runtime_snapshot,
    )
    from evaluation.phase10.conversation_e2e_semantic import (
        build_openai_compatible_metrics,
        run_semantic_preflight,
    )
    from industrial_rag.config import Settings
    from industrial_rag.lightrag_service import LightRAGService, QueryOptions
    from industrial_rag.vector_collections import VectorBackend
    from scripts.evaluate_conversation_retrieval_development import load_conversation_cases

    fingerprint = fingerprint_dataset(DATASET)
    cases = load_conversation_cases(DATASET)
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
    if RUNTIME_SNAPSHOT.exists():
        try:
            runtime_cases, snapshot_manifest = load_runtime_snapshot(RUNTIME_SNAPSHOT, fingerprint, runtime_fp)
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
    elif REPORT_JSON.exists():
        try:
            previous_report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
            snapshot = build_runtime_snapshot_from_report(previous_report, fingerprint, runtime_fp)
            write_runtime_snapshot(snapshot, RUNTIME_SNAPSHOT)
            runtime_cases, snapshot_manifest = load_runtime_snapshot(RUNTIME_SNAPSHOT, fingerprint, runtime_fp)
        except (OSError, json.JSONDecodeError, SnapshotValidationError) as error:
            reason_code = error.reason_code if isinstance(error, SnapshotValidationError) else "legacy_report_unusable"
            report = build_blocked_report(
                fingerprint,
                reason_code,
                f"cannot create a trusted runtime snapshot: {type(error).__name__}: {error}",
                runtime_fingerprint=runtime_fp,
                judge_config=judge_config,
            )
            write_artifacts(report, output_json, output_md)
            return 2
    else:
        service = LightRAGService(settings)
        try:
            await service.initialize()
            runtime_cases = await resolve_runtime_cases(
                service,
                cases,
                mode=query_options.mode,
                top_k=query_options.top_k,
                chunk_top_k=query_options.chunk_top_k,
            )
            snapshot = build_runtime_snapshot(runtime_cases, fingerprint, runtime_fp)
            write_runtime_snapshot(snapshot, RUNTIME_SNAPSHOT)
            runtime_cases, snapshot_manifest = load_runtime_snapshot(RUNTIME_SNAPSHOT, fingerprint, runtime_fp)
        except Exception as error:
            report = build_blocked_report(
                fingerprint,
                "e2e_runtime_unavailable",
                f"{type(error).__name__}: {error}",
                runtime_fingerprint=runtime_fp,
                judge_config=judge_config,
            )
            write_artifacts(report, output_json, output_md)
            return 2
        finally:
            await service.close()

    assert runtime_cases is not None and snapshot_manifest is not None
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.api_key,
        base_url=settings.llm_base_url,
        timeout=judge_config.timeout_seconds,
        max_retries=0,
    )
    try:
        faithfulness, relevancy = build_openai_compatible_metrics(
            judge_config,
            base_url=settings.llm_base_url,
            api_key=settings.api_key,
        )
        preflight = await run_semantic_preflight(
            config=judge_config,
            client=client,
            faithfulness=faithfulness,
            relevancy=relevancy,
        )
        report = await run_development_experiment(
            service=None,
            cases=cases,
            mode=query_options.mode,
            top_k=query_options.top_k,
            chunk_top_k=query_options.chunk_top_k,
            runtime_fingerprint=runtime_fp,
            dataset_fingerprint=fingerprint,
            judge_config=judge_config,
            faithfulness=faithfulness,
            relevancy=relevancy,
            semantic_blocked_reason=semantic_preflight_block_reason(preflight) if preflight["status"] == "BLOCKED" else None,
            frozen_cases=runtime_cases,
        )
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
    report["runtime_snapshot"] = {
        "artifact": str(RUNTIME_SNAPSHOT.relative_to(PROJECT_ROOT)),
        "snapshot_sha256": snapshot_manifest["snapshot_sha256"],
        "case_count": snapshot_manifest["case_count"],
        "ordered_case_ids": snapshot_manifest["ordered_case_ids"],
        "dataset_fingerprint_parity": snapshot_manifest["dataset_fingerprint"] == fingerprint.to_dict(),
        "runtime_config_fingerprint_parity": snapshot_manifest["runtime_config_fingerprint"] == runtime_fp,
    }
    report["semantic_preflight"] = preflight if "preflight" in locals() else {"status": "BLOCKED", "reason": report.get("reason")}
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

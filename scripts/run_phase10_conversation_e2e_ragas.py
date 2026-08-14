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
        build_blocked_report,
        run_development_experiment,
        write_artifacts,
    )
    from evaluation.phase10.conversation_e2e_semantic import (
        build_openai_compatible_metrics,
        semantic_smoke_test,
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
        judge_model=settings.llm_model,
        embedding_provider="openai-compatible-dashscope",
        embedding_model=settings.embedding_model,
        temperature=0.0,
        seed=None,
        timeout_seconds=60,
        retry=2,
        max_concurrency=1,
    )
    service = LightRAGService(settings)
    try:
        await service.initialize()
        faithfulness, relevancy = build_openai_compatible_metrics(
            judge_config,
            base_url=settings.llm_base_url,
            api_key=settings.api_key,
        )
        smoke = await semantic_smoke_test(
            faithfulness=faithfulness,
            relevancy=relevancy,
            config=judge_config,
        )
        report = await run_development_experiment(
            service=service,
            cases=cases,
            mode=query_options.mode,
            top_k=query_options.top_k,
            chunk_top_k=query_options.chunk_top_k,
            runtime_fingerprint=runtime_fp,
            dataset_fingerprint=fingerprint,
            judge_config=judge_config,
            faithfulness=faithfulness,
            relevancy=relevancy,
            semantic_blocked_reason=smoke.get("judge_error") if smoke["status"] == "BLOCKED" else None,
        )
    except Exception as error:
        report = build_blocked_report(fingerprint, "e2e_runtime_unavailable", f"{type(error).__name__}: {error}")
    finally:
        await service.close()
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

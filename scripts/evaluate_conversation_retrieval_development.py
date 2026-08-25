"""Legacy/frozen Development evaluator; new runs use the Ragas migration runner."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from industrial_rag.conversation.query_rewriter import QueryRewriter
from industrial_rag.conversation.retrieval_evaluation import (
    aggregate_metric_rows,
    compare_retrieval_metrics,
)
from industrial_rag.lightrag_service import (
    LightRAGBackend,
    QueryOptions,
    _extract_retrieved,
)
from industrial_rag.query_normalization import normalize_query

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = PROJECT_ROOT / "data/evaluation/conversation_retrieval_development.jsonl"
SOURCE_GOLD_PATH = PROJECT_ROOT / "evaluation/phase10/expanded_golden_set.jsonl"
ALLOWED_DEVELOPMENT_IDS = {
    *(f"S{index:03d}" for index in range(1, 21)),
    *(f"D{index:03d}" for index in range(1, 17)),
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_conversation_cases(path: Path = DATASET_PATH) -> list[dict[str, Any]]:
    cases = _load_jsonl(path)
    required = {
        "case_id",
        "source_question_id",
        "history",
        "dependent_query",
        "expected_standalone_query",
        "gold_chunk_ids",
        "category",
    }
    for case in cases:
        if not required <= case.keys():
            raise ValueError("conversation case is missing required fields")
    return cases


def validate_development_cases(
    cases: list[dict[str, Any]], source_gold_path: Path = SOURCE_GOLD_PATH
) -> None:
    source_rows = {row["question_id"]: row for row in _load_jsonl(source_gold_path)}
    seen_cases: set[str] = set()
    for case in cases:
        source_id = case["source_question_id"]
        if source_id not in ALLOWED_DEVELOPMENT_IDS:
            raise ValueError(f"source question is not allowed Development ID: {source_id}")
        if case["case_id"] in seen_cases:
            raise ValueError(f"duplicate conversation case: {case['case_id']}")
        seen_cases.add(case["case_id"])
        source = source_rows.get(source_id)
        if source is None or source.get("split") != "development":
            raise ValueError(f"source question is not Development: {source_id}")
        if source.get("answerable") is not True:
            raise ValueError(f"source question is not answerable: {source_id}")
        expected_gold = [item["chunk_id"] for item in source["expected_evidence"]]
        if case["gold_chunk_ids"] != expected_gold:
            raise ValueError(f"gold chunk provenance mismatch: {case['case_id']}")


def _fingerprint(config: dict[str, Any], fingerprint: dict[str, Any]) -> dict[str, Any]:
    payload = {**fingerprint, "retrieval_config": config}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return {**payload, "sha256": hashlib.sha256(encoded).hexdigest()}


async def evaluate_backend(
    backend: LightRAGBackend,
    *,
    cases: list[dict[str, Any]],
    config: QueryOptions,
    fingerprint: dict[str, Any],
) -> dict[str, Any]:
    validate_development_cases(cases)
    rewriter = QueryRewriter()
    rows: list[dict[str, Any]] = []
    for case in cases:
        rewrite = await rewriter.rewrite(case["dependent_query"], case["history"])
        expected_normalized = normalize_query(case["expected_standalone_query"]).normalized_query
        if rewrite.status != "rewritten":
            raise ValueError(f"rewrite failed for {case['case_id']}: {rewrite.status}")
        rewritten_normalized = normalize_query(rewrite.standalone_query or "").normalized_query
        if rewritten_normalized != expected_normalized:
            raise ValueError(f"rewrite gold mismatch for {case['case_id']}")
        before_query = normalize_query(case["dependent_query"]).normalized_query
        before = await backend.aquery_data(before_query, config)
        after = await backend.aquery_data(rewritten_normalized, config)
        before_results = _extract_retrieved(before)
        after_results = _extract_retrieved(after)
        comparison = compare_retrieval_metrics(
            before_results, after_results, case["gold_chunk_ids"]
        )
        rows.append(
            {
                "case_id": case["case_id"],
                "source_question_id": case["source_question_id"],
                "category": case["category"],
                "dependent_query": case["dependent_query"],
                "rewritten_query": rewrite.standalone_query,
                "gold_chunk_ids": case["gold_chunk_ids"],
                "before_query": before_query,
                "after_query": rewritten_normalized,
                "rewrite_status": rewrite.status,
                "before_ranks": comparison["before_ranks"],
                "after_ranks": comparison["after_ranks"],
                **comparison,
            }
        )
    before_metrics = aggregate_metric_rows([row["before"] for row in rows])
    after_metrics = aggregate_metric_rows([row["after"] for row in rows])
    return {
        "status": "READY",
        "dataset": {
            "case_count": len(rows),
            "source_question_ids": [row["source_question_id"] for row in rows],
            "category_distribution": dict(Counter(row["category"] for row in rows)),
            "development_only_guard": True,
        },
        "rewrite": {
            "rewrite_accuracy": 1.0,
            "failed_cases": [],
            "ambiguous_cases": [],
            "unnecessary_rewrite": 0.0,
        },
        "before": before_metrics,
        "after": after_metrics,
        "delta": {
            key: after_metrics[key] - before_metrics[key] for key in before_metrics
        },
        "improved_cases": [row["case_id"] for row in rows if row["improved"]],
        "unchanged_cases": [row["case_id"] for row in rows if row["unchanged"]],
        "regressed_cases": [
            {
                key: row[key]
                for key in (
                    "case_id",
                    "source_question_id",
                    "dependent_query",
                    "rewritten_query",
                    "gold_chunk_ids",
                    "before_ranks",
                    "after_ranks",
                )
            }
            for row in rows
            if row["regressed"]
        ],
        "cases": rows,
        "fingerprint": _fingerprint(
            {
                "mode": config.mode,
                "top_k": config.top_k,
                "chunk_top_k": config.chunk_top_k,
                "enable_rerank": config.enable_rerank,
            },
            fingerprint,
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cases = load_conversation_cases()
    validate_development_cases(cases)
    report = {
        "status": "BLOCKED",
        "reason_code": "real_backend_not_configured",
        "reason": "Run this evaluator with a configured Development KB backend; no retrieval metrics are fabricated by the offline CLI.",
        "dataset": {"case_count": len(cases), "development_only_guard": True},
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


def _load_staging_environment() -> None:
    env_path = PROJECT_ROOT / ".env.local_staging"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            import os

            os.environ.setdefault(key.strip(), value.strip())


async def evaluate_configured_staging(output: Path | None = None) -> int:
    """Run against the explicitly configured staging Development generation."""

    import os

    _load_staging_environment()
    required = (
        "QDRANT_URL",
        "QDRANT_KB_ID",
        "QDRANT_GENERATION",
        "LIGHTRAG_WORKING_DIR",
        "DASHSCOPE_API_KEY",
    )
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise RuntimeError(f"staging retrieval configuration is incomplete: {', '.join(missing)}")
    from industrial_rag.config import Settings
    from industrial_rag.lightrag_service import LightRAGService
    from industrial_rag.vector_collections import VectorBackend

    settings = Settings.from_env()
    if settings.vector_backend is not VectorBackend.qdrant:
        raise RuntimeError("Development retrieval proof requires VECTOR_BACKEND=qdrant")
    service = LightRAGService(settings)
    await service.initialize()
    try:
        report = await evaluate_backend(
            service._backend,  # type: ignore[arg-type, union-attr]
            cases=load_conversation_cases(),
            config=QueryOptions(
                mode=settings.phase10b_query_mode, top_k=settings.phase10b_top_k,
                chunk_top_k=settings.phase10b_chunk_top_k, enable_rerank=False,
            ),
            fingerprint={
                "knowledge_base_id": settings.qdrant_kb_id,
                "generation_id": settings.qdrant_generation,
                "workspace": str(settings.working_dir),
                "vector_backend": settings.vector_backend.value,
                "embedding_model": settings.embedding_model,
            },
        )
    finally:
        await service.close()
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output:
        output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

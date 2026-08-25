"""Pure contracts and provenance helpers for the Development E2E experiment."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_DEVELOPMENT_IDS = {*(f"S{index:03d}" for index in range(1, 21)), *(f"D{index:03d}" for index in range(1, 17))}


@dataclass(frozen=True, slots=True)
class DatasetFingerprint:
    source_path: str
    raw_sha256: str
    semantic_sha256: str
    case_count: int
    case_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "raw_sha256": self.raw_sha256,
            "semantic_sha256": self.semantic_sha256,
            "case_count": self.case_count,
            "case_ids": list(self.case_ids),
        }


@dataclass(frozen=True, slots=True)
class RuntimeConfigFingerprint:
    payload: dict[str, Any]
    digest: str

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload, "sha256": self.digest}


@dataclass(frozen=True, slots=True)
class JudgeConfig:
    ragas_version: str
    faithfulness_metric: str
    response_relevancy_metric: str
    judge_provider: str
    judge_model: str
    embedding_provider: str
    embedding_model: str
    temperature: float
    seed: int | None
    timeout_seconds: int
    retry: int
    max_concurrency: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "ragas_version": self.ragas_version,
            "metrics": [self.faithfulness_metric, self.response_relevancy_metric],
            "judge_provider": self.judge_provider,
            "judge_model": self.judge_model,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "temperature": self.temperature,
            "seed": self.seed,
            "timeout_seconds": self.timeout_seconds,
            "retry": self.retry,
            "max_concurrency": self.max_concurrency,
        }


def _load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def fingerprint_dataset(path: Path) -> DatasetFingerprint:
    path = path.resolve()
    rows = _load_rows(path)
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return DatasetFingerprint(
        source_path=str(path.relative_to(PROJECT_ROOT)),
        raw_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        semantic_sha256=hashlib.sha256(canonical).hexdigest(),
        case_count=len(rows),
        case_ids=tuple(str(row["case_id"]) for row in rows),
    )


def assert_development_only(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        source_id = str(row.get("source_question_id") or "")
        if source_id not in ALLOWED_DEVELOPMENT_IDS or source_id.startswith(("V", "H")):
            raise ValueError(f"source question is not Development: {source_id}")


def resolved_evaluation_user_input(case: dict[str, Any]) -> str:
    value = str(case.get("standalone_query") or case.get("expected_standalone_query") or "").strip()
    if not value:
        raise ValueError("conversation case has no frozen standalone evaluator question")
    return value


def provider_context_payload(result: Any) -> dict[str, Any]:
    trace = getattr(result, "retrieval_trace", None)
    if trace is None:
        return {"provider_evidence_ids": [], "provider_context_order": [], "provider_context_sha256": None}
    return {
        "provider_evidence_ids": list(trace.provider_evidence_ids),
        "provider_context_order": list(trace.provider_context_order),
        "provider_context_sha256": trace.provider_context_sha256,
    }


def _setting(settings: Any, name: str, default: Any = None) -> Any:
    value = getattr(settings, name, default)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, tuple):
        return list(value)
    return value


def runtime_config_fingerprint(settings: Any, *, query_text: str | None = None, query_options: Any = None) -> RuntimeConfigFingerprint:
    payload = {
        "knowledge_base_id": _setting(settings, "qdrant_kb_id"),
        "generation_id": _setting(settings, "qdrant_generation"),
        "workspace": _setting(settings, "working_dir"),
        "vector_backend": _setting(settings, "vector_backend"),
        "embedding_model": _setting(settings, "embedding_model"),
        "embedding_dim": _setting(settings, "embedding_dim"),
        "query_options": {
            "mode": _setting(query_options, "mode", _setting(settings, "phase10b_query_mode")),
            "top_k": _setting(query_options, "top_k", _setting(settings, "phase10b_top_k")),
            "chunk_top_k": _setting(query_options, "chunk_top_k", _setting(settings, "phase10b_chunk_top_k")),
            "enable_rerank": _setting(query_options, "enable_rerank", False),
        },
        "settings": {
            name: _setting(settings, name)
            for name in (
                "query_normalization_enabled", "answer_grounding_enabled", "grounding_audit_enabled",
                "evidence_selection_diversity_enabled", "evidence_completion_enabled", "evidence_completion_max",
                "supplemental_retrieval_enabled", "structured_citation_output_enabled", "llm_base_url",
                "llm_model", "llm_fallback_models",
            )
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return RuntimeConfigFingerprint(payload=payload, digest=hashlib.sha256(encoded).hexdigest())

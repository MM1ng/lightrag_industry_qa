"""Frozen Phase 5 configuration and artifact paths."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PHASE5_ROOT = Path(__file__).resolve().parent

PHASE5_CONFIG = {
    "parser_pipeline": "pymupdf_standard_adapter",
    "query_mode": "mix",
    "top_k": 12,
    "chunk_top_k": 20,
    "parent_expansion": "none",
    "rerank_enabled": False,
    "embedding_model": "text-embedding-v4",
    "embedding_dimension": 1024,
    "answer_model": "qwen-plus-2025-07-28",
    "fallback_enabled": False,
    "thinking_enabled": False,
    "golden_set": "industrial_pump_golden_set_50.jsonl",
    "candidate_k": 20,
    "final_k": 12,
    "evidence_limit": 3,
    "max_context_tokens": 6000,
    "max_repair_attempts": 1,
    "safe_fallback_enabled": True,
    "grounded_answer_enabled": False,
    "context_stable_dedup_enabled": False,
}

SOURCE_COMMIT = "dd9ce808b7ea32071c5e1db043e118d741dc5750"

GOLDEN_SET_PATH = PROJECT_ROOT / "data" / "evaluation" / "industrial_pump_golden_set_50.jsonl"
CANDIDATE_POOL_PATH = (
    PROJECT_ROOT
    / "evaluation"
    / "experiments"
    / "phase4"
    / "parent_expansion"
    / "frozen_child_results.jsonl"
)
CANDIDATE_POOL_SHA256 = "fc731efc904d9d9dca639fecf181a01e022c162ac91b67f6432d18b7619bf6a0"
FROZEN_INDEX_MANIFEST = (
    PROJECT_ROOT
    / "evaluation"
    / "experiments"
    / "phase4"
    / "parent_expansion"
    / "manifests"
    / "index_manifest.json"
)
PHASE4_ANSWERS_CN0 = (
    PROJECT_ROOT
    / "evaluation"
    / "experiments"
    / "phase4"
    / "rerank"
    / "results"
    / "answers"
    / "baseline.jsonl"
)
PHASE4_ANSWERS_R1 = (
    PROJECT_ROOT
    / "evaluation"
    / "experiments"
    / "phase4"
    / "rerank"
    / "results"
    / "answers"
    / "reranked.jsonl"
)
PHASE4_STAGE2_METRICS = (
    PROJECT_ROOT
    / "evaluation"
    / "experiments"
    / "phase4"
    / "rerank"
    / "results"
    / "answers"
    / "metrics.json"
)
PYMUPDF_CHILDREN_DIR = PROJECT_ROOT / "evaluation" / "experiments" / "parser_backend" / "P0"
EVIDENCE_MAPPING_PATH = (
    PROJECT_ROOT
    / "evaluation"
    / "experiments"
    / "parser_backend"
    / "fixed_model"
    / "comparison"
    / "evidence_mapping_p0.json"
)

PDF_NAMES = ("2196-ANSI-Manual-Chinese.pdf", "t1739cn.pdf")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def prompt_bundle_path() -> Path:
    return (
        PHASE5_ROOT
        / "grounded_answer"
        / "prompt_bundle"
        / "prompt_bundle_v1.json"
    )


def category_manifest_path() -> Path:
    return PHASE5_ROOT / "manifests" / "category_manifest.json"

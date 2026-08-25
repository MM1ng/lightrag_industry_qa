"""Frozen Phase 4D configuration."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
EXPERIMENT_ROOT = Path(__file__).resolve().parent

RERANK_CONFIG = {
    "parser_pipeline": "pymupdf_standard_adapter",
    "query_mode": "mix",
    "top_k": 12,
    "chunk_top_k": 20,
    "parent_expansion": "none",
    "candidate_k": 20,
    "final_k": 12,
    "rerank_enabled": False,
    "rerank_fallback_enabled": False,
    "rerank_timeout_seconds": 60.0,
}

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

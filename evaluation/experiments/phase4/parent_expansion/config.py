"""Frozen Phase 4C configuration (single source of truth)."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
EXPERIMENT_ROOT = Path(__file__).resolve().parent

FIXED_MODEL = "qwen-plus-2025-07-28"

EXPANSION_CONFIG = {
    "parser_pipeline": "pymupdf_standard_adapter",
    "query_mode": "mix",
    "top_k": 12,
    "chunk_top_k": 20,
    "rerank": False,
    "evidence_limit": 3,
    "parent_expansion_strategies": ["none", "top_1_parent", "top_3_parents", "adaptive"],
    "max_parents": 3,
    "max_context_tokens": 6000,
    "preserve_all_children": True,
    "deduplicate_parents": True,
}

PYMUPDF_CHILDREN_DIR = PROJECT_ROOT / "evaluation" / "experiments" / "parser_backend" / "P0"
PYMUPDF_PARENTS_DIR = PROJECT_ROOT / "evaluation" / "experiments" / "parser_backend" / "P0"
FIXED_MODEL_DIR = PROJECT_ROOT / "evaluation" / "experiments" / "parser_backend" / "fixed_model"
GOLDEN_SET_PATH = PROJECT_ROOT / "data" / "evaluation" / "industrial_pump_golden_set_50.jsonl"
GOLDEN_SHA256 = "fc52600fcce019d7f3cab04e0d0306ce336c468873ba2aef44391cc863e37aaf"
PROMPT_BUNDLE_PATH = PROJECT_ROOT / "evaluation" / "experiments" / "parser_backend" / "fixed_model" / "prompt_bundle.json"
PDF_NAMES = ("2196-ANSI-Manual-Chinese.pdf", "t1739cn.pdf")


def results_dir(strategy: str) -> Path:
    return EXPERIMENT_ROOT / "results" / strategy


def output_dir() -> Path:
    return EXPERIMENT_ROOT

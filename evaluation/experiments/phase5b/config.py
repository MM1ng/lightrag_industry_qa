"""Phase 5B paths and frozen hashes."""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PHASE5B_ROOT = Path(__file__).resolve().parent

SOURCE_COMMIT = "2ff697a9d356d99ea8431e74311f5c78229982a5"

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
PHASE4_R0_ANSWERS = (
    PROJECT_ROOT
    / "evaluation"
    / "experiments"
    / "phase4"
    / "rerank"
    / "results"
    / "answers"
    / "baseline.jsonl"
)
PHASE4_ANSWER_CACHE = (
    PROJECT_ROOT
    / "evaluation"
    / "experiments"
    / "phase4"
    / "rerank"
    / "cache"
    / "phase4_answers.jsonl"
)
PHASE5_GA0_ANSWERS = (
    PROJECT_ROOT
    / "evaluation"
    / "experiments"
    / "phase5"
    / "grounded_answer"
    / "results"
    / "baseline"
    / "answers.jsonl"
)
PHASE5_GA1_ANSWERS = (
    PROJECT_ROOT
    / "evaluation"
    / "experiments"
    / "phase5"
    / "grounded_answer"
    / "results"
    / "grounded"
    / "answers.jsonl"
)
PHASE5_COMPARISON = (
    PROJECT_ROOT
    / "evaluation"
    / "experiments"
    / "phase5"
    / "grounded_answer"
    / "metrics"
    / "comparison.json"
)
EVIDENCE_MAPPING_PATH = (
    PROJECT_ROOT
    / "evaluation"
    / "experiments"
    / "parser_backend"
    / "fixed_model"
    / "comparison"
    / "evidence_mapping_p0.json"
)
PYMUPDF_CHILDREN_DIR = PROJECT_ROOT / "evaluation" / "experiments" / "parser_backend" / "P0"
PDF_NAMES = ("2196-ANSI-Manual-Chinese.pdf", "t1739cn.pdf")


def sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

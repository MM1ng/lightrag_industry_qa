"""Phase 6B paths and frozen hashes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PHASE6B_ROOT = Path(__file__).resolve().parent
SOURCE_COMMIT = "56e5550921fb68802c0ea57537c2983ed122484e"

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
PHASE6_GOLDEN = (
    PROJECT_ROOT / "evaluation" / "experiments" / "phase6" / "e2e" / "golden_results.jsonl"
)
PHASE6_METRICS = (
    PROJECT_ROOT / "evaluation" / "experiments" / "phase6" / "e2e" / "metrics.json"
)
PHASE6_SHADOW = (
    PROJECT_ROOT / "evaluation" / "experiments" / "phase6" / "shadow_audit" / "metrics.json"
)
PHASE6_RELEASE_GATES = (
    PROJECT_ROOT / "evaluation" / "experiments" / "phase6" / "manifests" / "result_manifest.json"
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
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

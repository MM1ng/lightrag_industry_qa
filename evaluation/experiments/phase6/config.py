"""Phase 6 paths and frozen hashes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PHASE6_ROOT = Path(__file__).resolve().parent
PYTHONPATH = PROJECT_ROOT / "src"

SOURCE_COMMIT = "90429f89d44cf1143a6d2eacb6b5768eb0e4d514"

GOLDEN_SET_PATH = PROJECT_ROOT / "data" / "evaluation" / "industrial_pump_golden_set_50.jsonl"
GOLDEN_SHA256 = "fc52600fcce019d7f3cab04e0d0306ce336c468873ba2aef44391cc863e37aaf"
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
PROMPT_BUNDLE_PATH = (
    PROJECT_ROOT / "evaluation" / "experiments" / "parser_backend" / "fixed_model" / "prompt_bundle.json"
)
PHASE5_STRATEGY = PROJECT_ROOT / "evaluation" / "experiments" / "phase5" / "final_answer_strategy.json"
PHASE5B_STRATEGY = PROJECT_ROOT / "evaluation" / "experiments" / "phase5b" / "final_answer_strategy.json"
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

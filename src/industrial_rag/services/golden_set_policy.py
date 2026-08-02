"""Frozen Phase 9B canonical validation policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from industrial_rag.config import PROJECT_ROOT

GOLDEN_SET_VERSION = "phase9b-canonical-20-v1"
RUNNER_VERSION = "phase9b-fastapi-candidate-v1"
CANONICAL_QUESTION_IDS = (
    "S001", "S002", "S003", "S004", "S005", "S007", "S009", "S011",
    "S012", "S014", "S015", "S016", "S017", "D003", "D005", "C001",
    "C002", "C003", "N001", "N002",
)
DEFAULT_GOLDEN_SET_PATH = (
    PROJECT_ROOT / "data" / "evaluation" / "industrial_pump_golden_set_50.jsonl"
)


@dataclass(frozen=True, slots=True)
class GoldenSetPolicy:
    version: str
    source_path: Path
    source_sha256: str
    questions: tuple[dict[str, Any], ...]


def load_canonical_policy(path: Path = DEFAULT_GOLDEN_SET_PATH) -> GoldenSetPolicy:
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    by_id = {str(row.get("id")): row for row in rows}
    missing = [question_id for question_id in CANONICAL_QUESTION_IDS if question_id not in by_id]
    if missing:
        raise ValueError(f"canonical golden set missing IDs: {','.join(missing)}")
    questions = tuple(by_id[question_id] for question_id in CANONICAL_QUESTION_IDS)
    if len(questions) != 20 or len({item["id"] for item in questions}) != 20:
        raise ValueError("canonical golden policy must contain exactly 20 unique questions")
    return GoldenSetPolicy(
        version=GOLDEN_SET_VERSION,
        source_path=path.resolve(),
        source_sha256=hashlib.sha256(raw).hexdigest(),
        questions=questions,
    )

"""Thin, provenance-preserving adapter for the frozen conversation dataset."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ragas import Dataset
from ragas.backends.local_jsonl import LocalJSONLBackend
from ragas.dataset_schema import SingleTurnSample

from scripts.evaluate_conversation_retrieval_development import (
    SOURCE_GOLD_PATH,
    validate_development_cases,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = PROJECT_ROOT / "data/evaluation/conversation_retrieval_development.jsonl"


@dataclass(frozen=True)
class ConversationDatasetBundle:
    cases: list[dict[str, Any]]
    dataset: Dataset
    fingerprint: dict[str, Any]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def dataset_fingerprint(
    path: Path = DATASET_PATH, rows: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    raw = path.read_bytes()
    semantic_rows = rows if rows is not None else _load_jsonl(path)
    canonical = json.dumps(
        semantic_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "source_path": str(path.relative_to(PROJECT_ROOT)),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "semantic_sha256": hashlib.sha256(canonical).hexdigest(),
        "case_count": len(semantic_rows),
        "case_ids": [row["case_id"] for row in semantic_rows],
    }


def _ragas_row(case: dict[str, Any]) -> dict[str, Any]:
    """Map fields without changing the source row or the gold ID order."""

    # SingleTurnSample is kept as a validation object, while Dataset stores the
    # complete trace row (including metadata Ragas does not model itself).
    sample = SingleTurnSample(
        user_input=case["dependent_query"],
        retrieved_context_ids=[],
        reference_context_ids=list(case["gold_chunk_ids"]),
    )
    return {
        **case,
        "user_input": sample.user_input,
        "retrieved_context_ids": sample.retrieved_context_ids,
        "reference_context_ids": sample.reference_context_ids,
        "trace": {
            "case_id": case["case_id"],
            "input_query": case["dependent_query"],
            "rewritten_query": None,
            "retrieved_chunk_ids": [],
            "retrieved_ranks": {},
            "gold_ids": list(case["gold_chunk_ids"]),
            "first_gold_rank": None,
            "latency_ms": None,
            "rewrite_metadata": None,
        },
    }


def build_ragas_dataset(path: Path = DATASET_PATH) -> ConversationDatasetBundle:
    cases = _load_jsonl(path)
    validate_development_cases(cases, SOURCE_GOLD_PATH)
    if not cases:
        raise ValueError("Development dataset is empty")
    rows = [_ragas_row(case) for case in cases]
    backend = LocalJSONLBackend(str(PROJECT_ROOT / "evaluation/ragas"))
    dataset = Dataset(
        name="conversation-retrieval-development",
        backend=backend,
        data=rows,
    )
    return ConversationDatasetBundle(
        cases=cases,
        dataset=dataset,
        fingerprint=dataset_fingerprint(path, cases),
    )

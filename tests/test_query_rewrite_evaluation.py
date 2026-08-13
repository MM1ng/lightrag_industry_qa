from __future__ import annotations

import asyncio
import json

from scripts.evaluate_query_rewrite import DATASET, evaluate


def test_development_dataset_has_expected_shape_and_size() -> None:
    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert 30 <= len(rows) <= 40
    assert {row["category"] for row in rows} == {
        "Pronoun", "Ellipsis", "Constraint Inheritance", "Independent Query",
        "Ambiguous Reference", "Topic Switch", "Long History",
    }
    assert all({"history", "query", "expected_status", "expected_standalone_query", "category"} <= row.keys() for row in rows)


def test_development_evaluation_is_reproducible() -> None:
    metrics = asyncio.run(evaluate())
    assert metrics["cases"] == 34
    assert metrics["rewrite_accuracy"] >= 0.8
    assert metrics["ambiguous_detection_accuracy"] == 1.0
    assert metrics["unnecessary_rewrite_rate"] <= 0.1

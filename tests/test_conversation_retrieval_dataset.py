from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from scripts.evaluate_conversation_retrieval_development import (
    ALLOWED_DEVELOPMENT_IDS,
    DATASET_PATH,
    SOURCE_GOLD_PATH,
    load_conversation_cases,
    validate_development_cases,
)


def test_conversation_dataset_is_real_development_only_and_provenance_backed() -> None:
    cases = load_conversation_cases(DATASET_PATH)

    assert 12 <= len(cases) <= 20
    assert {case["source_question_id"] for case in cases} <= ALLOWED_DEVELOPMENT_IDS
    assert {case["category"] for case in cases} >= {
        "Pronoun Resolution",
        "Ellipsis",
        "Property Inheritance",
        "Topic Continuation",
    }
    assert all(case["history"] for case in cases)
    assert all(case["gold_chunk_ids"] for case in cases)

    source_rows = {
        row["question_id"]: row
        for row in (
            json.loads(line)
            for line in SOURCE_GOLD_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    for case in cases:
        source = source_rows[case["source_question_id"]]
        assert source["split"] == "development"
        assert source["answerable"] is True
        assert case["gold_chunk_ids"] == [
            evidence["chunk_id"] for evidence in source["expected_evidence"]
        ]


def test_validate_development_cases_rejects_validation_or_unknown_source_ids(
    tmp_path: Path,
) -> None:
    cases = load_conversation_cases(DATASET_PATH)

    invalid = [dict(cases[0], source_question_id="D017")]
    with pytest.raises(ValueError, match="Development"):
        validate_development_cases(invalid, SOURCE_GOLD_PATH)

    invalid = [dict(cases[0], source_question_id="N001")]
    with pytest.raises(ValueError, match="allowed"):
        validate_development_cases(invalid, SOURCE_GOLD_PATH)


def test_dataset_has_multiple_real_source_questions() -> None:
    cases = load_conversation_cases(DATASET_PATH)

    assert len({case["source_question_id"] for case in cases}) == len(cases)
    assert Counter(case["category"] for case in cases)["Ellipsis"] >= 2

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from industrial_rag.phase10b_failure_analysis import (
    build_failure_matrix,
    classify_failure,
    summarize_failure_matrix,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PHASE10_DIR = PROJECT_ROOT / "evaluation" / "phase10"


def _case(
    *,
    split: str = "development",
    expected_ids: tuple[str, ...] = ("gold-1",),
    initial_ids: tuple[str, ...] = ("other",),
    selected_ids: tuple[str, ...] = (),
    citation_ids: tuple[str, ...] = (),
    status: str = "insufficient_evidence",
    question_type: str = "procedure",
) -> dict:
    evidence = [
        {
            "evidence_id": f"evidence-{index}",
            "document_name": "manual.pdf",
            "page_number": index,
            "chunk_id": chunk_id,
            "evidence_text": f"证据 {chunk_id} 的操作步骤。",
            "role": "primary",
            "relevance_grade": 2,
        }
        for index, chunk_id in enumerate(expected_ids, start=1)
    ]
    initial = [
        {
            "initial_rank": index,
            "chunk_id": chunk_id,
            "document_id": "doc-1",
            "document_name": "manual.pdf",
            "page_number": 1,
            "matched_terms": ["如何"],
        }
        for index, chunk_id in enumerate(initial_ids, start=1)
    ]
    selected = [
        {
            "final_rank": index,
            "chunk_id": chunk_id,
            "document_id": "doc-1",
            "document_name": "manual.pdf",
            "page_number": 1,
            "initial_rank": index,
            "used_for_answer": True,
            "cited_in_answer": chunk_id in citation_ids,
        }
        for index, chunk_id in enumerate(selected_ids, start=1)
    ]
    return {
        "question_id": "Q001",
        "golden": {
            "question_id": "Q001",
            "split": split,
            "question": "如何操作？",
            "question_type": question_type,
            "difficulty": "medium",
            "answerable": True,
            "expected_evidence": evidence,
            "expected_answer_points": [
                {"point_id": "point-1", "text": "操作步骤", "supported_by": ["evidence-1"]}
            ],
        },
        "response": {
            "status": status,
            "answer": "根据证据回答操作步骤。" if status == "success" else "无法回答。",
            "citations": [{"chunk_id": chunk_id} for chunk_id in citation_ids],
        },
        "trace": {
            "initial_results": initial,
            "final_selected_chunks": selected,
            "retrieval_config": {"metadata_filter": None},
        },
    }


def test_missing_expected_chunk_is_retrieval_failure() -> None:
    result = classify_failure(_case())
    assert result.failure_layer == "Retrieval"
    assert result.failure_category == "chunk_not_recalled"
    assert result.expected_evidence_recalled_count == 0


def test_recalled_but_not_selected_is_evidence_selection_failure() -> None:
    result = classify_failure(
        _case(initial_ids=("gold-1",), selected_ids=(), status="insufficient_evidence")
    )
    assert result.failure_layer == "Evidence Selection"
    assert result.failure_category == "evidence_threshold_too_high"


def test_selected_but_missing_citation_is_citation_failure() -> None:
    result = classify_failure(
        _case(
            initial_ids=("gold-1",),
            selected_ids=("gold-1",),
            citation_ids=(),
            status="success",
        )
    )
    assert result.failure_layer == "Citation"
    assert result.failure_category == "citation_binding_failure"


def test_builder_excludes_holdout_rows_and_summary_groups_dimensions() -> None:
    rows = build_failure_matrix(
        [_case(), _case(split="validation", question_type="table"), _case(split="holdout")],
        [],
    )
    assert len(rows) == 2
    assert {row["split"] for row in rows} == {"development", "validation"}
    summary = summarize_failure_matrix(rows)
    assert summary["analyzed_question_count"] == 2
    assert summary["holdout_rows_loaded"] is False
    assert summary["groupings"]["split"]["development"]["question_count"] == 1
    assert summary["groupings"]["question_type"]["table"]["question_count"] == 1


def test_real_matrix_is_exactly_dev_validation_and_preserves_golden_sha() -> None:
    matrix = [
        json.loads(line)
        for line in (PHASE10_DIR / "phase10b_failure_matrix.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    summary = json.loads(
        (PHASE10_DIR / "phase10b_failure_summary.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (PHASE10_DIR / "golden_set_manifest.json").read_text(encoding="utf-8")
    )
    golden_sha = hashlib.sha256(
        (PHASE10_DIR / "expanded_golden_set.jsonl").read_bytes()
    ).hexdigest()
    assert len(matrix) == 52
    assert {row["split"] for row in matrix} == {"development", "validation"}
    assert summary["split_counts"] == {"development": 36, "validation": 16}
    assert summary["holdout_rows_loaded"] is False
    assert summary["dataset_sha256"] == manifest["dataset_sha256"] == golden_sha
    assert all(row["expected_evidence"] is not None for row in matrix)
    for category in {
        "wrong_document",
        "page_not_recalled",
        "chunk_not_recalled",
        "correct_chunk_rank_too_low",
        "evidence_not_selected",
        "table_parse_failure",
        "cross_page_context_missing",
        "query_term_mismatch",
        "metadata_filter_failure",
        "evidence_threshold_too_high",
        "generation_extraction_failure",
        "citation_binding_failure",
    }:
        assert category in summary["failure_category_counts"]

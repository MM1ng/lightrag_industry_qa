from industrial_rag.coverage_funnel import build_coverage_funnel, summarize_coverage_funnel


def _row(stage: str = "covered_final_emitted"):
    return {
        "golden": {
            "question_id": "Q1",
            "split": "development",
            "expected_evidence": [{"evidence_id": "E1", "chunk_id": "c1"}],
            "expected_answer_points": [{"point_id": "P1", "text": "值", "supported_by": ["E1"]}],
        },
        "response": {
            "status": "success",
            "claims": [{"claim_id": "P1", "evidence_ids": ["E1"], "citation_ids": ["C1"]}],
            "citations": [{"citation_id": "C1", "chunk_id": "c1"}],
        },
        "trace": {
            "initial_results": [{"chunk_id": "c1"}],
            "final_selected_chunks": [{"chunk_id": "c1"}],
            "provider_evidence_ids": ["E1"],
        },
    }


def test_successful_point_is_covered_not_unknown():
    rows = build_coverage_funnel([_row()])
    assert rows[0]["final_failure_stage"] == "covered_final_emitted"
    summary = summarize_coverage_funnel(rows)
    assert summary["unknown_count"] == 0


def test_holdout_is_excluded_and_mapping_error_is_explicit():
    row = _row()
    row["golden"]["split"] = "holdout"
    assert build_coverage_funnel([row]) == []
    row = _row()
    row["golden"]["expected_evidence"][0].pop("chunk_id")
    result = build_coverage_funnel([row])[0]
    assert result["final_failure_stage"] == "evaluation_mapping_error"

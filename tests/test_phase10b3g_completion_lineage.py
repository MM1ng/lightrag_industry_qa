from industrial_rag.completion_lineage import audit_record, summarize


def _record() -> dict:
    item = {
        "chunk_id": "child-1",
        "document_id": "doc-1",
        "document_name": "manual.pdf",
        "page_number": 2,
        "generation_id": "gen-1",
        "source_type": "adjacent",
        "context_role": "context_only",
        "completion_reason": "bounded_parent_or_adjacent_context",
        "used_for_answer": False,
        "cited_in_answer": False,
    }
    return {
        "split": "development",
        "question_id": "S001",
        "response": {"request_id": "req-1", "evidence": [], "claims": []},
        "trace": {
            "trace_id": "trace-1",
            "generation_id": "gen-1",
            "completion_applied": True,
            "completed_evidence": [item],
            "answer_plan": [],
            "grounding_audit": {"point_decisions": []},
        },
    }


def test_audit_does_not_infer_unpersisted_registry_or_provider():
    row = audit_record(_record())[0]
    assert row["stages"]["registry"]["status"] == "unverifiable"
    assert row["stages"]["provider"]["status"] == "unverifiable"
    assert "not_referenced_by_answer_point" in row["drop_reasons"]


def test_generation_mismatch_is_explicit():
    record = _record()
    record["trace"]["generation_id"] = "gen-other"
    row = audit_record(record)[0]
    assert row["generation_match"] is False
    assert "generation_id_mismatch" in row["drop_reasons"]


def test_summary_counts_completion_items_and_holdout_boundary():
    rows = audit_record(_record())
    summary = summarize(rows)
    assert summary["completion_count"] == 1
    assert summary["holdout_used"] is False
    assert summary["audit_is_conservative"] is True

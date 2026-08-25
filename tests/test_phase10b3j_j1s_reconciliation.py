from __future__ import annotations


def test_linkage_preserves_a_parseable_requirement_fallback_as_a_linked_record() -> None:
    from scripts.certify_phase10b3j_j1s_reconciliation import build_linkage_record

    saved = {
        "question_id": "D014",
        "response": {
            "request_id": "request-14",
            "trace_id": "trace-14",
            "status": "success",
        },
    }
    trace = {
        "request_id": "request-14",
        "trace_id": "trace-14",
        "generation_id": "g1",
        "trace_version": "phase10a-retrieval-trace-v1",
        "structured_citation_flag": True,
        "json_mode_enabled": True,
        "source_registry_count": 3,
        "source_registry_sha256": "source-sha",
        "requirement_registry_count": 0,
        "requirement_registry_sha256": "requirement-sha",
        "provider_raw_response_sha256": "response-sha",
        "parsed_structured_output_sha256": "parsed-sha",
        "backend_generate_call_count": 1,
        "backend_second_query_called": False,
        "structured_output_valid": False,
        "structured_citation_fallback": True,
        "structured_citation_fallback_mode": "fallback_to_j0_postprocessing",
        "structured_citation_fallback_reason": "unknown_requirement_id",
    }

    record = build_linkage_record(saved, trace, expected_generation_id="g1")

    assert record["linked"] is True
    assert record["identity_mismatch"] is False
    assert record["persisted_trace_lookup_key"] == "trace_id"
    assert record["original_trace_version"] == "phase10a-retrieval-trace-v1"
    assert record["offline_contract_reconciled"] is True
    assert record["historical_trace_mutated"] is False
    assert record["structured_output_valid"] is False
    assert record["structured_citation_fallback"] is True

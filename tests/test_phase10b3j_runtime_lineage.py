import json
from pathlib import Path

from industrial_rag.retrieval_trace import (
    RUNTIME_LINEAGE_TRACE_VERSION,
    RetrievalExecutionTrace,
)


def test_runtime_lineage_fields_are_serialized_without_public_answer_fields():
    trace = RetrievalExecutionTrace(
        trace_version=RUNTIME_LINEAGE_TRACE_VERSION,
        original_query="q",
        normalized_query="q",
        retrieval_config=(),
        initial_results=(),
        rerank_applied=False,
        reranked_results=(),
        final_selected_chunks=(),
        selected_chunk_ids=(),
        normalization_ms=0,
        retrieval_ms=0,
        rerank_ms=0,
        evidence_selection_ms=0,
        provider_primary_evidence_ids=("E1",),
        provider_completed_evidence_ids=("E2",),
        provider_context_order=("E1", "E2"),
        provider_context_sha256="abc",
        provider_evidence_count=2,
        provider_context_truncated=False,
        provider_context_token_estimate=42,
        coverage_after_parent_adjacent=("P1",),
        selected_coverage=("P1",),
        generated_coverage=("P1",),
        grounding_retained_coverage=("P1",),
        grounding_answer_point_identity=("P1",),
        grounding_support_candidate_ids=({"point_id": "P1", "candidate_ids": ["E1"]},),
        grounding_retained_answer_points=("P1",),
    )
    payload = trace.to_payload()
    assert payload["trace_version"] == RUNTIME_LINEAGE_TRACE_VERSION
    assert payload["provider_context_order"] == ["E1", "E2"]
    assert payload["grounding_support_candidate_ids"][0]["point_id"] == "P1"
    assert payload["structured_citation_flag"] is False
    assert payload["json_mode_enabled"] is False
    assert payload["source_registry_count"] == 0
    assert payload["requirement_registry_count"] == 0
    assert payload["structured_output_valid"] is False
    assert payload["structured_citation_fallback"] is False
    assert payload["backend_generate_call_count"] == 0
    # Runtime lineage payload itself has no public answer fields; the admin
    # diagnostics adapter is responsible for exposing it, not QueryResponse.
    assert "answer" not in payload
    assert "citations" not in payload
    assert "claims" not in payload


def test_metric_reconciliation_preserves_invalid_requested_equation_as_audit_fact():
    path = Path("evaluation/phase10b3j/metric_unit_reconciliation.json")
    artifact = json.loads(path.read_text(encoding="utf-8"))
    reconciliation = artifact["reconciliation"]
    assert reconciliation["expected_point_count"] == 39
    assert reconciliation["disjoint_partition_value"] == 39
    assert reconciliation["requested_equation_value"] == 36
    assert reconciliation["requested_equation_valid"] is False
    assert artifact["units"]["question"]["count"] == 36
    assert artifact["units"]["claim"]["identity_resolution_denominator"] == 198

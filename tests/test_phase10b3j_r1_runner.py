from __future__ import annotations

from pathlib import Path


def test_j0_runner_uses_candidate_database_without_changing_active_pointer(monkeypatch, tmp_path: Path) -> None:
    from scripts.run_phase10b3j_r1_j0 import load_runtime_env

    env_file = tmp_path / ".env.local_staging"
    env_file.write_text(
        "DATABASE_URL=sqlite+aiosqlite:///staging.db\n"
        "SERVICE_API_KEY=service-test\n"
        "ADMIN_API_KEY=admin-test\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("QA_CLAIM_CITATION_PRUNING_ENABLED", "true")

    env = load_runtime_env(env_file, tmp_path / "candidate.db")

    assert env["DATABASE_URL"] == "sqlite+aiosqlite:///" + str(tmp_path / "candidate.db")
    assert env["QA_CLAIM_CITATION_PRUNING_ENABLED"] == "false"
    assert env["QA_COVERAGE_AWARE_SELECTION_ENABLED"] == "false"
    assert env["QA_GROUNDING_FALSE_NEGATIVE_RECOVERY_ENABLED"] == "false"
    assert env["QA_PARTIAL_GENERATION_ENABLED"] == "false"
    assert env["QA_SUPPORT_VALIDATOR_V2_ENABLED"] == "false"
    assert env["QA_STRUCTURED_GENERATION_ENABLED"] == "false"


def test_admin_trace_contract_preserves_runtime_lineage_fields() -> None:
    from industrial_rag.routers.admin_diagnostics import RetrievalTraceResponse

    payload = {
        "request_id": "r",
        "trace_id": "t",
        "trace_version": "phase10b3j-runtime-lineage-v2",
        "knowledge_base_id": "kb",
        "generation_id": "g",
        "generation_epoch": 0,
        "original_query": "q",
        "normalized_query": "q",
        "retrieval_config": {},
        "initial_results": [],
        "rerank_applied": False,
        "reranked_results": [],
        "final_selected_chunks": [],
        "normalization_ms": 0,
        "retrieval_ms": 0,
        "rerank_ms": 0,
        "evidence_selection_ms": 0,
        "end_to_end_ms": 0,
        "created_at": "now",
        "expires_at": "later",
        "provider_evidence_ids": ["E1"],
        "provider_context_order": ["c1"],
        "provider_context_sha256": "hash",
        "provider_evidence_count": 1,
        "provider_context_truncated": False,
        "provider_context_token_estimate": 4,
        "backend_second_query_called": False,
        "coverage_before": ["req-1"],
        "coverage_after_parent_adjacent": ["req-1"],
        "grounding_removal_reasons": [],
    }

    trace = RetrievalTraceResponse.model_validate(payload)

    assert trace.provider_context_sha256 == "hash"
    assert trace.coverage_before == ["req-1"]
    assert trace.coverage_after_parent_adjacent == ["req-1"]


def test_legacy_trace_payload_accepts_additive_conversation_fields() -> None:
    from industrial_rag.routers.admin_diagnostics import RetrievalTraceResponse

    payload = {
        "request_id": "r",
        "trace_id": "t",
        "trace_version": "phase10a-retrieval-trace-v1",
        "knowledge_base_id": "kb",
        "generation_id": "g",
        "generation_epoch": 0,
        "original_query": "q",
        "normalized_query": "q",
        "retrieval_config": {},
        "initial_results": [],
        "rerank_applied": False,
        "reranked_results": [],
        "final_selected_chunks": [],
        "normalization_ms": 0,
        "retrieval_ms": 0,
        "rerank_ms": 0,
        "evidence_selection_ms": 0,
        "end_to_end_ms": 0,
        "created_at": "now",
        "expires_at": "later",
    }

    trace = RetrievalTraceResponse.model_validate(payload)

    assert trace.rewrite_status == "unchanged"
    assert trace.retrieval_query is None
    assert trace.rewrite_failure_reason is None


def test_admin_trace_exposes_structured_citation_audit_fields() -> None:
    """The admin-only projection must preserve J1S audit fields already in Trace."""

    from industrial_rag.routers.admin_diagnostics import RetrievalTraceResponse

    payload = {
        "request_id": "r",
        "trace_id": "t",
        "trace_version": "phase10a-retrieval-trace-v1",
        "knowledge_base_id": "kb",
        "generation_id": "g",
        "generation_epoch": 0,
        "original_query": "q",
        "normalized_query": "q",
        "retrieval_config": {},
        "initial_results": [],
        "rerank_applied": False,
        "reranked_results": [],
        "final_selected_chunks": [],
        "normalization_ms": 0,
        "retrieval_ms": 0,
        "rerank_ms": 0,
        "evidence_selection_ms": 0,
        "end_to_end_ms": 0,
        "created_at": "now",
        "expires_at": "later",
        "structured_citation_flag": True,
        "json_mode_enabled": True,
        "source_registry_count": 2,
        "source_registry_sha256": "source-sha",
        "requirement_registry_count": 1,
        "requirement_registry_sha256": "requirement-sha",
        "provider_raw_response_sha256": "raw-sha",
        "parsed_structured_output_sha256": "parsed-sha",
        "structured_output_valid": True,
        "structured_citation_fallback": False,
        "structured_citation_fallback_mode": None,
        "structured_citation_fallback_reason": None,
        "backend_generate_call_count": 1,
        "backend_second_query_called": False,
    }

    trace = RetrievalTraceResponse.model_validate(payload)

    assert trace.structured_citation_flag is True
    assert trace.source_registry_sha256 == "source-sha"
    assert trace.requirement_registry_count == 1
    assert trace.provider_raw_response_sha256 == "raw-sha"
    assert trace.parsed_structured_output_sha256 == "parsed-sha"
    assert trace.backend_generate_call_count == 1

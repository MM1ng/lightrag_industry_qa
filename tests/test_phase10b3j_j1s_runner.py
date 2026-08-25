from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter

import pytest
from scripts.run_phase10b3j_j1s import (
    _load_development,
    assert_j1s_evaluation_order,
    assert_preflight_allows_development,
    assert_preflight_config_sha,
    ensure_src_on_path,
    evaluate_j1s_development,
    j1s_environment,
    load_persisted_trace,
    load_persisted_trace_by_trace_id,
    reconcile_development_from_persisted_traces,
    reconcile_j1s_result,
    validate_j1s_trace,
)


def _case(*, valid: bool = True) -> dict[str, object]:
    return {
        "http_status": 200,
        "backend_generate_call_count": 1,
        "structured_citation_flag": True,
        "json_mode_enabled": True,
        "source_registry_present": True,
        "source_registry_identity_resolved": True,
        "candidate_generation_correct": True,
        "backend_second_query_called": False,
        "active_pointer_changed": False,
        "structured_output_valid": valid,
    }


def test_development_is_rejected_without_three_passing_preflight_cases() -> None:
    with pytest.raises(RuntimeError, match="J1S-1 preflight"):
        assert_preflight_allows_development((_case(), _case()))


def test_development_is_rejected_when_a_preflight_contract_field_fails() -> None:
    failed = _case()
    failed["backend_generate_call_count"] = 2

    with pytest.raises(RuntimeError, match="backend_generate_call_count"):
        assert_preflight_allows_development((_case(), _case(), failed))


def test_j1s_environment_enables_only_structured_citation_output() -> None:
    values = j1s_environment({"SERVICE_API_KEY": "not-a-secret"})

    assert values["QA_STRUCTURED_CITATION_OUTPUT_ENABLED"] == "true"
    assert values["QA_SUPPLEMENTAL_RETRIEVAL_ENABLED"] == "false"
    assert values["QA_CLAIM_CITATION_PRUNING_ENABLED"] == "false"
    assert values["QA_GROUNDING_FALSE_NEGATIVE_RECOVERY_ENABLED"] == "false"
    assert values["QA_COVERAGE_AWARE_SELECTION_ENABLED"] == "false"
    assert values["QA_PARTIAL_GENERATION_ENABLED"] == "false"
    assert values["QA_GROUNDING_AUDIT_ENABLED"] == "true"


def test_development_is_rejected_when_j1s2_has_not_passed() -> None:
    with pytest.raises(RuntimeError, match="J1S-2"):
        assert_j1s_evaluation_order(
            j1s0_passed=True,
            j1s1_cases=(_case(), _case(), _case()),
            j1s2_passed=False,
        )


def test_development_loader_rejects_a_non_development_source_row(tmp_path) -> None:
    source = tmp_path / "development.jsonl"
    source.write_text(
        "\n".join(
            (
                json.dumps({"split": "development", "question_id": "D001"}),
                json.dumps({"split": "validation", "question_id": "V001"}),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="non-Development"):
        _load_development(source)


def test_runner_adds_project_src_before_direct_script_import(monkeypatch) -> None:
    monkeypatch.setattr(sys, "path", [])

    ensure_src_on_path()

    assert any(path.replace("\\", "/").endswith("/src") for path in sys.path)


def test_legacy_trace_version_is_accepted_only_with_complete_j1s_audit_fields() -> None:
    trace = {
        "trace_version": "phase10a-retrieval-trace-v1",
        "trace_id": "trace-1",
        "generation_id": "g1",
        "structured_citation_flag": True,
        "json_mode_enabled": True,
        "source_registry_count": 3,
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

    assert validate_j1s_trace(trace, expected_generation_id="g1") == ()


def test_complete_trace_accepts_a_recorded_deterministic_fallback() -> None:
    trace = {
        "trace_version": "phase10a-retrieval-trace-v1",
        "generation_id": "g1",
        "structured_citation_flag": True,
        "json_mode_enabled": True,
        "source_registry_count": 3,
        "source_registry_sha256": "source-sha",
        "requirement_registry_count": 0,
        "requirement_registry_sha256": "requirement-sha",
        "provider_raw_response_sha256": "raw-sha",
        "parsed_structured_output_sha256": "parsed-sha",
        "structured_output_valid": False,
        "structured_citation_fallback": True,
        "structured_citation_fallback_mode": "fallback_to_j0_postprocessing",
        "structured_citation_fallback_reason": "unknown_requirement_id",
        "backend_generate_call_count": 1,
        "backend_second_query_called": False,
    }

    assert validate_j1s_trace(trace, expected_generation_id="g1") == ()


def test_saved_response_is_marked_completed_when_its_persisted_trace_is_valid() -> None:
    trace = {
        "trace_version": "phase10a-retrieval-trace-v1",
        "generation_id": "g1",
        "structured_citation_flag": True,
        "json_mode_enabled": True,
        "source_registry_count": 3,
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
    saved = {
        "question_id": "D001",
        "response": {"request_id": "request-1", "generation_id": "g1"},
        "execution_status": "trace_contract_failed",
    }

    reconciled = reconcile_j1s_result(
        saved, persisted_trace=trace, expected_generation_id="g1"
    )

    assert reconciled["execution_status"] == "completed"
    assert reconciled["trace"] == trace
    assert reconciled["trace_reconciled_from_persisted_record"] is True


def test_persisted_trace_loader_reads_only_the_requested_immutable_payload(tmp_path) -> None:
    database = tmp_path / "candidate.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE retrieval_traces (request_id TEXT PRIMARY KEY, payload JSON NOT NULL)"
    )
    connection.execute(
        "INSERT INTO retrieval_traces (request_id, payload) VALUES (?, ?)",
        ("request-1", json.dumps({"request_id": "request-1", "trace_version": "v"})),
    )
    connection.commit()
    connection.close()

    trace = load_persisted_trace(database, "request-1")

    assert trace == {"request_id": "request-1", "trace_version": "v"}


def test_reconciliation_reuses_saved_responses_without_a_query_or_generation(tmp_path) -> None:
    database = tmp_path / "candidate.db"
    trace = {
        "trace_version": "phase10a-retrieval-trace-v1",
        "trace_id": "trace-1",
        "generation_id": "g1",
        "structured_citation_flag": True,
        "json_mode_enabled": True,
        "source_registry_count": 1,
        "source_registry_sha256": "source-sha",
        "requirement_registry_count": 0,
        "requirement_registry_sha256": "requirements-sha",
        "provider_raw_response_sha256": "raw-sha",
        "parsed_structured_output_sha256": "parsed-sha",
        "structured_output_valid": True,
        "structured_citation_fallback": False,
        "structured_citation_fallback_mode": None,
        "structured_citation_fallback_reason": None,
        "backend_generate_call_count": 1,
        "backend_second_query_called": False,
    }
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE retrieval_traces (request_id TEXT PRIMARY KEY, trace_id TEXT, payload JSON NOT NULL)"
    )
    connection.execute(
        "INSERT INTO retrieval_traces (request_id, trace_id, payload) VALUES (?, ?, ?)",
        ("request-1", "trace-1", json.dumps(trace)),
    )
    connection.commit()
    connection.close()
    saved_path = tmp_path / "saved.jsonl"
    saved_path.write_text(
        json.dumps(
            {
                "question_id": "D001",
                "response": {
                    "request_id": "request-1",
                    "trace_id": "trace-1",
                    "generation_id": "g1",
                },
                "execution_status": "trace_contract_failed",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "reconciled.jsonl"

    summary = reconcile_development_from_persisted_traces(
        saved_path=saved_path,
        candidate_db=database,
        output_path=output_path,
        expected_generation_id="g1",
    )

    row = json.loads(output_path.read_text(encoding="utf-8"))
    assert summary == {"questions": 1, "completed": 1, "reconciled_from_persisted_traces": 1}
    assert row["execution_status"] == "completed"
    assert row["response"]["request_id"] == "request-1"


def test_future_preflight_rejects_different_feature_flag_configuration() -> None:
    cases = [_case(), _case(), _case()]
    for case in cases:
        case["feature_flag_config_sha256"] = "same-config"
    cases[-1]["feature_flag_config_sha256"] = "different-config"

    with pytest.raises(RuntimeError, match="configuration SHA"):
        assert_preflight_config_sha(cases)


def test_trace_loader_uses_response_trace_id_to_read_the_persisted_record(tmp_path) -> None:
    database = tmp_path / "candidate.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE retrieval_traces (request_id TEXT PRIMARY KEY, trace_id TEXT, payload JSON NOT NULL)"
    )
    connection.execute(
        "INSERT INTO retrieval_traces (request_id, trace_id, payload) VALUES (?, ?, ?)",
        ("request-1", "trace-1", json.dumps({"request_id": "request-1", "trace_id": "trace-1"})),
    )
    connection.commit()
    connection.close()

    trace = load_persisted_trace_by_trace_id(database, "trace-1")

    assert trace == {"request_id": "request-1", "trace_id": "trace-1"}


def test_development_evaluator_scores_saved_citation_identity_without_model_calls() -> None:
    rows = [
        {
            "question_id": "D001",
            "response": {
                "status": "success",
                "generation_id": "g1",
                "request_id": "request-1",
                "citations": [{"citation_id": "cite-1", "chunk_id": "child-1"}],
                "claims": [{"claim_id": "P1", "citation_ids": ["cite-1"]}],
            },
            "golden": {
                "answerable": True,
                "expected_answer_points": [{"point_id": "D001-p1", "supported_by": ["D001-e1"]}],
            },
            "trace": {
                "structured_output_valid": True,
                "structured_citation_fallback": False,
                "generation_id": "g1",
            },
        }
    ]

    metrics = evaluate_j1s_development(
        rows,
        expected_chunk_by_evidence={("D001", "D001-e1"): "child-1"},
        expected_generation_id="g1",
    )

    assert metrics["status_counts"] == Counter({"success": 1})
    assert metrics["supporting_citation_recall"] == {"numerator": 1, "denominator": 1, "value": 1.0}
    assert metrics["expected_answer_point_coverage"] == {"numerator": 1, "denominator": 1, "value": 1.0}
    assert metrics["citation_precision"] == {"numerator": 1.0, "denominator": 1, "value": 1.0}
    assert metrics["wrong_generation_count"] == 0

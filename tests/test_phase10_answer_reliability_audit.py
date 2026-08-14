from __future__ import annotations

import pytest
from evaluation.phase10.conversation_answer_reliability_audit import (
    EXPECTED_SNAPSHOT_SHA256,
    TAXONOMY,
    AuditBlocked,
    _transition,
    build_audit,
    load_and_verify_snapshot,
)


def test_frozen_snapshot_sha_and_18_case_contract() -> None:
    cases, manifest = load_and_verify_snapshot()
    assert manifest["snapshot_sha256"] == EXPECTED_SNAPSHOT_SHA256
    assert len(cases) == 18
    assert manifest["ordered_case_ids"] == [case["case_id"] for case in cases]


def test_audit_has_zero_lightrag_calls_and_complete_candidate_point_rows() -> None:
    report, rows = build_audit()
    assert report["light_rag_service_calls"] == 0
    assert report["candidate"]["unsupported_cases"] == 14
    assert report["candidate"]["unsupported_answer_point_count"] == len(rows)
    assert all(row["failure_classification"] in TAXONOMY for row in rows)
    assert all(row["failure_classification"] for row in rows)


def test_case_and_point_denominators_and_transitions() -> None:
    cases, _ = load_and_verify_snapshot()
    assert _transition(cases) == {
        "unsupported -> supported": 3,
        "supported -> unsupported": 2,
        "unsupported -> unsupported": 12,
        "supported -> supported": 1,
    }


def test_audit_report_corrects_judge_error_semantics() -> None:
    report, _ = build_audit()
    semantics = report["faithfulness_reporting"]
    assert semantics["formal_successes"] == 36
    assert semantics["formal_errors"] == 0
    assert semantics["response_relevancy_preflight_errors"] == 2
    assert semantics["response_relevancy_formal_status"] == "NOT_RUN"
    assert semantics["diagnostic_errors"] == 1


def test_snapshot_contract_blocks_checksum_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    import evaluation.phase10.conversation_answer_reliability_audit as audit

    monkeypatch.setattr(audit, "EXPECTED_SNAPSHOT_SHA256", "wrong")
    with pytest.raises(AuditBlocked):
        audit.load_and_verify_snapshot()

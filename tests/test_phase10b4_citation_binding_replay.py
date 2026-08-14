from __future__ import annotations

from evaluation.phase10.citation_binding_correction_replay import build_replay


def test_frozen_replay_reproduces_r4a_before_and_removes_only_binding_points() -> None:
    report, _ = build_replay()

    assert report["before"]["total_unsupported_points"] == 26
    assert report["before"]["citation_binding_error_points"] == 18
    assert report["after"]["citation_binding_error_points"] == 0
    assert report["after"]["total_unsupported_points"] == 8
    assert report["after"]["supported_semantic_points"] >= report["before"]["supported_semantic_points"]


def test_replay_has_no_runtime_calls_or_citation_fanout() -> None:
    report, _ = build_replay()

    assert report["light_rag_service_calls"] == 0
    assert report["llm_calls"] == 0
    assert report["retrieval_calls"] == 0
    assert report["validation_holdout_accessed"] is False
    assert report["citation_fanout"] is False
    assert report["semantic_point_deleted"] is False
    assert report["structured_citation_output_enabled_before"] is False
    assert report["structured_citation_output_enabled_after"] is False


def test_replay_keeps_citation_count_and_removes_internal_markers() -> None:
    report, _ = build_replay()

    assert report["after"]["citation_count"] == report["before"]["citation_count"]
    assert all(not diff["internal_source_marker_leaked"] for diff in report["case_diffs"])
    assert all(diff["citation_binding_unchanged"] for diff in report["case_diffs"])

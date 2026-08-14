from __future__ import annotations

import json
from pathlib import Path

import pytest
from evaluation.phase10.conversation_e2e_contracts import JudgeConfig, fingerprint_dataset
from evaluation.phase10.conversation_e2e_runner import (
    SnapshotValidationError,
    atomic_write_text,
    build_blocked_report,
    build_report,
    build_runtime_snapshot,
    build_runtime_snapshot_from_report,
    load_runtime_snapshot,
    render_markdown_report,
    resolve_runtime_cases,
    write_artifacts,
    write_runtime_snapshot,
    write_semantic_scores,
)


def test_blocked_report_contains_fingerprint_but_no_fabricated_metrics() -> None:
    fingerprint = fingerprint_dataset(Path("data/evaluation/conversation_retrieval_development.jsonl"))
    report = build_blocked_report(fingerprint, "judge_unavailable", "semantic judge could not execute")

    assert report["status"] == "BLOCKED"
    assert report["dataset_fingerprint"]["case_count"] == 18
    assert "baseline" not in report
    assert "candidate" not in report


def test_blocked_report_writes_a_non_empty_round_trippable_json_artifact(tmp_path: Path) -> None:
    fingerprint = fingerprint_dataset(Path("data/evaluation/conversation_retrieval_development.jsonl"))
    report = build_blocked_report(fingerprint, "judge_unavailable", "semantic judge could not execute")
    json_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"

    write_artifacts(report, json_path, markdown_path)

    assert json_path.stat().st_size > 0
    assert json.loads(json_path.read_text(encoding="utf-8"))["status"] == "BLOCKED"
    for field in (
        "reason_code",
        "reason",
        "dataset_fingerprint",
        "case_count",
        "runtime_config_fingerprint",
        "judge_config",
        "ragas_version",
        "semantic_execution",
        "judge_errors",
        "created_at",
    ):
        assert field in report


def test_atomic_write_failure_preserves_existing_canonical_artifact(tmp_path: Path, monkeypatch) -> None:
    artifact = tmp_path / "report.json"
    artifact.write_text('{"status":"prior"}\n', encoding="utf-8")

    def fail_replace(self: Path, target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)

    try:
        atomic_write_text(artifact, '{"status":"new"}\n')
    except OSError:
        pass
    else:
        raise AssertionError("expected atomic replace to fail")

    assert json.loads(artifact.read_text(encoding="utf-8"))["status"] == "prior"
    assert not list(tmp_path.glob(".report.json.*.tmp"))


def _snapshot_case(case_id: str = "conv-s001") -> dict:
    arm = {
        "runtime_query": "query",
        "retrieved_chunk_ids": ["g1"],
        "retrieved_ranks": {"1": "g1"},
        "selected_evidence_ids": ["g1"],
        "provider_evidence_ids": ["g1"],
        "provider_context_ids": ["g1"],
        "provider_context_hash": "hash",
        "provider_contexts": ["actual provider context"],
        "evaluation_user_input": "equipment procedure?",
        "answer": "answer",
        "answer_status": "success",
        "citations": [],
        "answer_points": [],
        "grounding_removed_points": [],
        "grounding_failure_categories": [],
        "latency_ms": 1.0,
        "metric_error": None,
    }
    return {
        "case_id": case_id,
        "history": [],
        "dependent_query": "it?",
        "standalone_query": "equipment procedure?",
        "gold_chunk_ids": ["g1"],
        "rewrite": {"status": "rewritten"},
        "baseline": arm,
        "candidate": {**arm, "runtime_query": "equipment procedure?"},
    }


def test_runtime_snapshot_round_trip_preserves_actual_provider_contexts_and_checksum(tmp_path: Path) -> None:
    fingerprint = fingerprint_dataset(Path("data/evaluation/conversation_retrieval_development.jsonl"))
    cases = [_snapshot_case(case_id) for case_id in fingerprint.case_ids]
    snapshot = build_runtime_snapshot(cases, fingerprint, {"sha256": "runtime"})
    path = tmp_path / "runtime.jsonl"

    write_runtime_snapshot(snapshot, path)
    loaded, manifest = load_runtime_snapshot(path, fingerprint, {"sha256": "runtime"})

    assert len(loaded) == 18
    assert manifest["snapshot_sha256"] == snapshot["manifest"]["snapshot_sha256"]
    assert loaded[0]["baseline"]["provider_contexts"] == ["actual provider context"]


def test_runtime_snapshot_rejects_missing_context_or_fingerprint_mismatch(tmp_path: Path) -> None:
    fingerprint = fingerprint_dataset(Path("data/evaluation/conversation_retrieval_development.jsonl"))
    cases = [_snapshot_case(case_id) for case_id in fingerprint.case_ids]
    cases[0]["candidate"]["provider_contexts"] = []
    path = tmp_path / "runtime.jsonl"
    write_runtime_snapshot(build_runtime_snapshot(cases, fingerprint, {"sha256": "runtime"}), path)

    try:
        load_runtime_snapshot(path, fingerprint, {"sha256": "runtime"})
    except SnapshotValidationError as error:
        assert error.reason_code == "snapshot_missing_provider_contexts"
    else:
        raise AssertionError("expected snapshot validation to block")


def test_runtime_snapshot_rejects_unexpected_canonical_sha(tmp_path: Path) -> None:
    fingerprint = fingerprint_dataset(Path("data/evaluation/conversation_retrieval_development.jsonl"))
    cases = [_snapshot_case(case_id) for case_id in fingerprint.case_ids]
    path = tmp_path / "runtime.jsonl"
    write_runtime_snapshot(build_runtime_snapshot(cases, fingerprint, {"sha256": "runtime"}), path)

    try:
        load_runtime_snapshot(path, fingerprint, {"sha256": "runtime"}, expected_snapshot_sha256="wrong")
    except SnapshotValidationError as error:
        assert error.reason_code == "snapshot_checksum_mismatch"
    else:
        raise AssertionError("expected canonical snapshot SHA validation to block")


def test_semantic_scores_write_one_atomic_jsonl_record_per_case(tmp_path: Path) -> None:
    path = tmp_path / "semantic.jsonl"
    write_semantic_scores([{"case_id": "conv-s001", "baseline": {"faithfulness": 0.8}, "candidate": {"faithfulness": 0.9}}], path)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "case_id": "conv-s001",
        "baseline": {"faithfulness": 0.8},
        "candidate": {"faithfulness": 0.9},
    }


@pytest.mark.asyncio
async def test_valid_runtime_snapshot_resume_makes_zero_light_rag_calls() -> None:
    class ExplodingService:
        async def query(self, *_args, **_kwargs):
            raise AssertionError("snapshot resume must not call LightRAGService.query")

    rows = [_snapshot_case("conv-s001")]
    resolved = await resolve_runtime_cases(
        ExplodingService(),
        rows,
        mode="naive",
        top_k=12,
        chunk_top_k=20,
        frozen_cases=rows,
    )

    assert resolved == rows


def test_existing_real_report_can_backfill_a_canonical_runtime_snapshot() -> None:
    fingerprint = fingerprint_dataset(Path("data/evaluation/conversation_retrieval_development.jsonl"))
    cases = [_snapshot_case(case_id) for case_id in fingerprint.case_ids]
    snapshot = build_runtime_snapshot_from_report(
        {
            "case_count": 18,
            "dataset_fingerprint": fingerprint.to_dict(),
            "runtime_config_fingerprint": {"sha256": "runtime"},
            "cases": cases,
        },
        fingerprint,
        {"sha256": "runtime"},
    )

    assert snapshot["manifest"]["case_count"] == 18
    assert snapshot["cases"] == cases


def test_report_preserves_case_order_and_markdown_contains_required_summary() -> None:
    fingerprint = fingerprint_dataset(Path("data/evaluation/conversation_retrieval_development.jsonl"))
    rows = [{
        "case_id": "conv-s001",
        "gold_chunk_ids": ["g1"],
        "baseline": {"retrieved_chunk_ids": ["g1"]},
        "candidate": {"retrieved_chunk_ids": ["g1"]},
    }]
    report = build_report(
        cases=rows,
        fingerprint=fingerprint,
        runtime_fingerprint={"sha256": "runtime"},
        judge_config=JudgeConfig("0.3.9", "Faithfulness", "ResponseRelevancy", "fake", "j", "fake", "e", 0.0, None, 60, 2, 1),
        semantic_rows=[{"case_id": "conv-s001", "baseline": {"faithfulness": None, "response_relevancy": None, "judge_error": "offline"}, "candidate": {"faithfulness": None, "response_relevancy": None, "judge_error": "offline"}}],
        experiment_artifact="evaluation/ragas/experiments/row.jsonl",
    )

    assert report["dataset_fingerprint"]["case_ids"] == ["conv-s001"]
    assert report["case_count"] == 1
    assert report["judge_errors"] == 2
    assert report["status"] == "BLOCKED"
    markdown = render_markdown_report(report)
    assert "Faithfulness" in markdown
    assert "Response Relevancy" in markdown
    assert "BLOCKED" in markdown or "R3_" in markdown
    json.dumps(report, ensure_ascii=False)


def test_markdown_records_snapshot_preflight_and_prior_blocked_provenance() -> None:
    report = {
        "status": "BLOCKED",
        "ragas_version": "0.3.9",
        "case_count": 18,
        "dataset_fingerprint": {},
        "judge_config": {},
        "baseline": {},
        "candidate": {},
        "semantic": {},
        "semantic_execution": {"status": "BLOCKED"},
        "paired_case_counts": {},
        "judge_errors": 1,
        "failure_layer_distribution": {},
        "previous_blocked_commit": "dbaf649e6fd59f710def1e99aa46a93cc514484f",
        "runtime_snapshot": {"snapshot_sha256": "snapshot"},
        "semantic_preflight": {"status": "BLOCKED", "components": {"chat": {"status": "BLOCKED"}}},
        "implementation_audit": {"legacy_report_json": "valid and non-empty", "gate_integration": "build_report calls evaluate_gate"},
    }

    markdown = render_markdown_report(report)

    assert "Previous blocked commit" in markdown
    assert "Runtime snapshot" in markdown
    assert "Semantic preflight" in markdown
    assert "Implementation audit" in markdown


def test_report_marks_semantic_smoke_block_without_fabricating_case_scores() -> None:
    fingerprint = fingerprint_dataset(Path("data/evaluation/conversation_retrieval_development.jsonl"))
    report = build_report(
        cases=[],
        fingerprint=fingerprint,
        runtime_fingerprint={"sha256": "runtime"},
        judge_config=JudgeConfig("0.3.9", "Faithfulness", "ResponseRelevancy", "fake", "j", "fake", "e", 0.0, None, 60, 2, 1),
        semantic_rows=[],
        experiment_artifact="evaluation/ragas/experiments/row.jsonl",
        semantic_blocked_reason="judge smoke returned provider HTTP 500",
    )

    assert report["status"] == "BLOCKED"
    assert report["semantic_execution"] == {
        "status": "BLOCKED",
        "reason": "judge smoke returned provider HTTP 500",
        "formal_case_scoring_executed": False,
    }
    assert report["semantic_cases"] == []
    assert report["judge_errors"] == 1


def test_report_uses_real_gate_and_never_auto_passes_when_mandatory_gold_is_unavailable() -> None:
    fingerprint = fingerprint_dataset(Path("data/evaluation/conversation_retrieval_development.jsonl"))
    rows = [{
        "case_id": "conv-s001",
        "gold_chunk_ids": ["g1"],
        "baseline": {"retrieved_chunk_ids": ["noise"], "answer_status": "success", "citations": [], "answer_points": []},
        "candidate": {"retrieved_chunk_ids": ["g1"], "answer_status": "success", "citations": [], "answer_points": []},
    }]
    report = build_report(
        cases=rows,
        fingerprint=fingerprint,
        runtime_fingerprint={"sha256": "runtime"},
        judge_config=JudgeConfig("0.3.9", "Faithfulness", "ResponseRelevancy", "fake", "j", "fake", "e", 0.0, None, 60, 2, 1),
        semantic_rows=[{
            "case_id": "conv-s001",
            "baseline": {"faithfulness": 0.7, "response_relevancy": 0.7, "judge_error": None},
            "candidate": {"faithfulness": 0.8, "response_relevancy": 0.8, "judge_error": None},
        }],
        experiment_artifact="evaluation/ragas/experiments/row.jsonl",
    )

    assert report["status"] == "R3_MIXED"
    assert "supporting_recall_unavailable" in report["gate"]["reasons"]

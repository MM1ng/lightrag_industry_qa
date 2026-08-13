from __future__ import annotations

import json

from scripts.phase12b3a_r2_protocol_replay import (
    R1_ROWS,
    classify_protocol_gate,
    load_jsonl,
    matrix_fingerprint,
    run_r2,
    run_protocol_replay,
)


def _row() -> dict:
    return {
        "split": "development",
        "question_id": "S001",
        "response": {
            "status": "success",
            "claims": [{"claim_id": "c1", "text": "结论"}],
            "evidence": [
                {"evidence_id": "e1", "chunk_id": "chunk-1", "excerpt": "证据一"},
                {"evidence_id": "e2", "chunk_id": "chunk-2", "excerpt": "证据二"},
            ],
            "citations": [],
        },
    }


def _valid_response(_: str) -> str:
    return '{"claims":[{"claim_id":"c1","supported":["e1"],"partially_supported":[],"not_supported":[],"uncertain":["e2"]}]}'


def test_protocol_replay_calls_each_batch_once_and_preserves_matrix(tmp_path) -> None:
    rows = [_row()]
    before = matrix_fingerprint(rows)
    calls: list[str] = []

    def judge(prompt: str) -> str:
        calls.append(prompt)
        return _valid_response(prompt)

    report = run_protocol_replay(judge=judge, rows=rows, output_dir=tmp_path)

    assert len(calls) == 1
    assert report["candidate_matrix_fingerprint"] == before
    assert report["protocol_gate"]["pass"] is True
    assert report["protocol_gate"]["valid_batch_rate"] == 1.0
    assert report["protocol_gate"]["valid_pair_coverage"] == 1.0
    saved = json.loads((tmp_path / "raw_judge_responses.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert saved["raw_response"]
    assert saved["expected_pair_count"] == 2


def test_protocol_replay_does_not_retry_invalid_response(tmp_path) -> None:
    calls: list[str] = []

    def judge(prompt: str) -> str:
        calls.append(prompt)
        return '{"claims":[]}'

    report = run_protocol_replay(judge=judge, rows=[_row()], output_dir=tmp_path)

    assert len(calls) == 1
    assert report["protocol_gate"]["valid_batch_rate"] == 0.0
    assert report["protocol_gate"]["pass"] is False
    assert report["invalid_batches"] == 1


def test_protocol_gate_uses_both_ninety_five_percent_thresholds() -> None:
    passing = classify_protocol_gate(total_calls=20, valid_batches=19, expected_pairs=100, valid_pairs=95)
    failing_batches = classify_protocol_gate(total_calls=20, valid_batches=18, expected_pairs=100, valid_pairs=100)
    failing_pairs = classify_protocol_gate(total_calls=20, valid_batches=20, expected_pairs=100, valid_pairs=94)

    assert passing["pass"] is True
    assert failing_batches["pass"] is False
    assert failing_pairs["pass"] is False


def test_protocol_failure_stops_before_semantic_scoring(tmp_path) -> None:
    result = run_r2(
        judge=lambda _: '{"claims":[]}',
        rows=[_row()],
        output_dir=tmp_path,
        run_historical_audit=False,
    )

    assert result["status"] == "OUTPUT_CONTRACT_FAIL"
    assert result["semantic_quality_metrics_computed"] is False
    assert not (tmp_path / "semantic_judge_metrics.json").exists()


def test_r2_uses_the_exact_r1_matrix_counts() -> None:
    rows = load_jsonl(R1_ROWS)
    assert len(rows) == 36
    assert sum(len((row.get("response") or {}).get("evidence", [])) for row in rows) == 101
    assert sum(
        len((row.get("response") or {}).get("claims", []))
        * len((row.get("response") or {}).get("evidence", []))
        for row in rows
    ) == 566


def test_r2_does_not_pass_evaluation_labels_to_judge_input(tmp_path) -> None:
    row = _row()
    row["response"]["evidence"][0]["supporting_actual_chunk_ids"] = ["chunk-1"]
    calls: list[str] = []
    result = run_protocol_replay(
        judge=lambda prompt: calls.append(prompt) or _valid_response(prompt),
        rows=[row],
        output_dir=tmp_path,
    )

    assert calls == []
    assert result["input_errors"] == 1
    assert result["protocol_gate"]["pass"] is False

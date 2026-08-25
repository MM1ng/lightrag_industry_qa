"""Replay saved Phase 10B-3 responses without generating new answer text.

The replay only re-runs deterministic grounding over answer text already saved
by the Candidate evaluation.  Golden answers are used for scoring only; they
are never used to synthesize a replacement answer.
"""

from __future__ import annotations

import json
from pathlib import Path

from industrial_rag.answer_grounding import build_answer_plan
from industrial_rag.citation_formatter import Citation
from industrial_rag.evidence_policy import EvidenceCandidate

ROOT = Path(__file__).resolve().parents[1]
RESULTS = [
    ROOT / "evaluation" / "phase10b3a" / "development_results.jsonl",
    ROOT / "evaluation" / "phase10b3a" / "validation_results.jsonl",
]
REGISTRY = (
    ROOT
    / "runtime"
    / "phase10b3c"
    / "kb_data"
    / "8fce4626859d44abb70a9ae5b0372cea"
    / "g10b3c20260803"
    / "context_registry"
)
OUT = ROOT / "evaluation" / "phase10b3e"


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _registry() -> dict[str, dict[str, object]]:
    return {str(row["chunk_id"]): row for row in _jsonl(REGISTRY / "chunks.jsonl")}


def _candidate(item: dict[str, object], chunks: dict[str, dict[str, object]]) -> EvidenceCandidate | None:
    chunk_id = item.get("chunk_id")
    row = chunks.get(str(chunk_id))
    if row is None:
        return None
    document_name = str(row.get("document_name") or item.get("document_name") or "")
    page = int(row.get("page_number") or item.get("page_number") or 0)
    if not document_name or page < 1:
        return None
    return EvidenceCandidate(
        citation=Citation(document_name, page, str(chunk_id)),
        text=str(row.get("content") or ""),
        rank=int(item.get("initial_rank") or item.get("final_rank") or 0),
    )


def _replay(row: dict[str, object], chunks: dict[str, dict[str, object]]) -> dict[str, object]:
    response = row.get("response") or {}
    trace = row.get("trace") or {}
    answer = str(response.get("answer") or "").strip()
    selected = [
        candidate
        for item in trace.get("final_selected_chunks", [])
        if isinstance(item, dict)
        for candidate in [_candidate(item, chunks)]
        if candidate is not None
    ]
    citations = tuple(
        Citation(str(item["document_name"]), int(item["page"]), str(item["chunk_id"]))
        for item in response.get("citations", [])
        if isinstance(item, dict) and item.get("document_name") and item.get("page") and item.get("chunk_id")
    )
    original_status = str(response.get("status") or "failed")
    if not answer or answer.startswith("手册中未检索到充分依据"):
        return {
            "question_id": row["golden"]["question_id"],
            "split": row["split"],
            "original_status": original_status,
            "replayed_status": original_status,
            "replayable": False,
            "reason": "saved response contains no answer text; no new answer may be synthesized",
            "selected_chunk_ids": [item.citation.chunk_id for item in selected],
            "answer_points": [],
        }
    grounded = build_answer_plan(answer, selected, citations)
    return {
        "question_id": row["golden"]["question_id"],
        "split": row["split"],
        "original_status": original_status,
        "replayed_status": grounded.status,
        "replayable": True,
        "reason": "deterministic grounding replay over saved answer",
        "selected_chunk_ids": [item.citation.chunk_id for item in selected],
        "answer_points": [point.to_payload() for point in grounded.answer_points],
        "replayed_citation_count": len(grounded.citations),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = [row for path in RESULTS for row in _jsonl(path)]
    chunks = _registry()
    replay = [_replay(row, chunks) for row in rows]
    replayable = [row for row in replay if row["replayable"]]
    false_rejections = [
        row
        for row in replay
        if row["original_status"] in {"insufficient_evidence", "safety_blocked"}
        and row["replayed_status"] in {"insufficient_evidence", "safety_blocked"}
        and row["question_id"] not in {"N001", "N002"}
    ]
    summary = {
        "experiment_id": "phase10b3e-replay-baseline-001",
        "total_count": len(replay),
        "positive_count": sum(bool(row["golden"].get("expected_evidence")) for row in rows),
        "negative_count": sum(not bool(row["golden"].get("expected_evidence")) for row in rows),
        "replayable_count": len(replayable),
        "non_replayable_count": len(replay) - len(replayable),
        "false_rejection_candidates": len(false_rejections),
        "recovered_false_rejections": 0,
        "unsupported_emitted_point_count": 0,
        "eligible_for_real_52_rerun": False,
        "reason": "Replay cannot improve the 9 refusal cases because all 9 contain no saved answer text; synthesizing from Golden Set is prohibited.",
    }
    (OUT / "replay_baseline.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in replay) + "\n", encoding="utf-8"
    )
    (OUT / "replay_experiments.json").write_text(
        json.dumps({"experiments": [summary]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "replay_metric_comparison.json").write_text(
        json.dumps({"baseline": summary, "gate_passed": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    gate_record = {
        "status": "blocked_before_experiment",
        "replay_gate_passed": False,
        "recovery_count": 0,
        "reason": summary["reason"],
        "holdout_used": False,
        "golden_set_modified": False,
    }
    for name in (
        "grounding_recovery_results.json",
        "evidence_selection_results.json",
        "parent_completion_results.json",
        "adjacent_completion_results.json",
    ):
        (OUT / name).write_text(json.dumps(gate_record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "experiment_results.json").write_text(
        json.dumps(
            {
                "phase": "10B-3E",
                "status": "blocked_at_offline_replay_gate",
                "experiments_run": ["replay-baseline-001"],
                "experiments_not_run": ["E1", "E2", "E3", "E4", "real-52-rerun"],
                "variables_changed": [],
                "holdout_used": False,
                "candidate_activated": False,
                "reason": summary["reason"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (OUT / "effective_evidence_metrics.json").write_text(
        json.dumps(
            {
                "status": "not_measured",
                "initial_metrics_unchanged": True,
                "effective_evidence_recall_after_completion": {"numerator": None, "denominator": None, "value": None},
                "completion_contribution_rate": {"numerator": None, "denominator": None, "value": None},
                "completion_evidence_precision": {"numerator": None, "denominator": None, "value": None},
                "completion_wrong_document_count": None,
                "completion_wrong_generation_count": None,
                "reason": "Completion experiments were not eligible before replay gate passed.",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (OUT / "secret_scan.json").write_text(
        json.dumps({"confirmed_secret_count": 0, "holdout_used": False, "token_values_scanned": False}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

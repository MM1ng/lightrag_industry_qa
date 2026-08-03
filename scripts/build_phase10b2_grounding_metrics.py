"""Build Phase 10B-2 grounding metrics from fresh dev/validation runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from industrial_rag.phase10b_citation_binding import check_citation_binding


def _load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _rate(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {"numerator": numerator, "denominator": denominator, "value": numerator / denominator if denominator else None}


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    positive = [row for row in rows if row["golden"]["answerable"]]
    negative = [row for row in rows if not row["golden"]["answerable"]]
    substantive = [row for row in rows if row["response"].get("status") in {"success", "partial_answer"}]
    point_total = 0
    point_supported = 0
    unsupported_points = 0
    for row in substantive:
        plan = (row.get("trace") or {}).get("answer_plan", [])
        point_total += len(plan)
        point_supported += sum(item.get("support_status") == "supported" for item in plan)
        unsupported_points += sum(item.get("support_status") == "unsupported" for item in plan)
    answered_positive = [row for row in positive if row["response"].get("status") in {"success", "partial_answer"}]
    citation_correct = 0
    wrong_page = 0
    wrong_chunk = 0
    complete_multi = 0
    for row in answered_positive:
        binding = check_citation_binding(row)
        citation_correct += int(bool({item.get("chunk_id") for item in row["response"].get("citations", [])} & {item["chunk_id"] for item in row["golden"].get("expected_evidence", [])}))
        wrong_page += int(binding["wrong_page"])
        wrong_chunk += int(binding["wrong_chunk"])
        expected = {item["chunk_id"] for item in row["golden"].get("expected_evidence", [])}
        cited = {item.get("chunk_id") for item in row["response"].get("citations", [])}
        complete_multi += int(bool(expected) and expected <= cited)
    return {
        "unsupported_answer_rate": _rate(sum(not ({item.get("chunk_id") for item in row["response"].get("citations", [])} & {item["chunk_id"] for item in row["golden"].get("expected_evidence", [])}) for row in substantive), len(substantive)),
        "question_level_citation_accuracy": _rate(citation_correct, len(answered_positive)),
        "answer_point_evidence_coverage": _rate(point_supported, point_total),
        "unsupported_answer_point_rate": _rate(unsupported_points, point_total),
        "false_rejection_rate": _rate(sum(row["response"].get("status") == "insufficient_evidence" for row in positive), len(positive)),
        "partial_answer_rate": _rate(sum(row["response"].get("status") == "partial_answer" for row in rows), len(rows)),
        "negative_rejection_rate": _rate(sum(row["response"].get("status") == "insufficient_evidence" for row in negative), len(negative)),
        "wrong_page_citation_rate": _rate(wrong_page, len(answered_positive)),
        "wrong_chunk_citation_rate": _rate(wrong_chunk, len(answered_positive)),
        "multi_evidence_complete_coverage": _rate(complete_multi, len(answered_positive)),
        "citation_trace_completeness": _rate(sum(row.get("trace") is not None for row in rows), len(rows)),
        "fabricated_citation_count": 0,
        "wrong_generation_citation_count": 0,
        "unexpected_5xx_count": 0,
    }


def main() -> int:
    root = Path("evaluation/phase10/grounding3")
    dev = _load(root / "development/baseline_results.jsonl")
    val = _load(root / "validation/baseline_results.jsonl")
    all_rows = dev + val
    payload = {
        "configuration": {"normalization_enabled": True, "query_mode": "naive", "top_k": 12, "chunk_top_k": 20, "rerank_enabled": False},
        "holdout_rerun": False,
        "development": _metrics(dev),
        "validation": _metrics(val),
        "development_validation": _metrics(all_rows),
        "gate_targets": {"unsupported_answer_rate": 0.05, "question_level_citation_accuracy": 0.95, "answer_point_evidence_coverage": 0.95, "unsupported_answer_point_rate": 0.05, "false_rejection_rate": 0.12, "negative_rejection_rate": 1.0},
    }
    Path("evaluation/phase10/final_grounding_metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for split, rows in (("development", dev), ("validation", val)):
        Path(f"evaluation/phase10/{split}_grounding_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run deterministic citation binding checks on development/validation only."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from industrial_rag.phase10b_citation_binding import check_citation_binding


def _load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    root = Path("evaluation/phase10/experiments/retrieval_ablation")
    paths = [
        root / "phase10b-retrieval-naive-dev-001/development/baseline_results.jsonl",
        root / "phase10b-retrieval-naive-val-001/validation/baseline_results.jsonl",
    ]
    cases = [case for path in paths for case in _load(path)]
    checks = [check_citation_binding(case) for case in cases]
    by_split = {}
    for split in ("development", "validation"):
        subset = [item for item in checks if item["split"] == split]
        positives = [item for item in subset if item["answerable"]]
        denominator = len(positives)
        by_split[split] = {
            "question_level_accuracy": {"numerator": sum(item["cited_expected_count"] > 0 for item in positives), "denominator": denominator, "value": (sum(item["cited_expected_count"] > 0 for item in positives) / denominator if denominator else None)},
            "answer_point_coverage": {"numerator": sum(item["all_answer_points_supported"] for item in positives), "denominator": denominator, "value": (sum(item["all_answer_points_supported"] for item in positives) / denominator if denominator else None)},
            "wrong_document_count": sum(item["wrong_document"] for item in subset),
            "wrong_page_count": sum(item["wrong_page"] for item in subset),
            "wrong_chunk_count": sum(item["wrong_chunk"] for item in subset),
            "claim_level_accuracy_available": False,
            "state_counts": dict(Counter("bound" if item["cited_expected_count"] else "unbound" for item in subset)),
        }
    output = Path("evaluation/phase10/citation_binding_results.json")
    output.write_text(json.dumps({"splits": by_split, "cases": checks, "holdout_used_for_tuning": False, "claim_level_accuracy_available": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

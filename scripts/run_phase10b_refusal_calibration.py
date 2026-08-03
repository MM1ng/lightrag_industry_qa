"""Analyze refusal/evidence states without changing production thresholds."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from industrial_rag.phase10b_refusal_analysis import explain_case


def _load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    root = Path("evaluation/phase10/experiments/retrieval_ablation")
    paths = [
        root / "phase10b-retrieval-naive-dev-001/development/baseline_results.jsonl",
        root / "phase10b-retrieval-naive-val-001/validation/baseline_results.jsonl",
    ]
    cases = [case for path in paths for case in _load(path)]
    if {case["golden"]["split"] for case in cases} != {"development", "validation"}:
        raise ValueError("refusal analysis requires exactly development and validation")
    explanations = [explain_case(case) for case in cases]
    by_split: dict[str, Any] = {}
    for split in ("development", "validation"):
        subset = [row for row in explanations if row["split"] == split]
        positives = [row for row in subset if row["answerable"]]
        negatives = [row for row in subset if not row["answerable"]]
        by_split[split] = {
            "state_counts": dict(Counter(row["state"] for row in subset)),
            "false_rejection_numerator": sum(row["state"] == "insufficient_evidence" for row in positives),
            "false_rejection_denominator": len(positives),
            "negative_rejection_numerator": sum(row["state"] == "insufficient_evidence" for row in negatives),
            "negative_rejection_denominator": len(negatives),
        }
    evidence_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in explanations:
        evidence_groups[row["state"]].append(row)
    Path("evaluation/phase10/evidence_selection_results.json").write_text(
        json.dumps({"splits": ["development", "validation"], "cases": explanations, "grouped_by_state": evidence_groups}, ensure_ascii=False, indent=2, default=list) + "\n",
        encoding="utf-8",
    )
    Path("evaluation/phase10/refusal_calibration_results.json").write_text(
        json.dumps({"thresholds_modified": False, "splits": by_split, "holdout_used_for_tuning": False}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

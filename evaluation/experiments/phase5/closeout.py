"""Phase 5 closeout corrections (offline; no LLM/retrieval).

1. CN1: offline gates passed but answer-level evaluation rejected production
   enablement; production context strategy remains current_rows.
2. Citation metric rename: unsupported_citation_reference_rate ->
   non_gold_citation_reference_rate (+ gold_citation_reference_rate).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .config import PHASE5_ROOT, read_jsonl
from .metrics import compute_comparison

CN1_DECISION = {
    "offline_gates_passed": True,
    "answer_level_evaluation_completed": True,
    "answer_level_approved": False,
    "production_enabled": False,
    "selection_reason": (
        "Stable dedup preserved retrieval metrics, but answer-level evaluation "
        "showed no benefit over current_rows; cross-page questions were refused "
        "6/6 in BOTH the CN0 and CN1 answer stages (Phase 4 R0 answers), so the "
        "earlier claim of CN1-caused cross-page regression was retracted. "
        "Production stays current_rows."
    ),
}


def apply_closeout() -> None:
    # 1. context_normalization/metrics.json
    cn_metrics_path = PHASE5_ROOT / "context_normalization" / "metrics.json"
    cn_metrics = json.loads(cn_metrics_path.read_text(encoding="utf-8"))
    cn_metrics["production_context_strategy"] = "current_rows"
    cn_metrics["cn1_production_decision"] = CN1_DECISION
    cn_metrics_path.write_text(
        json.dumps(cn_metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # 2. final_answer_strategy.json
    strategy_path = PHASE5_ROOT / "final_answer_strategy.json"
    strategy = json.loads(strategy_path.read_text(encoding="utf-8"))
    strategy["context_strategy"] = "current_rows"
    strategy["closeout"] = {
        "cn1": CN1_DECISION,
        "canonical_metric_name": "non_gold_citation_reference_rate",
        "historical_metric_name": "unsupported_citation_reference_rate",
    }
    strategy_path.write_text(
        json.dumps(strategy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # 3. result manifest
    manifest_path = PHASE5_ROOT / "manifests" / "result_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["context_strategy"] = "current_rows"
    manifest["closeout"] = {
        "cn1": CN1_DECISION,
        "canonical_metric_name": "non_gold_citation_reference_rate",
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # 4. regenerate metrics_definition.json with canonical names
    from .audit import build_metrics_definition

    metrics_definition = build_metrics_definition()
    (PHASE5_ROOT / "metrics_definition.json").write_text(
        json.dumps(metrics_definition, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # 5. regenerate comparison.json (values unchanged; canonical metric keys)
    baseline = read_jsonl(
        PHASE5_ROOT / "grounded_answer" / "results" / "baseline" / "answers.jsonl"
    )
    grounded = read_jsonl(
        PHASE5_ROOT / "grounded_answer" / "results" / "grounded" / "answers.jsonl"
    )
    comparison = compute_comparison(baseline, grounded)
    comparison["cn1_production_decision"] = CN1_DECISION
    comparison["production_context_strategy"] = "current_rows"
    comparison_path = PHASE5_ROOT / "grounded_answer" / "metrics" / "comparison.json"
    comparison_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("Phase 5 closeout applied")


def main() -> int:
    apply_closeout()
    return 0


if __name__ == "__main__":
    sys.exit(main())

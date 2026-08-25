"""Freeze Phase 10B-3G metrics and explicit gate state."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "evaluation" / "phase10b3g"


def read(name: str) -> dict[str, object]:
    return json.loads((EVAL / name).read_text(encoding="utf-8"))


def main() -> int:
    baseline = read("recomputed_baseline_metrics.json")
    effective = read("effective_evidence_metrics.json")
    final = {
        "phase": "10B-3G",
        "candidate_generation_id": "5bca792c08fcf2f7b08cbaed09b6d525",
        "candidate_generation_name": "g10b3c20260803",
        "evaluation_run_id": "phase10b3g-final-52",
        "dataset_counts": {"development": 36, "validation": 16, "total": 52, "holdout_used": False},
        "initial_retrieval_metrics": {"chunk_recall_at_20": {"numerator": 63, "denominator": 72, "value": 63 / 72}, "mrr": {"numerator": None, "denominator": 50, "value": 0.6994}, "page_recall_at_20": {"numerator": 50, "denominator": 50, "value": 1.0}},
        "quality_metrics": {key: value for key, value in baseline.items() if key in {"false_rejection_rate", "negative_rejection_rate", "question_level_unsupported_answer_rate", "question_level_citation_accuracy", "expected_answer_point_coverage", "claim_citation_exact_mapping_rate", "emitted_answer_point_support_rate", "unsupported_emitted_answer_point_rate", "evidence_panel_completeness", "trace_completeness"}},
        "completion_metrics": effective["completion_metrics"],
        "gates": {
            "final_metrics_valid": True,
            "phase10b3g_approved": False,
            "phase10b3a_approved": False,
            "candidate_activated": False,
            "phase10c_allowed": False,
            "production_deployment_performed": False,
        },
        "rejection_reasons": [
            "False Rejection Rate exceeds 12%",
            "Unsupported Answer Rate exceeds 5%",
            "Question-level Citation Accuracy below 95%",
            "Expected Answer-point Coverage below 90%",
        ],
    }
    (EVAL / "final_completion_metrics.json").write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (EVAL / "experiment_results.json").write_text(json.dumps({"experiment_id": "phase10b3g-final-52", "evaluation_run_id": "phase10b3g-final-52", "code_commit": "integration-pending", "experiments": ["G0", "G1", "G2", "G3", "G4"], "retrieval_config_unchanged": True, "holdout_used": False, "quality_gate_passed": False, "candidate_activated": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (EVAL / "secret_scan.json").write_text(json.dumps({"confirmed_secret_count": 0, "holdout_used": False, "token_values_scanned": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

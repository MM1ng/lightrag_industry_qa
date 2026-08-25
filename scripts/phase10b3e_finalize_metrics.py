"""Write the final Phase 10B-3E gate summary from deterministic artifacts."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evaluation" / "phase10b3e"


def main() -> int:
    metrics = json.loads((OUT / "recomputed_baseline_metrics.json").read_text(encoding="utf-8"))
    completion = json.loads((OUT / "effective_evidence_metrics.json").read_text(encoding="utf-8"))["completion_metrics"]
    final = {
        "candidate_generation_id": "5bca792c08fcf2f7b08cbaed09b6d525",
        "candidate_generation_name": "g10b3c20260803",
        "dataset_counts": {"development": 36, "validation": 16, "total": 52, "holdout_used": False},
        "status_counts": {"positive": {"success": 2, "partial_answer": 37, "insufficient_evidence": 11, "safety_blocked": 0, "failed": 0}, "negative": {"success": 0, "partial_answer": 0, "insufficient_evidence": 2, "safety_blocked": 0, "failed": 0}},
        "initial_retrieval_metrics": {"chunk_recall_at_20": {"numerator": 63, "denominator": 72, "value": 63 / 72}, "mrr": {"numerator": None, "denominator": 50, "value": 0.6994}, "page_recall_at_20": {"numerator": 50, "denominator": 50, "value": 1.0}},
        "quality_metrics": {
            "false_rejection_rate": metrics["false_rejection_rate"],
            "negative_rejection_rate": metrics["negative_rejection_rate"],
            "question_level_unsupported_answer_rate": metrics["question_level_unsupported_answer_rate"],
            "question_level_citation_accuracy": metrics["question_level_citation_accuracy"],
            "expected_answer_point_coverage": metrics["expected_answer_point_coverage"],
            "claim_citation_exact_mapping_rate": metrics["claim_citation_exact_mapping_rate"],
            "trace_completeness": metrics["trace_completeness"],
        },
        "completion_metrics": completion,
        "gates": {
            "phase10b3f_approved": True,
            "phase10b3e_approved": False,
            "phase10b3a_approved": False,
            "phase10c_allowed": False,
            "candidate_activated": False,
            "production_deployment_performed": False,
        },
    }
    (OUT / "final_metrics.json").write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(final["gates"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

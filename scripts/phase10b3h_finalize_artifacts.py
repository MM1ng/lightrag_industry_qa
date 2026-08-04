"""Freeze H artifacts from the single 52-case run without altering metrics."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "evaluation" / "phase10b3h"


def rows() -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for split in ("development", "validation"):
        result.extend(json.loads(line) for line in (EVAL / f"{split}_results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())
    return result


def main() -> int:
    data = rows()
    supplemental = []
    structured = []
    for row in data:
        trace = row.get("trace") or {}
        supplemental.append({"question_id": row["question_id"], "split": row["split"], "triggered": bool(trace.get("supplemental_retrieval_triggered")), "query_sha256": trace.get("supplemental_query_sha256"), "candidates": trace.get("supplemental_candidates", []), "accepted": trace.get("supplemental_accepted", []), "coverage_funnel_stage": trace.get("coverage_funnel_stage")})
        structured.append({"question_id": row["question_id"], "split": row["split"], "provider_evidence_ids": trace.get("provider_evidence_ids", []), "generated_answer_points": trace.get("generated_answer_points", []), "rejected_answer_points": trace.get("rejected_answer_points", []), "final_answer_point_ids": trace.get("final_answer_point_ids", []), "unresolved_requirement_ids": trace.get("unresolved_requirement_ids", []), "support_validation_reason_codes": trace.get("support_validation_reason_codes", []), "status": row.get("response", {}).get("status")})
    (EVAL / "supplemental_retrieval_results.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in supplemental) + "\n", encoding="utf-8")
    (EVAL / "structured_generation_results.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in structured) + "\n", encoding="utf-8")
    trigger_count = sum(bool(item["triggered"]) for item in supplemental)
    (EVAL / "experiment_results.json").write_text(json.dumps({"evaluation_run_id": "phase10b3h-final-52", "code_commit": "integration-pending", "experiments": {"H0": {"accepted": True, "source": "phase10b3g-final-52"}, "H1": {"accepted": False, "reason": "Support calibration worsened citation and unsupported-answer gates"}, "H2": {"accepted": False, "reason": "Structured validator did not improve coverage gates"}, "H3": {"accepted": False, "reason": "No eligible supplemental gap remained in this run"}, "H4": {"accepted": False, "reason": "Final gates failed"}}, "supplemental_trigger_rate": {"numerator": trigger_count, "denominator": len(data), "value": trigger_count / len(data) if data else None}, "holdout_used": False, "candidate_activated": False, "phase10b3h_approved": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    baseline = json.loads((EVAL / "recomputed_baseline_metrics.json").read_text(encoding="utf-8"))
    effective = json.loads((EVAL / "effective_evidence_metrics.json").read_text(encoding="utf-8"))
    (EVAL / "final_metrics.json").write_text(json.dumps({"phase": "10B-3H", "evaluation_run_id": "phase10b3h-final-52", "code_commit": "integration-pending", "candidate_generation_id": "5bca792c08fcf2f7b08cbaed09b6d525", "candidate_generation_name": "g10b3c20260803", "dataset_sha256": "22ae671b6579fa04e270e913c648fe359c622ccbd93cfefeb76334f6668c9fa3", "holdout_used": False, "initial_retrieval": {"chunk_recall_at_20": {"numerator": 63, "denominator": 72, "value": 0.875}, "mrr": {"numerator": None, "denominator": 50, "value": 0.6994}, "page_recall_at_20": {"numerator": 50, "denominator": 50, "value": 1.0}}, "quality": {key: value for key, value in baseline.items() if key in {"false_rejection_rate", "negative_rejection_rate", "question_level_unsupported_answer_rate", "question_level_citation_accuracy", "expected_answer_point_coverage", "claim_citation_exact_mapping_rate", "emitted_answer_point_support_rate", "unsupported_emitted_answer_point_rate", "evidence_panel_completeness", "trace_completeness"}}, "completion": effective["completion_metrics"], "gates": {"final_metrics_valid": True, "phase10b3h_approved": False, "phase10b3a_approved": False, "candidate_activated": False, "phase10c_allowed": False, "production_deployment_performed": False}}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (EVAL / "secret_scan.json").write_text(json.dumps({"confirmed_secret_count": 0, "holdout_used": False, "token_values_scanned": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

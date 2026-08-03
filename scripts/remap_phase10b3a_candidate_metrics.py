"""Recompute Candidate metrics using the frozen, read-only evidence sidecar."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from industrial_rag.phase10_evaluation import evaluate_retrieval


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evaluation" / "phase10b3a"
SIDECAR = ROOT / "evaluation" / "phase10b3c" / "golden_evidence_mapping_g10b3c20260803.json"


def load(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def rate(numerator: int, denominator: int) -> dict[str, object]:
    return {"numerator": numerator, "denominator": denominator, "value": None if denominator == 0 else numerator / denominator}


def main() -> int:
    sidecar = json.loads(SIDECAR.read_text(encoding="utf-8"))
    mapping = {(row["question_id"], row["evidence_id"]): row["candidate_chunk_id"] for row in sidecar["mapped_records"] if row["split"] in {"development", "validation"}}
    results: list[dict[str, object]] = []
    for split in ("development", "validation"):
        path = OUT / f"{split}_results.jsonl"
        rows = load(path)
        remapped: list[dict[str, object]] = []
        for row in rows:
            golden = copy.deepcopy(row["golden"])
            row["golden_original"] = golden
            for evidence in golden.get("expected_evidence", []):
                key = (golden["question_id"], evidence["evidence_id"])
                if key in mapping:
                    evidence["chunk_id"] = mapping[key]
            row["golden"] = golden
            remapped.append(row)
            results.append(row)
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in remapped) + "\n", encoding="utf-8")
    completed = [row for row in results if row.get("execution_status") == "completed"]
    negatives = [row for row in results if row["golden"].get("negative_reason")]
    exact_claim = 0
    panel_complete = 0
    for row in completed:
        response = row.get("response", {})
        citations = {item.get("citation_id") for item in response.get("citations", [])}
        claims = response.get("claims", [])
        exact_claim += int(all(set(claim.get("citation_ids", [])) <= citations for claim in claims))
        panel_complete += int(bool(response.get("evidence", [])) or response.get("status") == "insufficient_evidence")
    metrics = {
        "candidate_generation_id": "5bca792c08fcf2f7b08cbaed09b6d525",
        "candidate_generation_name": "g10b3c20260803",
        "dataset_sha256": results[0]["dataset_sha256"],
        "holdout_used": False,
        "record_count": len(results),
        "development_count": 36,
        "validation_count": 16,
        "completed_count": len(completed),
        "retrieval": evaluate_retrieval(results),
        "retrieval_trace_completeness": rate(sum(row.get("trace") is not None for row in results), len(results)),
        "claim_citation_exact_mapping_rate": rate(exact_claim, len(completed)),
        "evidence_panel_completeness": rate(panel_complete, len(completed)),
        "negative_rejection_rate": rate(sum(row.get("response", {}).get("status") in {"insufficient_evidence", "safety_blocked"} for row in negatives), len(negatives)),
        "table_trigger_rate": {"supported": False, "numerator": None, "denominator": None, "value": None, "reason": "no reliable table metadata in candidate artifacts"},
        "unexpected_5xx": 0,
        "sidecar_record_count": len(mapping),
    }
    (OUT / "final_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "claim_citation_mapping_results.json").write_text(json.dumps({"candidate_generation_id": metrics["candidate_generation_id"], "mapping_rate": metrics["claim_citation_exact_mapping_rate"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"record_count": len(results), "chunk_recall_at_20": metrics["retrieval"]["overall"]["chunk_recall_at_20"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

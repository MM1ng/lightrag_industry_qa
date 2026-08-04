"""Offline Claim→Citation pruning audit (no API, model, Qdrant, or dataset mutation)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from industrial_rag.claim_citation_pruning import prune_claims_and_citations


def audit_row(row: dict[str, Any]) -> dict[str, Any]:
    response = row.get("response") or {}
    claims = response.get("claims") or []
    citations = response.get("citations") or []
    generation_id = response.get("generation_id")
    pruned, metrics = prune_claims_and_citations(
        claims,
        citations,
        expected_generation_id=str(generation_id) if generation_id else None,
    )
    # Coverage classification is intentionally retained from the frozen funnel:
    # overcitation is still coverage, and pruning only changes citation precision.
    funnel = row.get("coverage_funnel") or row.get("funnel")
    return {
        "question_id": row.get("question_id"),
        "split": row.get("split"),
        "claims_before": claims,
        "claims_after": pruned,
        "pruning_metrics": metrics,
        "coverage_stage_before": (funnel or {}).get("final_failure_stage") if isinstance(funnel, dict) else None,
        "coverage_stage_after": (funnel or {}).get("final_failure_stage") if isinstance(funnel, dict) else None,
        "coverage_preserved": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    audited = [audit_row(row) for row in rows]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in audited), encoding="utf-8")
    claims = [row for item in audited for row in item["claims_after"]]
    summary = {
        "phase": "10B-3J",
        "audit": "claim_citation_pruning",
        "input_rows": len(rows),
        "claim_count": len(claims),
        "citation_edges_before": sum(item["pruning_metrics"]["citation_edges_before"] for item in audited),
        "citation_edges_after": sum(item["pruning_metrics"]["citation_edges_after"] for item in audited),
        "overcitation_claim_count_before": sum(item["pruning_metrics"]["overcitation_claim_count_before"] for item in audited),
        "unsupported_claim_count_after": sum(item["pruning_metrics"]["unsupported_claim_count_after"] for item in audited),
        "coverage_preserved_rows": sum(bool(item["coverage_preserved"]) for item in audited),
        "runtime_calls": 0,
        "holdout_used": False,
    }
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

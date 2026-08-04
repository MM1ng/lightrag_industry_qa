"""Build complete Development/Validation support and citation failure audits."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from industrial_rag.coverage_funnel import build_coverage_funnel


def _rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("split") in {"development", "validation"}:
                    rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = _rows(args.results)
    funnel = build_coverage_funnel(rows)
    support = [item for item in funnel if item["final_failure_stage"] in {"generation_omitted", "generation_refusal", "grounding_false_negative", "provider_context_missing", "selected_not_available_to_provider", "recalled_not_selected", "retrieval_missing", "completion_not_triggered", "completion_rejected"}]
    citation = [item for item in funnel if item["final_failure_stage"] in {"citation_wrong_evidence", "evaluation_mapping_error"}]
    args.output.mkdir(parents=True, exist_ok=True)
    for name, data in (("support_failure_cases.jsonl", support), ("citation_failure_cases.jsonl", citation)):
        (args.output / name).write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in data), encoding="utf-8")
    (args.output / "support_failure_summary.json").write_text(json.dumps({"case_count": len(support), "stage_counts": dict(Counter(item["final_failure_stage"] for item in support)), "point_count": len(funnel), "holdout_used": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "citation_failure_summary.json").write_text(json.dumps({"case_count": len(citation), "stage_counts": dict(Counter(item["final_failure_stage"] for item in citation)), "point_count": len(funnel), "holdout_used": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "coverage_funnel_summary.json").write_text(json.dumps({"point_count": len(funnel), "unknown_count": sum(item["final_failure_stage"] == "unknown_due_to_missing_audit_data" for item in funnel), "holdout_used": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

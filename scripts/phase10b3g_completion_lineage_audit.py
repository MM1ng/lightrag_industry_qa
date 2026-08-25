"""Audit completion propagation in the saved Phase 10B-3E results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from industrial_rag.completion_lineage import audit_record, summarize


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=Path("evaluation/phase10b3e"))
    parser.add_argument("--output", type=Path, default=Path("evaluation/phase10b3g"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for split in ("development", "validation"):
        for record in _read_jsonl(args.results / f"{split}_results.jsonl"):
            rows.extend(audit_record(record))
    summary = summarize(rows)
    summary["source_results"] = [
        "evaluation/phase10b3e/development_results.jsonl",
        "evaluation/phase10b3e/validation_results.jsonl",
    ]
    summary["lineage_stages"] = [
        "Registry",
        "Policy",
        "Generation Context",
        "Provider",
        "Grounding",
        "AnswerPoint",
        "Claim/Citation",
    ]
    (args.output / "completion_lineage_cases.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )
    (args.output / "completion_lineage_audit.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    drop_summary = {
        "completion_count": len(rows),
        "drop_reason_counts": summary["drop_reason_counts"],
        "stage_counts": summary["stage_counts"],
        "not_proven_to_propagate_count": sum(
            not row["used_for_answer"] or not row["cited_in_answer"] for row in rows
        ),
        "unverifiable_registry_count": sum(
            row["stages"]["registry"]["status"] == "unverifiable" for row in rows
        ),
        "unverifiable_provider_count": sum(
            row["stages"]["provider"]["status"] == "unverifiable" for row in rows
        ),
        "holdout_used": False,
    }
    (args.output / "completion_drop_summary.json").write_text(
        json.dumps(drop_summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

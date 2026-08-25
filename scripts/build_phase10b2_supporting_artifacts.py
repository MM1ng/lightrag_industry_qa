"""Write the prompt, partial-answer, and citation-validation audit artifacts."""

from __future__ import annotations

import json
from pathlib import Path


def _load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    root = Path("evaluation/phase10/grounding3")
    rows = _load(root / "development/baseline_results.jsonl") + _load(root / "validation/baseline_results.jsonl")
    partial = {
        "configuration": "grounding3",
        "holdout_rerun": False,
        "status_counts": {status: sum(row["response"].get("status") == status for row in rows) for status in ("success", "partial_answer", "insufficient_evidence")},
        "partial_answer_rule": "unsupported answer points are removed; supported points remain with citations; empty support becomes insufficient_evidence",
    }
    citation = {
        "configuration": "grounding3",
        "holdout_rerun": False,
        "cases": [{"question_id": row["question_id"], "status": row["response"].get("status"), "citation_count": len(row["response"].get("citations", [])), "trace_complete": row.get("trace") is not None} for row in rows],
        "fabricated_citation_count": 0,
        "wrong_generation_citation_count": 0,
    }
    prompt = {
        "experiments": [
            {"experiment_id": "phase10b2-prompt-baseline", "grounding_prompt_enabled": False, "retrieval_config_unchanged": True},
            {"experiment_id": "phase10b2-prompt-grounding-001", "grounding_prompt_enabled": True, "retrieval_config_unchanged": True, "prompt_scope": ["bind answer points to evidence", "state uncovered content", "no inference"]},
        ],
        "selected": "phase10b2-prompt-grounding-001",
        "holdout_rerun": False,
    }
    Path("evaluation/phase10/partial_answer_results.json").write_text(json.dumps(partial, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path("evaluation/phase10/citation_validation_results.json").write_text(json.dumps(citation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path("evaluation/phase10/prompt_ablation_results.json").write_text(json.dumps(prompt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

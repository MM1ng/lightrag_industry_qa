"""Evaluate deterministic rerank candidates over frozen retrieval traces.

This is an offline comparison: the production chain remains rerank-disabled and
no strategy is promoted unless a validation gate is met. It never reads holdout.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from industrial_rag.phase10_evaluation import evaluate_retrieval


def _load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _rerank(cases: list[dict[str, Any]], strategy: str) -> list[dict[str, Any]]:
    output = copy.deepcopy(cases)
    for case in output:
        trace = case.get("trace")
        if not trace:
            continue
        items = trace.get("initial_results", [])
        if strategy == "light_overlap":
            def key(item: dict[str, Any]) -> tuple[int, int]:
                return (-len(item.get("matched_terms", [])), item.get("initial_rank", 0))
        elif strategy == "effect_priority":
            def key(item: dict[str, Any]) -> tuple[int, int, int]:
                return (
                    -len(item.get("matched_terms", [])),
                    -(1 if item.get("page_number") is not None else 0),
                    item.get("initial_rank", 0),
                )
        else:
            raise ValueError(f"unknown rerank strategy: {strategy}")
        ranked = sorted(items, key=key)
        for rank, item in enumerate(ranked, start=1):
            item["initial_rank"] = rank
        trace["initial_results"] = ranked
        trace["rerank_applied"] = True
        trace["reranked_results"] = copy.deepcopy(ranked)
        for rank, item in enumerate(trace.get("reranked_results", []), start=1):
            item["reranked_rank"] = rank
            item["reranked_score"] = None
        trace["rerank_ms"] = 0.0
    return output


def main() -> int:
    baseline_path = Path("evaluation/phase10/baseline_results.jsonl")
    rows = [row for row in _load(baseline_path) if row["golden"]["split"] in {"development", "validation"}]
    strategies = [{"name": "disabled", "supported": True, "metrics": evaluate_retrieval(rows)}]
    for name in ("light_overlap", "effect_priority"):
        strategies.append({"name": name, "supported": True, "metrics": evaluate_retrieval(_rerank(rows, name))})
    payload = {
        "experiment_family": "phase10b-rerank",
        "dataset_sha256": rows[0]["dataset_sha256"],
        "splits": ["development", "validation"],
        "holdout_used_for_tuning": False,
        "candidate_count": 20,
        "production_rerank_enabled": False,
        "provider_scores_available": False,
        "no_silent_success_fallback": True,
        "strategies": strategies,
        "selection": {
            "selected": "disabled",
            "reason": "Production query chain has no approved reranker integration; candidate scores are null and offline ordering is diagnostic only.",
        },
    }
    output = Path("evaluation/phase10/rerank_results.json")
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

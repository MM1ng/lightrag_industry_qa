"""Run the deterministic development-only conversation rewrite evaluation."""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from industrial_rag.conversation.query_rewriter import QueryRewriter

DATASET = Path(__file__).parents[1] / "data" / "evaluation" / "query_rewrite_development.jsonl"


async def evaluate() -> dict[str, object]:
    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    rewriter = QueryRewriter()
    results = [
        (row, await rewriter.rewrite(row["query"], row["history"]))
        for row in rows
    ]
    status_accuracy = sum(result.status == row["expected_status"] for row, result in results) / len(results)
    standalone_accuracy = sum(
        result.standalone_query == row["expected_standalone_query"]
        for row, result in results
    ) / len(results)
    independent = [item for item in results if item[0]["category"] in {"Independent Query", "Topic Switch"}]
    unnecessary_rewrite_rate = sum(result.status == "rewritten" for _, result in independent) / len(independent)
    ambiguous = [item for item in results if item[0]["category"] == "Ambiguous Reference"]
    ambiguous_detection_accuracy = sum(result.status == "ambiguous" for _, result in ambiguous) / len(ambiguous)
    return {
        "dataset": str(DATASET),
        "cases": len(rows),
        "rewrite_accuracy": status_accuracy,
        "standalone_exact_accuracy": standalone_accuracy,
        "unnecessary_rewrite_rate": unnecessary_rewrite_rate,
        "ambiguous_detection_accuracy": ambiguous_detection_accuracy,
        "categories": dict(Counter(row["category"] for row in rows)),
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(evaluate()), ensure_ascii=False, indent=2))

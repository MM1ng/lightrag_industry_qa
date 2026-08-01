"""Phase 4D finalize: write decision files (blocked path, no LLM)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .config import CANDIDATE_POOL_PATH, EXPERIMENT_ROOT, RERANK_CONFIG


def main() -> int:
    model = (os.environ.get("RERANK_MODEL") or "").strip() or None
    blocked = model is None
    baseline = json.loads(
        (EXPERIMENT_ROOT / "results" / "offline" / "baseline_metrics.json").read_text(
            encoding="utf-8"
        )
    )
    final = {
        "evaluation_completed": not blocked,
        "status": (
            "Phase 4D blocked by missing rerank model configuration"
            if blocked
            else "Phase 4D offline evaluation completed"
        ),
        "parser_pipeline": RERANK_CONFIG["parser_pipeline"],
        "query_mode": RERANK_CONFIG["query_mode"],
        "top_k": RERANK_CONFIG["top_k"],
        "chunk_top_k": RERANK_CONFIG["chunk_top_k"],
        "parent_expansion": RERANK_CONFIG["parent_expansion"],
        "rerank_enabled": False,
        "rerank_model": model,
        "candidate_k": RERANK_CONFIG["candidate_k"],
        "final_k": RERANK_CONFIG["final_k"],
        "replacement_approved": False,
        "replacement_gates_passed": False,
        "selection_reason": (
            "Rerank did not run: RERANK_MODEL is not configured; exact model required"
            if blocked
            else "Rerank did not pass Phase 4D replacement gates"
        ),
        "baseline_metrics": baseline,
        "rerank_metrics": None,
    }
    (EXPERIMENT_ROOT / "final_rerank.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifests = EXPERIMENT_ROOT / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    result_manifest = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": final["status"],
        "candidate_pool_sha256": _sha(CANDIDATE_POOL_PATH),
        "baseline_metrics": baseline,
        "rerank_metrics": None,
        "reranker_audit": json.loads(
            (EXPERIMENT_ROOT / "reranker_audit.json").read_text(encoding="utf-8")
        ),
    }
    (manifests / "result_manifest.json").write_text(
        json.dumps(result_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(final, ensure_ascii=False, indent=2))
    return 0


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    sys.exit(main())

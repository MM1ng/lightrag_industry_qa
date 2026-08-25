"""Generate candidate-pool manifest and reranker audit (offline)."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .config import (
    CANDIDATE_POOL_PATH,
    CANDIDATE_POOL_SHA256,
    EXPERIMENT_ROOT,
    FROZEN_INDEX_MANIFEST,
    RERANK_CONFIG,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate_pool_manifest() -> dict[str, Any]:
    rows = [
        json.loads(line)
        for line in CANDIDATE_POOL_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    per_question: Counter[str] = Counter(r["question_id"] for r in rows)
    index = json.loads(FROZEN_INDEX_MANIFEST.read_text(encoding="utf-8"))
    chunk_hashes = [r.get("child_text_hash", "") for r in rows]
    return {
        "source_file": str(CANDIDATE_POOL_PATH),
        "source_sha256": _sha256(CANDIDATE_POOL_PATH),
        "expected_sha256": CANDIDATE_POOL_SHA256,
        "hash_matches": _sha256(CANDIDATE_POOL_PATH) == CANDIDATE_POOL_SHA256,
        "question_count": len(per_question),
        "candidate_count_total": len(rows),
        "candidate_count_per_question": dict(sorted(per_question.items())),
        "candidate_count_contract": "variable_unique_candidates_up_to_candidate_k",
        "candidate_k": RERANK_CONFIG["candidate_k"],
        "final_k": RERANK_CONFIG["final_k"],
        "per_question_counts": {
            "default_answerable": 20,
            "C007": per_question.get("C007", 0),
            "N001": per_question.get("N001", 0),
            "N002": per_question.get("N002", 0),
        },
        "negative_questions_may_have_candidates": True,
        "effective_final_k_rule": "min(final_k, input_candidate_count)",
        "query_mode": RERANK_CONFIG["query_mode"],
        "parser_pipeline": RERANK_CONFIG["parser_pipeline"],
        "parent_expansion": RERANK_CONFIG["parent_expansion"],
        "kb_id": index["kb_id"],
        "generation": index["generation"],
        "chunk_hash_summary": {
            "unique": len(set(chunk_hashes)),
            "count": len(chunk_hashes),
        },
    }


def _reranker_audit() -> dict[str, Any]:
    env_model = (os.environ.get("RERANK_MODEL") or "").strip()
    return {
        "existing_interface": False,
        "provider": None,
        "configured_model": env_model or None,
        "exact_model_name": env_model or None,
        "endpoint": None,
        "supports_batch": None,
        "max_documents": None,
        "score_range": None,
        "deterministic_parameters": None,
        "fallback_behavior": "disabled (RERANK_FALLBACK_ENABLED must be false)",
        "secret_source": "RERANK_API_KEY / provider env (never logged)",
        "readiness": bool(env_model),
        "blocked_reason": (
            None if env_model else "missing rerank model configuration (RERANK_MODEL unset)"
        ),
        "notes": [
            "Production default remains RERANK_ENABLED=false",
            "RerankerProvider interface implemented in reranker.py (provider-neutral)",
            "Exact model name required; latest/auto aliases rejected",
            (
                "Phase 4D-R2: variable-size candidate contract accepted; "
                "C007=19 rows (one pre-existing duplicate chunk_id row at "
                "original ranks 2/5), N001=20, N002=19 are all valid frozen "
                "inputs; qwen3-rerank preserved every input row"
            ),
            "Provider Request Cache keyed by request_payload_hash (exact request semantics)",
            "Legacy cache entries (old key with commit/config hash) remain readable",
        ],
    }


def main() -> int:
    manifests = EXPERIMENT_ROOT / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    pool = _candidate_pool_manifest()
    (manifests / "candidate_pool_manifest.json").write_text(
        json.dumps(pool, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    audit = _reranker_audit()
    (EXPERIMENT_ROOT / "reranker_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(pool, ensure_ascii=False, indent=2))
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Phase 5 frozen baseline manifest (hash checks; fail fast on mismatch)."""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any

from .config import (
    CANDIDATE_POOL_PATH,
    CANDIDATE_POOL_SHA256,
    FROZEN_INDEX_MANIFEST,
    GOLDEN_SET_PATH,
    PHASE4_ANSWERS_CN0,
    PHASE5_ROOT,
    PHASE5_CONFIG,
    SOURCE_COMMIT,
    category_manifest_path,
    prompt_bundle_path,
    sha256_file,
)
from .grounded_answer.core import load_prompt_bundle


def _category_manifest() -> dict[str, Any]:
    from evaluation.experiments.parser_backend.config import QUESTION_CATEGORIES

    payload = {
        "version": "phase4-canonical-categories-v1",
        "categories": dict(QUESTION_CATEGORIES),
    }
    path = category_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def build_baseline_manifest() -> dict[str, Any]:
    pool_sha = sha256_file(CANDIDATE_POOL_PATH)
    if pool_sha != CANDIDATE_POOL_SHA256:
        raise RuntimeError(
            f"frozen candidate pool SHA256 mismatch: {pool_sha} != {CANDIDATE_POOL_SHA256}"
        )
    golden_sha = sha256_file(GOLDEN_SET_PATH)
    index = json.loads(FROZEN_INDEX_MANIFEST.read_text(encoding="utf-8"))
    prompt_bundle = load_prompt_bundle()
    category = _category_manifest()
    answers_sha = sha256_file(PHASE4_ANSWERS_CN0)
    manifest = {
        "source_phase": "Phase 4D-R2",
        "source_commit": SOURCE_COMMIT,
        "parser_pipeline": PHASE5_CONFIG["parser_pipeline"],
        "query_mode": PHASE5_CONFIG["query_mode"],
        "top_k": PHASE5_CONFIG["top_k"],
        "chunk_top_k": PHASE5_CONFIG["chunk_top_k"],
        "parent_expansion": PHASE5_CONFIG["parent_expansion"],
        "rerank": PHASE5_CONFIG["rerank_enabled"],
        "answer_model": PHASE5_CONFIG["answer_model"],
        "fallback_enabled": PHASE5_CONFIG["fallback_enabled"],
        "thinking_enabled": PHASE5_CONFIG["thinking_enabled"],
        "golden_set": {
            "path": str(GOLDEN_SET_PATH),
            "sha256": golden_sha,
        },
        "frozen_candidate_pool": {
            "path": str(CANDIDATE_POOL_PATH),
            "sha256": pool_sha,
        },
        "phase4_frozen_index": {
            "kb_id": index["kb_id"],
            "generation": index["generation"],
            "points": index["points"],
            "role": index.get("index_role"),
        },
        "prompt_bundle": {
            "path": str(prompt_bundle_path()),
            "sha256": prompt_bundle["sha256"],
            "version": prompt_bundle["version"],
        },
        "answer_baseline_results": {
            "path": str(PHASE4_ANSWERS_CN0),
            "sha256": answers_sha,
        },
        "canonical_metric_definition_version": "phase5-metrics-v1",
        "canonical_category_manifest": {
            "path": str(category_manifest_path()),
            "sha256": sha256_file(category_manifest_path()),
            "version": category["version"],
        },
    }
    (PHASE5_ROOT / "baseline_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    manifest = build_baseline_manifest()
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

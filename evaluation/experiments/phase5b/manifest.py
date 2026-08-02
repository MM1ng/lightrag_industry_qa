"""Phase 5B baseline and prompt manifests (hash checks; fail fast)."""

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
    PHASE5B_ROOT,
    SOURCE_COMMIT,
    sha256_file,
)
from .lite import load_prompt, sha256_text


def build_manifests() -> dict[str, Any]:
    pool_sha = sha256_file(CANDIDATE_POOL_PATH)
    if pool_sha != CANDIDATE_POOL_SHA256:
        raise RuntimeError(f"candidate pool SHA256 mismatch: {pool_sha}")
    frozen = json.loads(
        (PHASE5B_ROOT / "config" / "frozen_common.json").read_text(encoding="utf-8")
    )
    inline_prompt = load_prompt("inline_citation_prompt.txt")
    repair_prompt = load_prompt("citation_repair_prompt.txt")
    prompt_manifest = {
        "inline_citation_prompt": {
            "file": "prompts/inline_citation_prompt.txt",
            "sha256": sha256_text(inline_prompt),
        },
        "citation_repair_prompt": {
            "file": "prompts/citation_repair_prompt.txt",
            "sha256": sha256_text(repair_prompt),
        },
        "frozen_common_config": {
            "file": "config/frozen_common.json",
            "sha256": hashlib.sha256(
                json.dumps(frozen, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        },
    }
    (PHASE5B_ROOT / "prompts" / "prompt_manifest.json").write_text(
        json.dumps(prompt_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    index = json.loads(FROZEN_INDEX_MANIFEST.read_text(encoding="utf-8"))
    baseline = {
        "source_phase": "Phase 5",
        "source_commit": SOURCE_COMMIT,
        "head_commit": SOURCE_COMMIT,
        **frozen,
        "golden_set": {
            "path": str(GOLDEN_SET_PATH),
            "sha256": sha256_file(GOLDEN_SET_PATH),
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
        "prompts": prompt_manifest,
        "gl0_reuse": {
            "source": "Phase 4D-R2 R0 answers (current_rows, same prompt/pool/policy)",
            "verified_by": "phase4 answer cache key match per question",
        },
    }
    (PHASE5B_ROOT / "baseline_manifest.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return baseline


def main() -> int:
    baseline = build_manifests()
    print(json.dumps(baseline, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

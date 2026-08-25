"""Phase 4D-R2 finalize: safe post-run finalization (no API, no overwrite).

The authoritative decision files are produced by ``run_rerank.py`` after the
offline ablation (and stage 2 when the gates pass). This module only
regenerates the result manifest from the already-computed artifacts so a
stale run can never overwrite a completed evaluation.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from .config import CANDIDATE_POOL_PATH, EXPERIMENT_ROOT


def main() -> int:
    final_path = EXPERIMENT_ROOT / "final_rerank.json"
    if not final_path.is_file():
        print("final_rerank.json absent; run run_rerank.py first")
        return 1
    final = json.loads(final_path.read_text(encoding="utf-8"))
    result_manifest: dict[str, Any] = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": final.get("status"),
        "phase": "Phase 4D-R2",
        "candidate_pool_sha256": hashlib.sha256(CANDIDATE_POOL_PATH.read_bytes()).hexdigest(),
        "baseline_metrics": final.get("baseline_metrics"),
        "rerank_metrics": final.get("rerank_metrics"),
        "completeness": final.get("completeness"),
        "movement": final.get("movement"),
        "offline_gates": final.get("offline_gates"),
        "reranker_audit": json.loads(
            (EXPERIMENT_ROOT / "reranker_audit.json").read_text(encoding="utf-8")
        ),
    }
    (EXPERIMENT_ROOT / "manifests" / "result_manifest.json").write_text(
        json.dumps(result_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result_manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

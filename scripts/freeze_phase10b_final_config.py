"""Freeze the Phase 10B configuration before the one-time holdout run."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


def main() -> int:
    golden = Path("evaluation/phase10/expanded_golden_set.jsonl")
    payload = {
        "config_id": "phase10b-final-001",
        "dataset_sha256": hashlib.sha256(golden.read_bytes()).hexdigest(),
        "generation_id": "a2d1c77ce08b414495e9d845cc42f799",
        "knowledge_base_id": "8fce4626859d44abb70a9ae5b0372cea",
        "normalization_enabled": True,
        "query_mode": "naive",
        "top_k": 12,
        "chunk_top_k": 20,
        "rerank_enabled": False,
        "evidence_selection": "frozen_phase10a",
        "refusal_strategy": "frozen_phase10a",
        "citation_binding": "deterministic_phase10b",
        "chunking": "frozen_phase10a_pymupdf_standard_adapter",
        "candidate_chunking_run": False,
        "candidate_chunking_decision": "not_justified: dominant development/validation failures are cross-page evidence mapping and refusal/evidence selection, not broad Top20 retrieval miss",
        "holdout_run_count": 0,
        "source_git_commit": subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip(),
        "frozen_before_holdout": True,
    }
    output = Path("evaluation/phase10/final_config_manifest.json")
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Write the auditable Phase 10B-3G experiment commit lineage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evaluation" / "phase10b3g" / "experiment_commit_lineage.json"


def main() -> int:
    config = {
        "model": "qwen-plus-2025-07-28",
        "fallback": False,
        "cache": False,
        "mode": "naive",
        "top_k": 12,
        "chunk_top_k": 20,
        "rerank": False,
        "embedding": "text-embedding-v4",
        "embedding_dim": 1024,
        "candidate_generation_id": "5bca792c08fcf2f7b08cbaed09b6d525",
    }
    config_sha = hashlib.sha256(
        json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    rows = [
        {
            "experiment_id": "10b3f-audit",
            "base_commit": "9fb882a",
            "code_commit": "dd9ac76",
            "changed_files": ["src/industrial_rag/answer_grounding.py", "src/industrial_rag/retrieval_trace.py"],
            "runtime_config_sha": config_sha,
            "candidate_generation_id": config["candidate_generation_id"],
            "evaluation_run_id": "phase10b3f-audit-capture",
            "development_result": "audit_capture_gate_passed",
            "validation_result": "audit_capture_gate_passed",
            "accepted": True,
            "rejection_reason": None,
        },
        *[
            {
                "experiment_id": experiment_id,
                "base_commit": "9fb882a",
                "code_commit": "9fb882a",
                "changed_files": [],
                "runtime_config_sha": config_sha,
                "candidate_generation_id": config["candidate_generation_id"],
                "evaluation_run_id": "phase10b3e-final2",
                "development_result": "not_independently_reproducible",
                "validation_result": "not_independently_reproducible",
                "accepted": False,
                "rejection_reason": "No independently recorded experiment commit; final combination was committed together.",
            }
            for experiment_id in ("E1", "E2", "E3", "E4")
        ],
        {
            "experiment_id": "final-52",
            "base_commit": "9fb882a",
            "code_commit": "9fb882a",
            "changed_files": ["evaluation/phase10b3e/development_results.jsonl", "evaluation/phase10b3e/validation_results.jsonl"],
            "runtime_config_sha": config_sha,
            "candidate_generation_id": config["candidate_generation_id"],
            "evaluation_run_id": "phase10b3e-final2",
            "development_result": "52_records",
            "validation_result": "52_records",
            "accepted": False,
            "rejection_reason": "Quality gates failed; Candidate remains inactive.",
        },
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"config": config, "runtime_config_sha": config_sha, "experiments": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

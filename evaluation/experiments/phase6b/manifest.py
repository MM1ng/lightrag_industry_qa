"""Phase 6B manifests (baseline / environment / artifact / result)."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .config import (
    CANDIDATE_POOL_PATH,
    CANDIDATE_POOL_SHA256,
    GOLDEN_SET_PATH,
    PHASE6B_ROOT,
    PROJECT_ROOT,
    SOURCE_COMMIT,
    sha256_file,
)


def _git(cmd: list[str]) -> str:
    try:
        return (
            subprocess.run(
                ["git", *cmd], capture_output=True, text=True, cwd=str(PROJECT_ROOT)
            )
            .stdout.strip()
        )
    except Exception:
        return "unknown"


def _artifact_manifest() -> dict[str, Any]:
    files = [
        "baseline_manifest.json",
        "parity/harness_traces.jsonl",
        "parity/fastapi_traces.jsonl",
        "parity/per_question_diff.jsonl",
        "parity/stage_diff_summary.json",
        "metric_audit/retrieval_metric_definitions.json",
        "metric_audit/citation_metric_definitions.json",
        "metric_audit/denominator_audit.json",
        "metric_audit/recomputed_metrics.json",
        "regression/citation_regressions.json",
        "regression/refusal_regressions.json",
        "regression/context_regressions.json",
        "regression/parser_regressions.json",
        "replay/saved_input_replay.jsonl",
        "replay/saved_answer_reparse.jsonl",
        "replay/replay_summary.json",
        "remediation/selected_fix.json",
        "remediation/before_after.json",
        "remediation/tests.json",
        "rc_retest/golden_results.jsonl",
        "rc_retest/metrics.json",
        "rc_retest/shadow_audit.jsonl",
        "rc_retest/release_gates.json",
        "manifests/result_manifest.json",
    ]
    return {
        "artifacts": {
            path: sha256_file(PHASE6B_ROOT / path)
            for path in files
            if (PHASE6B_ROOT / path).is_file()
        }
    }


def build_all() -> dict[str, Any]:
    if sha256_file(CANDIDATE_POOL_PATH) != CANDIDATE_POOL_SHA256:
        raise RuntimeError("frozen candidate pool SHA256 mismatch")
    release = json.loads(
        (PHASE6B_ROOT / "rc_retest" / "release_gates.json").read_text(encoding="utf-8")
    )
    baseline = {
        "source_phase": "Phase 6",
        "source_commit": SOURCE_COMMIT,
        "head_commit": _git(["rev-parse", "HEAD"]),
        "branch": _git(["branch", "--show-current"]),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "strategy": {
            "parser_pipeline": "pymupdf_standard_adapter",
            "query_mode": "mix",
            "top_k": 12,
            "chunk_top_k": 20,
            "parent_expansion": "none",
            "rerank_enabled": False,
            "context_strategy": "current_rows",
            "answer_strategy": "current",
            "answer_model": "qwen-plus-2025-07-28",
            "fallback_enabled": False,
            "thinking_enabled": False,
        },
        "frozen_candidate_pool": {
            "path": str(CANDIDATE_POOL_PATH),
            "sha256": CANDIDATE_POOL_SHA256,
        },
        "golden_set": {
            "path": str(GOLDEN_SET_PATH),
            "sha256": sha256_file(GOLDEN_SET_PATH),
        },
        "release_candidate_approved": release["release_candidate_approved"],
        "failed_gates": release["failed_gates"],
    }
    (PHASE6B_ROOT / "baseline_manifest.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    environment = {
        "python_version": platform.python_version(),
        "operating_system": f"{platform.system()} {platform.release()}",
        "conda_env": os.environ.get("CONDA_DEFAULT_ENV", "industrial-rag"),
        "git": {"commit": baseline["head_commit"], "branch": baseline["branch"]},
        "secrets": {
            "api_key_configured": bool(os.environ.get("DASHSCOPE_API_KEY")),
            "api_key_source": "DASHSCOPE_API_KEY",
        },
    }
    (PHASE6B_ROOT / "manifests").mkdir(parents=True, exist_ok=True)
    (PHASE6B_ROOT / "manifests" / "environment_manifest.json").write_text(
        json.dumps(environment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    artifacts = _artifact_manifest()
    (PHASE6B_ROOT / "manifests" / "artifact_manifest.json").write_text(
        json.dumps(artifacts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    result_manifest = {
        "created_at": baseline["created_at"],
        "phase": "Phase 6B",
        "head_commit": baseline["head_commit"],
        "release_candidate_approved": release["release_candidate_approved"],
        "failed_gates": release["failed_gates"],
        "release_gates": release,
        "recomputed_metrics": json.loads(
            (PHASE6B_ROOT / "metric_audit" / "recomputed_metrics.json").read_text(
                encoding="utf-8"
            )
        ),
        "selected_fix": json.loads(
            (PHASE6B_ROOT / "remediation" / "selected_fix.json").read_text(encoding="utf-8")
        ),
        "sanitization": {
            "api_key_logged": False,
            "authorization_header_logged": False,
            "workspace_endpoint_logged": False,
        },
    }
    (PHASE6B_ROOT / "manifests" / "result_manifest.json").write_text(
        json.dumps(result_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return baseline


def main() -> int:
    baseline = build_all()
    print(json.dumps(baseline, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

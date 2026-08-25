"""Phase 6 manifests: environment, artifacts, baseline, frozen strategy, timeouts."""

from __future__ import annotations

import hashlib
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
    FROZEN_INDEX_MANIFEST,
    GOLDEN_SET_PATH,
    GOLDEN_SHA256,
    PHASE6_ROOT,
    PROJECT_ROOT,
    PROMPT_BUNDLE_PATH,
    SOURCE_COMMIT,
    sha256_file,
)


def _git(cmd: list[str]) -> str:
    try:
        return (
            subprocess.run(
                ["git", *cmd],
                capture_output=True,
                text=True,
                cwd=str(PROJECT_ROOT),
            )
            .stdout.strip()
        )
    except Exception:
        return "unknown"


def _package_version(name: str) -> str | None:
    try:
        from importlib import metadata

        return metadata.version(name)
    except Exception:
        return None


def _runtime_timeout_budget() -> dict[str, Any]:
    runtime = json.loads(
        (PHASE6_ROOT / "config" / "runtime.json").read_text(encoding="utf-8")
    )
    return {
        "total_request_budget_seconds": runtime["total_timeout_budget_seconds"],
        "embedding_timeout_seconds": runtime["embedding_timeout_seconds"],
        "retrieval_timeout_seconds": runtime["retrieval_timeout_seconds"],
        "llm_timeout_seconds": runtime["llm_timeout_seconds"],
        "request_timeout_seconds": runtime["request_timeout_seconds"],
        "max_retries": runtime["max_retries"],
        "concurrency_limit": runtime["concurrency_limit"],
        "qdrant_connect_timeout_seconds": runtime["qdrant_connect_timeout_seconds"],
        "http_connection_pool_max": runtime["http_connection_pool_max"],
        "rationale": {
            "total_budget": (
                "Hard ceiling for one QA request; sub-stages (embedding 60s, "
                "retrieval 120s, LLM 150s) cannot stack beyond 180s because the "
                "runtime query future is cancelled at the total budget."
            ),
            "llm_retries": (
                "max_retries=2, no model fallback; retries are counted and "
                "audited, and a timeout never switches models."
            ),
            "concurrency": (
                "Per-runtime asyncio lock serializes queries to one KB runtime; "
                "API-level concurrency is bounded by uvicorn and the runtime "
                "manager cache (max 8 runtimes)."
            ),
        },
        "source": "evaluation/experiments/phase6/config/runtime.json",
    }


def _environment_manifest() -> dict[str, Any]:
    versions = {
        "lightrag": _package_version("lightrag-hku"),
        "fastapi": _package_version("fastapi"),
        "uvicorn": _package_version("uvicorn"),
        "qdrant_client": _package_version("qdrant-client"),
        "pydantic": _package_version("pydantic"),
        "sqlalchemy": _package_version("sqlalchemy"),
        "alembic": _package_version("alembic"),
        "openai": _package_version("openai"),
        "httpx": _package_version("httpx"),
        "streamlit": _package_version("streamlit"),
    }
    qdrant_server = None
    try:
        import httpx

        url = os.environ.get("QDRANT_URL", "http://127.0.0.1:16333")
        response = httpx.get(url, timeout=5)
        if response.status_code == 200:
            qdrant_server = response.json().get("version")
    except Exception:
        qdrant_server = None
    return {
        "python_version": platform.python_version(),
        "operating_system": f"{platform.system()} {platform.release()}",
        "conda_env": os.environ.get("CONDA_DEFAULT_ENV", "industrial-rag"),
        "python_executable": str(Path(sys.executable).resolve()),
        "versions": versions,
        "qdrant_server_version": qdrant_server,
        "git": {
            "commit": _git(["rev-parse", "HEAD"]),
            "branch": _git(["branch", "--show-current"]),
        },
        "hashes": {
            "config_runtime": sha256_file(PHASE6_ROOT / "config" / "runtime.json"),
            "config_observability": sha256_file(
                PHASE6_ROOT / "config" / "observability.json"
            ),
            "config_safety": sha256_file(PHASE6_ROOT / "config" / "safety.json"),
            "config_release_gates": sha256_file(
                PHASE6_ROOT / "config" / "release_gates.json"
            ),
            "prompt_bundle": sha256_file(PROMPT_BUNDLE_PATH),
            "golden_set": GOLDEN_SHA256,
            "frozen_strategy": sha256_file(PHASE6_ROOT / "frozen_strategy.json"),
        },
        "secrets": {
            "api_key_configured": bool(os.environ.get("DASHSCOPE_API_KEY")),
            "api_key_source": "DASHSCOPE_API_KEY",
            "service_api_key_configured": bool(os.environ.get("SERVICE_API_KEY")),
            "qdrant_api_key_configured": bool(os.environ.get("QDRANT_API_KEY")),
            "qdrant_url": (
                hashlib.sha256(os.environ["QDRANT_URL"].encode()).hexdigest()
                if os.environ.get("QDRANT_URL")
                else None
            ),
        },
    }


def _artifact_manifest() -> dict[str, Any]:
    files = [
        "frozen_strategy.json",
        "baseline_manifest.json",
        "config/runtime.json",
        "config/observability.json",
        "config/safety.json",
        "config/release_gates.json",
        "shadow_audit/citation_audit.jsonl",
        "shadow_audit/validation_summary.json",
        "shadow_audit/metrics.json",
        "e2e/golden_results.jsonl",
        "e2e/robustness_results.jsonl",
        "e2e/smoke_results.jsonl",
        "e2e/metrics.json",
        "load/raw_results.jsonl",
        "load/summary.json",
        "manifests/environment_manifest.json",
        "manifests/artifact_manifest.json",
    ]
    return {
        "artifacts": {
            path: sha256_file(PHASE6_ROOT / path)
            for path in files
            if (PHASE6_ROOT / path).is_file()
        }
    }


def build_all() -> dict[str, Any]:
    if sha256_file(CANDIDATE_POOL_PATH) != CANDIDATE_POOL_SHA256:
        raise RuntimeError("frozen candidate pool SHA256 mismatch")
    frozen = json.loads(
        (PHASE6_ROOT / "frozen_strategy.json").read_text(encoding="utf-8")
    )
    frozen["actual_run_commit"] = _git(["rev-parse", "HEAD"])
    frozen["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    (PHASE6_ROOT / "frozen_strategy.json").write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (PHASE6_ROOT / "manifests").mkdir(parents=True, exist_ok=True)
    environment = _environment_manifest()
    (PHASE6_ROOT / "manifests" / "environment_manifest.json").write_text(
        json.dumps(environment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    artifacts = _artifact_manifest()
    (PHASE6_ROOT / "manifests" / "artifact_manifest.json").write_text(
        json.dumps(artifacts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    index = json.loads(FROZEN_INDEX_MANIFEST.read_text(encoding="utf-8"))
    baseline = {
        "source_phase": "Phase 5B",
        "source_commit": SOURCE_COMMIT,
        "head_commit": frozen["actual_run_commit"],
        **{k: v for k, v in frozen.items() if k not in ("path", "sha256")},
        "phase4_frozen_index": {
            "kb_id": index["kb_id"],
            "generation": index["generation"],
            "points": index["points"],
            "role": index.get("index_role"),
        },
        "runtime_timeout_budget": _runtime_timeout_budget(),
    }
    (PHASE6_ROOT / "baseline_manifest.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (PHASE6_ROOT / "runtime_timeout_budget.json").write_text(
        json.dumps(_runtime_timeout_budget(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "frozen_strategy": frozen,
        "environment": environment,
        "artifacts": artifacts,
    }


def main() -> int:
    result = build_all()
    print(json.dumps(result, ensure_ascii=False, indent=2)[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(main())

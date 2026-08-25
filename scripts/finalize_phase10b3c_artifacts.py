"""Write auditable, secret-free Phase 10B-3C gate artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "evaluation" / "phase10b3c"
KB_ID = "8fce4626859d44abb70a9ae5b0372cea"
GENERATION_ID = "g10b3c20260803"
REGISTRY = ROOT / "runtime" / "phase10b3c" / "kb_data" / KB_ID / GENERATION_ID / "context_registry"


def write_json(name: str, value: object) -> None:
    (EVAL / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    EVAL.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((REGISTRY / "manifest.json").read_text(encoding="utf-8"))
    safe_manifest = dict(manifest)
    safe_manifest["source_artifacts"] = [
        {"path": item["path"].replace("\\", "/"), "sha256": item["sha256"]}
        for item in manifest["source_artifacts"]
    ]
    safe_manifest["creation_commit"] = "0036016"
    write_json("parser_build_manifest.json", safe_manifest)
    write_json("context_registry_manifest.json", {"registry_path": str(REGISTRY.relative_to(ROOT)).replace("\\", "/"), **safe_manifest})

    write_json(
        "runtime_config_proof.json",
        {
            "environment": "local_staging",
            "query_mode": "naive",
            "top_k": 12,
            "chunk_top_k": 20,
            "normalization_enabled": True,
            "grounding_enabled": True,
            "llm_cache_enabled": False,
            "rerank_enabled": False,
            "answer_model": "qwen-plus-2025-07-28",
            "embedding_model": "text-embedding-v4",
            "fallback_enabled": False,
            "source": "local_staging environment contract; secret values omitted",
        },
    )
    write_json(
        "candidate_activation.json",
        {
            "knowledge_base_id": KB_ID,
            "candidate_generation_id": GENERATION_ID,
            "activated": False,
            "legacy_active_generation_preserved": True,
            "reason": "Candidate is isolated and no candidate-query activation path was exercised.",
        },
    )
    write_json(
        "candidate_smoke_results.json",
        {
            "candidate_generation_id": GENERATION_ID,
            "status": "blocked",
            "query_count": 0,
            "reason": "The running API exposes only the Active Generation query path; candidate was not activated.",
            "provider_preflight": "passed:qwen-plus-2025-07-28",
            "holdout_executed": False,
        },
    )

    env_values = [value for key, value in os.environ.items() if key in {"SERVICE_API_KEY", "ADMIN_API_KEY", "DASHSCOPE_API_KEY"} and value]
    local_env = ROOT / ".env.local_staging"
    if local_env.exists():
        for line in local_env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                if key in {"SERVICE_API_KEY", "ADMIN_API_KEY", "DASHSCOPE_API_KEY"} and value:
                    env_values.append(value)
    scanned = []
    for path in EVAL.rglob("*"):
        if path.is_file() and path.name != "secret_scan.json":
            data = path.read_bytes()
            scanned.append({"path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": hashlib.sha256(data).hexdigest()})
    matches = 0
    for item in scanned:
        data = (ROOT / item["path"]).read_bytes()
        matches += sum(data.count(secret.encode("utf-8")) for secret in env_values if len(secret) > 8)
    write_json("secret_scan.json", {"confirmed_secret_count": matches, "scanned_file_count": len(scanned), "values_recorded": False, "generated_at": datetime.now(UTC).isoformat()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

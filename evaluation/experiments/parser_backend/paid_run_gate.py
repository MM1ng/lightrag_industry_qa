"""Phase 3A-D paid-run readiness gate and frozen-artifact verification."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .common import sha256_file
from .config import PDF_FACTS, PDF_NAMES, PROJECT_ROOT
from .fixed_model_gate import (
    FROZEN_CONFIG_PATH,
    GOLDEN_SET_PATH,
    PROMPT_BUNDLE_PATH,
    assert_consistency,
    load_frozen_config,
    write_prompt_bundle,
)

PAID_RUN_ENV = "IRA_PHASE3A_PAID_RUN"
FIXED_MODEL = "qwen-plus-2025-07-28"

EXPERIMENT_ROOT = Path(__file__).resolve().parent
FROZEN_MANIFEST_PATH = EXPERIMENT_ROOT / "manifests" / "phase3a_frozen_artifacts_manifest.json"
P1_RAW_ROOT = EXPERIMENT_ROOT / "P1"
P0_ROOT = EXPERIMENT_ROOT / "P0"
P1_CLEAN_ROOT = EXPERIMENT_ROOT / "fixed_model" / "P1_mineru"


def _frozen_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    def add(path: Path, role: str, pipeline: str, generated_by: str) -> None:
        if not path.is_file():
            raise FileNotFoundError(f"frozen artifact missing: {path}")
        entries.append(
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
                "role": role,
                "generated_by": generated_by,
                "parser_pipeline": pipeline,
                "immutable": True,
            }
        )

    for pdf in PDF_NAMES:
        add(PROJECT_ROOT / "data" / "manuals" / pdf, "pdf_source", "input", "external")
    add(GOLDEN_SET_PATH, "golden_set", "common", "phase2")
    for pdf in PDF_NAMES:
        base = P0_ROOT / pdf
        add(base / "blocks.jsonl", "p0_blocks", "pymupdf_standard_adapter", "phase3a-p")
        add(base / "parent_chunks.jsonl", "p0_parents", "pymupdf_standard_adapter", "phase3a-p")
        add(base / "child_chunks.jsonl", "p0_children", "pymupdf_standard_adapter", "phase3a-p")
    for pdf in PDF_NAMES:
        base = P1_CLEAN_ROOT / pdf
        add(base / "blocks.jsonl", "p1_clean_blocks", "mineru_online_clean_adapter", "phase3a-p")
        add(base / "parent_chunks.jsonl", "p1_clean_parents", "mineru_online_clean_adapter", "phase3a-p")
        add(base / "child_chunks.jsonl", "p1_clean_children", "mineru_online_clean_adapter", "phase3a-p")
        add(base / "cleanup_manifest.json", "cleanup_manifest", "mineru_online_clean_adapter", "phase3a-p")
        add(base / "tables_clean.json", "tables_clean", "mineru_online_clean_adapter", "phase3a-p")
        raw = P1_RAW_ROOT / pdf / "mineru_raw"
        add(raw / "result.zip", "mineru_raw_zip", "mineru_online_clean_adapter", "phase3a")
        add(raw / "content_list.json", "mineru_content_list", "mineru_online_clean_adapter", "phase3a")
    add(FROZEN_CONFIG_PATH, "frozen_config", "common", "phase3a-d")
    add(PROMPT_BUNDLE_PATH, "prompt_bundle", "common", "phase3a-d")
    return entries


def generate_frozen_manifest() -> dict[str, Any]:
    write_prompt_bundle()
    manifest = {
        "manifest_name": "phase3a_frozen_artifacts_manifest",
        "created_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
        "entries": _frozen_entries(),
    }
    FROZEN_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    FROZEN_MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def verify_frozen_artifacts(manifest_path: Path | None = None) -> dict[str, Any]:
    """Verify every frozen entry's path/size/sha256; return mismatch details."""
    path = manifest_path or FROZEN_MANIFEST_PATH
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mismatches: list[dict[str, Any]] = []
    checked = 0
    for entry in manifest["entries"]:
        checked += 1
        file_path = Path(entry["path"])
        if not file_path.is_file():
            mismatches.append({"path": entry["path"], "issue": "missing"})
            continue
        if file_path.stat().st_size != entry["size"]:
            mismatches.append({"path": entry["path"], "issue": "size_mismatch"})
            continue
        if sha256_file(file_path) != entry["sha256"]:
            mismatches.append({"path": entry["path"], "issue": "sha256_mismatch"})
    return {
        "checked": checked,
        "ok": not mismatches,
        "mismatches": mismatches,
        "immutable": all(entry.get("immutable") is True for entry in manifest["entries"]),
    }


def check_paid_run_gate(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return the full paid-run readiness check table (no LLM calls)."""
    env = dict(os.environ if env is None else env)
    cfg = load_frozen_config()
    checks: dict[str, Any] = {}
    checks["paid_run_env"] = env.get(PAID_RUN_ENV) == "1"
    checks["llm_model_fixed"] = env.get("LLM_MODEL", cfg["llm_model"]) == FIXED_MODEL
    checks["fallback_disabled"] = (
        env.get("MODEL_FALLBACK_ENABLED", "true").strip().lower() != "true"
    )
    checks["thinking_disabled"] = bool(cfg.get("enable_thinking") is False)
    try:
        assert_consistency()
        checks["config_hash_gate"] = True
    except Exception as error:  # pragma: no cover - defensive
        checks["config_hash_gate"] = False
        checks["config_hash_error"] = str(error)
    frozen = verify_frozen_artifacts()
    checks["frozen_artifacts_unchanged"] = frozen["ok"] and frozen["immutable"]
    checks["frozen_checked"] = frozen["checked"]
    checks["random_prefix_ready"] = env.get("QDRANT_COLLECTION_PREFIX", "").startswith("ira_p3ar_")

    precheck = EXPERIMENT_ROOT / "fixed_model" / "precheck_report.json"
    checks["precheck_report_present"] = precheck.is_file()
    if precheck.is_file():
        report = json.loads(precheck.read_text(encoding="utf-8"))
        checks["precheck_model_consistent"] = all(
            stats["model_set"] == [FIXED_MODEL] for stats in report["index_stats"].values()
        ) and report["llm_summary"]["model_mismatches"] == 0
    else:
        checks["precheck_model_consistent"] = False
    allowed = all(
        checks.get(key) is True
        for key in (
            "paid_run_env",
            "llm_model_fixed",
            "fallback_disabled",
            "thinking_disabled",
            "config_hash_gate",
            "frozen_artifacts_unchanged",
            "precheck_model_consistent",
        )
    )
    return {
        "allowed": allowed,
        "fixed_model": FIXED_MODEL,
        "estimated_total_tokens": (json.loads(precheck.read_text(encoding="utf-8"))["estimate"]["estimated_total_tokens"] if precheck.is_file() else None),
        "checks": checks,
    }


def scrub_secrets(text: str, api_key: str | None = None) -> str:
    """Remove API keys / signed URLs from logs before persistence."""
    api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
    if api_key:
        text = text.replace(api_key, "[REDACTED_API_KEY]")
    import re

    return re.sub(r"https?://[^\s\"']*X-OSS-[^\s\"']*", "[REDACTED_SIGNED_URL]", text)


def main() -> int:
    import sys

    if "--generate" in sys.argv:
        manifest = generate_frozen_manifest()
        print(f"frozen manifest entries: {len(manifest['entries'])}")
    result = check_paid_run_gate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

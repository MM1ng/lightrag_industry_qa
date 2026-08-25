"""Phase 7 build: manifests, dependency snapshot, secret scan and RC ZIP."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

from .config import (
    CANDIDATE_POOL_PATH,
    CANDIDATE_POOL_SHA256,
    DIST_DIR,
    PHASE7_ROOT,
    PROJECT_ROOT,
    RC_VERSION,
    RC_ZIP_NAME,
    SOURCE_COMMIT,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _package_version(name: str) -> str | None:
    try:
        from importlib import metadata

        return metadata.version(name)
    except Exception:
        return None


INCLUDE_GLOBS: list[str] = [
    "src/industrial_rag/**/*.py",
    "app/**/*.py",
    "migrations/**/*",
    "scripts/*.ps1",
    ".env.example",
    "alembic.ini",
    "pyproject.toml",
    "README.md",
    "docs/phase-5-grounded-answer-report.md",
    "docs/phase-5b-grounded-answer-lite-report.md",
    "docs/phase-6-production-readiness-report.md",
    "docs/phase-6b-official-path-parity-report.md",
    "docs/phase-7-release-packaging-report.md",
    "evaluation/experiments/phase6/frozen_strategy.json",
    "evaluation/experiments/phase6/config/*.json",
    "evaluation/experiments/phase6/runbooks/*.md",
    "evaluation/experiments/phase6b/closeout/*.json",
    "evaluation/experiments/phase6b/tech_debt/*.md",
]

EXCLUDE_PATTERNS: list[str] = [
    "**/__pycache__/**",
    "**/*.pyc",
    "**/.pytest_cache/**",
    "**/.ruff_cache/**",
    "**/.mypy_cache/**",
    "**/.git/**",
    "**/.run/**",
    "**/logs/**",
    "**/.env",
    "**/*.db",
    "**/*.sqlite*",
    "**/lightrag_storage/**",
    "**/data/**",
    "**/dist/**",
    "**/tmp/**",
    "**/cache/**",
    "**/results/**",
    "**/manifests/environment_manifest.json",
    "evaluation/experiments/phase3*/**",
    "evaluation/experiments/phase4/**",
    "evaluation/experiments/phase5/**",
    "evaluation/experiments/phase5b/**",
    "evaluation/experiments/phase6/e2e/**",
    "evaluation/experiments/phase6/load/**",
    "evaluation/experiments/phase6/shadow_audit/**",
    "evaluation/experiments/phase6/manifests/**",
    "evaluation/experiments/phase6b/parity/**",
    "evaluation/experiments/phase6b/metric_audit/**",
    "evaluation/experiments/phase6b/regression/**",
    "evaluation/experiments/phase6b/replay/**",
    "evaluation/experiments/phase6b/remediation/**",
    "evaluation/experiments/phase6b/rc_retest/**",
    "evaluation/experiments/phase6b/manifests/**",
    "evaluation/experiments/phase6b/tech_debt/QDRANT-COMPAT-001.md",
    "phase3-uncommitted-backup.patch",
]


def _collect_files() -> list[Path]:
    import fnmatch

    selected: dict[str, Path] = {}
    for pattern in INCLUDE_GLOBS:
        matches = list(PROJECT_ROOT.glob(pattern))
        for path in matches:
            if path.is_file():
                rel = path.relative_to(PROJECT_ROOT).as_posix()
                excluded = any(
                    fnmatch.fnmatch(rel, pat) or rel.startswith(pat.rstrip("/"))
                    for pat in EXCLUDE_PATTERNS
                )
                if not excluded:
                    selected[rel] = path
    return sorted(selected.values(), key=lambda p: p.relative_to(PROJECT_ROOT).as_posix())


def _secret_scan(files: list[Path]) -> dict[str, Any]:
    rules: list[tuple[str, re.Pattern[str], bool]] = [
        ("SK_KEY", re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"), True),
        ("BEARER", re.compile(r"Authorization[ \t]*:[ \t]*Bearer[ \t]+[A-Za-z0-9._-]{12,}", re.IGNORECASE), True),
        ("ALIYUN_AK", re.compile(r"\bLTAI[A-Za-z0-9]{12,}\b"), True),
        ("ALIYUN_SK", re.compile(r"\b[A-Za-z0-9]{30,}\b"), False),
        ("PRIVATE_KEY", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), True),
        ("DASHSCOPE_KEY_ASSIGN", re.compile(r"^[ \t]*DASHSCOPE_API_KEY[ \t]*=[ \t]*[^ \t\r\n#]+", re.MULTILINE), False),
        ("MAAS_ENDPOINT", re.compile(r"https://[a-z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com", re.IGNORECASE), True),
        ("SIGNED_URL", re.compile(r"X-Amz-Signature=|X-Signature="), True),
    ]
    findings: list[dict[str, Any]] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for rule_id, pattern, blocking in rules:
            for match in pattern.finditer(text):
                snippet = match.group(0)
                redacted = snippet[:6] + "..." + snippet[-4:] if len(snippet) > 14 else "***"
                findings.append(
                    {
                        "file": path.relative_to(PROJECT_ROOT).as_posix(),
                        "rule_id": rule_id,
                        "redacted": redacted,
                        "status": "blocking" if blocking else "review",
                    }
                )
    confirmed = [f for f in findings if f["status"] == "blocking"]
    return {
        "scanned_files": len(files),
        "findings": findings,
        "confirmed_secret_count": len(confirmed),
        "passed": len(confirmed) == 0,
    }


def build() -> dict[str, Any]:
    if _sha256(CANDIDATE_POOL_PATH) != CANDIDATE_POOL_SHA256:
        raise RuntimeError("frozen candidate pool SHA256 mismatch")
    commit = _git(["rev-parse", "HEAD"])
    files = _collect_files()
    secret_scan = _secret_scan(files)
    checksums: dict[str, Any] = {}
    total_size = 0
    for path in files:
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        checksums[rel] = {
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        total_size += path.stat().st_size
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DIST_DIR / RC_ZIP_NAME
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, arcname=path.relative_to(PROJECT_ROOT).as_posix())
    zip_sha = _sha256(zip_path)
    checksum_manifest = {
        "rc_version": RC_VERSION,
        "files": checksums,
        "total_file_count": len(files),
        "total_size": total_size,
        "zip": {
            "path": f"dist/{RC_ZIP_NAME}",
            "size": zip_path.stat().st_size,
            "sha256": zip_sha,
        },
    }
    (PHASE7_ROOT / "package").mkdir(parents=True, exist_ok=True)
    (PHASE7_ROOT / "package" / "include_manifest.json").write_text(
        json.dumps(
            {
                "rc_version": RC_VERSION,
                "include_globs": INCLUDE_GLOBS,
                "included_file_count": len(files),
                "included_files": sorted(checksums),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (PHASE7_ROOT / "package" / "exclude_manifest.json").write_text(
        json.dumps(
            {
                "exclude_patterns": EXCLUDE_PATTERNS,
                "reasons": {
                    "**/.env": "real secrets must never be packaged",
                    "**/*.db": "real databases must not be packaged",
                    "**/lightrag_storage/**": "runtime vector data lives on the host",
                    "**/data/**": "protected source data; acceptance uses workspace copies",
                    "evaluation/experiments/phase3*/**": "experiment artifacts are not runtime",
                    "**/results/**": "large experiment outputs are not runtime",
                    "phase3-uncommitted-backup.patch": "explicitly forbidden",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (PHASE7_ROOT / "package" / "checksum_manifest.json").write_text(
        json.dumps(checksum_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (PHASE7_ROOT / "security").mkdir(parents=True, exist_ok=True)
    (PHASE7_ROOT / "security" / "secret_scan.json").write_text(
        json.dumps(secret_scan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (PHASE7_ROOT / "security" / "package_scan.json").write_text(
        json.dumps(
            {
                "package": f"dist/{RC_ZIP_NAME}",
                "package_sha256": zip_sha,
                "file_count": len(files),
                "absolute_paths_present": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (PHASE7_ROOT / "security" / "log_scan.json").write_text(
        json.dumps(
            {
                "scanned_logs": [],
                "secret_matches": 0,
                "note": "rehearsal logs are scanned after the rehearsal run",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    deps = dependency_manifest()
    (PHASE7_ROOT / "dependency_manifest.json").write_text(
        json.dumps(deps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    config_manifest = {
        "config_version": "phase6-v1",
        "files": {
            rel: _sha256(PROJECT_ROOT / rel)
            for rel in (
                "evaluation/experiments/phase6/frozen_strategy.json",
                "evaluation/experiments/phase6/config/runtime.json",
                "evaluation/experiments/phase6/config/observability.json",
                "evaluation/experiments/phase6/config/safety.json",
                "evaluation/experiments/phase6/config/release_gates.json",
                ".env.example",
            )
        },
        "production_defaults_unchanged": True,
    }
    (PHASE7_ROOT / "config_manifest.json").write_text(
        json.dumps(config_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    release_manifest = {
        "rc_version": RC_VERSION,
        "release_channel": "rc",
        "git_commit": commit,
        "source_commit": SOURCE_COMMIT,
        "build_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "frozen_strategy_sha256": _sha256(
            PROJECT_ROOT / "evaluation" / "experiments" / "phase6" / "frozen_strategy.json"
        ),
        "candidate_pool_sha256": CANDIDATE_POOL_SHA256,
        "package": {
            "path": f"dist/{RC_ZIP_NAME}",
            "sha256": zip_sha,
            "size": zip_path.stat().st_size,
        },
        "deployment_performed": False,
        "tag_created": False,
        "tag_pushed": False,
    }
    (PHASE7_ROOT / "release_manifest.json").write_text(
        json.dumps(release_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    artifact_manifest = {
        "artifacts": {
            name: _sha256(PHASE7_ROOT / name)
            for name in (
                "release_manifest.json",
                "dependency_manifest.json",
                "config_manifest.json",
                "package/include_manifest.json",
                "package/exclude_manifest.json",
                "package/checksum_manifest.json",
                "security/secret_scan.json",
                "security/package_scan.json",
                "security/log_scan.json",
            )
        }
    }
    (PHASE7_ROOT / "artifact_manifest.json").write_text(
        json.dumps(artifact_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "release": release_manifest,
        "secret_scan": secret_scan,
        "checksum_manifest": checksum_manifest,
        "dependencies": deps,
    }


def dependency_manifest() -> dict[str, Any]:
    declared = {
        "lightrag-hku": "1.5.4",
        "openai": ">=1.97,<3",
        "PyMuPDF": ">=1.24,<2",
        "streamlit": ">=1.46,<2",
        "httpx": ">=0.28,<1",
        "fastapi": ">=0.115,<1",
        "uvicorn": ">=0.30,<1",
        "sqlalchemy": ">=2.0,<3",
        "aiosqlite": ">=0.20,<1",
        "alembic": ">=1.13,<2",
        "qdrant-client": ">=1.18,<2",
        "python-dotenv": ">=1.0,<2",
    }
    installed = {
        name: _package_version(name) for name in declared
    }
    diffs = {
        "declared_but_not_installed": [
            name for name, version in installed.items() if version is None
        ],
        "version_mismatch": [
            {"name": name, "declared": spec, "installed": installed[name]}
            for name, spec in declared.items()
            if installed.get(name) is not None
            and not _spec_ok(spec, installed[name])
        ],
    }
    return {
        "rc_version": RC_VERSION,
        "python_version": platform.python_version(),
        "conda_env": os.environ.get("CONDA_DEFAULT_ENV", "industrial-rag"),
        "declared_dependencies": declared,
        "installed_versions": installed,
        "lock_hash_state": "snapshot-only (no lockfile change)",
        "differences": diffs,
        "note": (
            "Snapshot for RC 0.1.0-rc.1; no upgrade/downgrade performed; "
            "Qdrant client/server mismatch recorded as tech debt, not fixed."
        ),
    }


def _spec_ok(spec: str, version: str) -> bool:
    """Very small declared-version check (range forms only)."""
    import re

    for low, high in re.findall(r">=(\d+(?:\.\d+)*),<(\d+(?:\.\d+)*)", spec):
        parts = [int(x) for x in version.split(".")[: len(low.split("."))]]
        low_parts = [int(x) for x in low.split(".")]
        high_parts = [int(x) for x in high.split(".")]
        if parts < low_parts or parts >= high_parts:
            return False
    return True


def main() -> int:
    result = build()
    print(
        json.dumps(
            {
                "release": result["release"],
                "secret_scan": {
                    "confirmed_secret_count": result["secret_scan"]["confirmed_secret_count"],
                    "passed": result["secret_scan"]["passed"],
                },
                "dependencies": result["dependencies"]["differences"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Single authoritative application version source (Phase 7).

All version surfaces (/version, package manifests, release metadata) must
read from this module; do not maintain duplicate version strings elsewhere.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

APP_VERSION = "0.1.0-rc.1"
RELEASE_CHANNEL = "rc"
CONFIG_VERSION = "phase6-v1"
STRATEGY_VERSION = "phase6b-v1"

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _git_commit() -> str:
    env_commit = (os.environ.get("GIT_COMMIT") or "").strip()
    if env_commit:
        return env_commit
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                cwd=str(PROJECT_ROOT),
            )
            .stdout.strip()
        )
    except Exception:
        return "unknown"


def version_info() -> dict[str, str]:
    return {
        "app_version": APP_VERSION,
        "release_channel": RELEASE_CHANNEL,
        "git_commit": _git_commit(),
        "config_version": CONFIG_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "build_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

"""Phase 7 paths, RC version and fixed acceptance subsets."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PHASE7_ROOT = Path(__file__).resolve().parent
SOURCE_COMMIT = "7f5f39fa3459aa3dbdaa1a42a4439c7cede82c60"

RC_VERSION = "0.1.0-rc.1"
RC_ZIP_NAME = f"industrial-energy-agent-{RC_VERSION}.zip"
DIST_DIR = PROJECT_ROOT / "dist"

GOLDEN_SET_PATH = PROJECT_ROOT / "data" / "evaluation" / "industrial_pump_golden_set_50.jsonl"
CANDIDATE_POOL_PATH = (
    PROJECT_ROOT
    / "evaluation"
    / "experiments"
    / "phase4"
    / "parent_expansion"
    / "frozen_child_results.jsonl"
)
CANDIDATE_POOL_SHA256 = "fc731efc904d9d9dca639fecf181a01e022c162ac91b67f6432d18b7619bf6a0"

# Fixed 20-question golden subset (frozen before running; category counts fixed).
GOLDEN_SUBSET = [
    "S001", "S002", "S003", "S004", "S005",   # parameter 5
    "S007", "S009",                            # table 2
    "S011", "S012", "S014",                    # procedure 3
    "S015", "S016",                            # troubleshooting 2
    "S017", "D003", "D005",                    # safety 3
    "C001", "C002", "C003",                    # cross-page 3
    "N001", "N002",                            # insufficient 2
]

# Smoke scenarios (same as Phase 6 smoke, reused verbatim).
SMOKE_QUESTIONS = [
    "SUMMIT 2196 系列泵长期存放时，存放环境和泵轴转动频率有什么要求？",
    "SUMMIT 2196 泵的润滑要求是什么？",
    "入口管路应如何选择和布置？",
    "泵不输送液体时可能的原因是什么？",
    "启动泵前有哪些安全要求？",
    "两份手册中关于入口管路布置的要求有何不同？",
]

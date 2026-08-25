"""Build the Phase 10B development/validation failure matrix offline."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from industrial_rag.phase10b_failure_analysis import (
    ANALYZED_SPLITS,
    build_failure_matrix,
    summarize_failure_matrix,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PHASE10_DIR = PROJECT_ROOT / "evaluation/phase10"
BASELINE_RESULTS = PHASE10_DIR / "baseline_results.jsonl"
BASELINE_DIAGNOSIS = PHASE10_DIR / "baseline_diagnosis.jsonl"
GOLDEN_SET = PHASE10_DIR / "expanded_golden_set.jsonl"
MANIFEST = PHASE10_DIR / "golden_set_manifest.json"
OUTPUT_MATRIX = PHASE10_DIR / "phase10b_failure_matrix.jsonl"
OUTPUT_SUMMARY = PHASE10_DIR / "phase10b_failure_summary.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_analyzed_results(path: Path) -> list[dict[str, Any]]:
    analyzed: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            case = json.loads(line)
            if case.get("golden", {}).get("split") in ANALYZED_SPLITS:
                analyzed.append(case)
    return analyzed


def _load_diagnoses(path: Path, question_ids: set[str]) -> list[dict[str, Any]]:
    diagnoses: list[dict[str, Any]] = []
    if not path.exists():
        return diagnoses
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            diagnosis = json.loads(line)
            if diagnosis.get("question_id") in question_ids:
                diagnoses.append(diagnosis)
    return diagnoses


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    results = _load_analyzed_results(BASELINE_RESULTS)
    question_ids = {case["question_id"] for case in results}
    diagnoses = _load_diagnoses(BASELINE_DIAGNOSIS, question_ids)
    matrix = build_failure_matrix(results, diagnoses)
    if len(matrix) != 52:
        raise ValueError(f"expected exactly 52 development/validation rows, got {len(matrix)}")
    if {row["split"] for row in matrix} != ANALYZED_SPLITS:
        raise ValueError("failure matrix contains an unexpected split")
    if any(row["question_id"] not in question_ids for row in matrix):
        raise ValueError("failure matrix contains an unexpected question")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    summary = summarize_failure_matrix(matrix)
    summary.update(
        {
            "dataset_sha256": manifest["dataset_sha256"],
            "dataset_sha256_verified": manifest["dataset_sha256"] == _sha256(GOLDEN_SET),
            "source_results": "evaluation/phase10/baseline_results.jsonl",
            "source_diagnoses": "evaluation/phase10/baseline_diagnosis.jsonl",
            "source_git_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "holdout_rows_loaded": False,
            "holdout_tuning": False,
        }
    )
    _write_jsonl(OUTPUT_MATRIX, matrix)
    _write_json(OUTPUT_SUMMARY, summary)
    print(
        f"records={len(matrix)} development={sum(row['split'] == 'development' for row in matrix)} "
        f"validation={sum(row['split'] == 'validation' for row in matrix)} "
        f"holdout_rows_loaded={summary['holdout_rows_loaded']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

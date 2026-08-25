"""Consolidate isolated Phase 10B retrieval ablation manifests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--baseline-manifest", required=True)
    parser.add_argument("--development-manifest", action="append", required=True)
    parser.add_argument("--selected-validation-manifest", required=True)
    args = parser.parse_args()

    baseline = _read(Path(args.baseline_manifest))
    development = [_read(Path(path)) for path in args.development_manifest]
    validation = _read(Path(args.selected_validation_manifest))
    manifests = [*development, validation]
    if any(item.get("split") not in {"development", "validation"} for item in manifests):
        raise ValueError("ablation consolidation accepts only development/validation manifests")
    if any(item.get("holdout_used_for_tuning") for item in manifests):
        raise ValueError("holdout must not be used for ablation tuning")
    unsupported = {
        "dense": {
            "supported": False,
            "reason": "LightRAG 1.5.4 exposes mix/naive/hybrid/local/global only; no dense mode",
        },
        "keyword": {
            "supported": False,
            "reason": "LightRAG 1.5.4 exposes mix/naive/hybrid/local/global only; no keyword mode",
        },
    }
    payload = {
        "experiment_family": "phase10b-retrieval-ablation",
        "dataset_sha256": baseline["dataset_sha256"],
        "baseline": {
            "experiment_id": baseline.get("experiment_id", "phase10a-real-baseline"),
            "split": "development_validation",
            "metrics": baseline["metrics"],
        },
        "development_runs": development,
        "selected_experiment_id": validation["experiment_id"],
        "validation_selected_run": validation,
        "unsupported_requested_modes": unsupported,
        "holdout_used_for_tuning": False,
        "selection_policy": "maximize development MRR with non-worsening FRR and inspect validation before freeze",
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

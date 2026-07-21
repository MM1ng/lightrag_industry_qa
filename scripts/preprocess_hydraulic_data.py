"""Build validated cycle-level features from the protected hydraulic dataset."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from industrial_energy_agent.data_processing.hydraulic_pipeline import (
    run_hydraulic_pipeline,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_ROOT = PROJECT_ROOT / "data" / "raw_dataset" / "hydraulic_systems"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "processed" / "hydraulic"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = run_hydraulic_pipeline(args.dataset_root, args.output_dir)
    print(
        "hydraulic_preprocess=PASS "
        f"rows={result.output_rows} columns={result.output_columns} "
        f"artifact_version={result.artifact_version}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

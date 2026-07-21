"""Generate the tracked synthetic_demo business fixtures."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from industrial_energy_agent.data_processing.synthetic_generator import (
    generate_synthetic_data,
)

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "synthetic"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate deterministic, non-executing synthetic_demo business data"
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=20260721)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = generate_synthetic_data(args.output_dir, seed=args.seed)
    print(f"Generated synthetic_demo data with {result.generator_version} (seed={result.seed})")
    for artifact in result.artifacts:
        print(f"  {artifact.filename}: {artifact.record_count} records, sha256={artifact.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

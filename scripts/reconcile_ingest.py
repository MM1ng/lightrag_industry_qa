"""Reconcile one expired ambiguous ingestion job through the shared service."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from industrial_energy_agent.cli import main as energyops_main


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return energyops_main(["reconcile-ingest", args.job_id])


if __name__ == "__main__":
    raise SystemExit(main())

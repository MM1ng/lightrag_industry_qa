"""Minimal command-line entry point for installation and version checks."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from industrial_energy_agent import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="energyops",
        description="EnergyOps Copilot safety-first industrial decision-support service",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the stable bootstrap CLI surface."""

    _build_parser().parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

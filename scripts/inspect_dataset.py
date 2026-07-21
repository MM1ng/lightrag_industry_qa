"""Inspect protected hydraulic sources and create/compare a stable manifest."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from industrial_energy_agent.data_processing.hydraulic_schema import (
    PROFILE_LABELS,
    SENSOR_SPECS,
    InspectionError,
    inspect_hydraulic_dataset,
)
from industrial_energy_agent.data_processing.manifest import (
    ManifestEntry,
    build_manifest,
    compare_manifests,
    load_manifest,
    write_manifest_atomic,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_ROOT = PROJECT_ROOT / "data" / "raw_dataset" / "hydraulic_systems"
DEFAULT_MANUALS_ROOT = PROJECT_ROOT / "data" / "manuals"
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "processed" / "manifests" / "source_before.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--manuals-root", type=Path, default=DEFAULT_MANUALS_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--compare-manifest", type=Path)
    return parser


def _protected_manifest(
    dataset_root: Path,
    manuals_root: Path,
) -> tuple[ManifestEntry, ...]:
    entries = [
        *build_manifest(dataset_root, relative_to=PROJECT_ROOT),
        *build_manifest(manuals_root, relative_to=PROJECT_ROOT),
    ]
    return tuple(sorted(entries, key=lambda entry: entry.relative_path))


def _print_errors(errors: Sequence[InspectionError]) -> None:
    for error in errors:
        location = error.relative_path
        if error.cycle is not None:
            location += f":cycle={error.cycle}"
        if error.column is not None:
            location += f":column={error.column}"
        print(f"ERROR {error.code} {location}: {error.message}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = inspect_hydraulic_dataset(args.dataset_root)
    if not report.is_valid:
        _print_errors(report.errors)
        print(f"dataset_contract=FAIL errors={len(report.errors)}", file=sys.stderr)
        return 1

    current_manifest = _protected_manifest(args.dataset_root, args.manuals_root)
    if args.compare_manifest is not None:
        baseline = load_manifest(args.compare_manifest)
        difference = compare_manifests(baseline, current_manifest)
        print(
            "manifest_compare="
            f"{'UNCHANGED' if difference.is_unchanged else 'CHANGED'} "
            f"added={len(difference.added)} removed={len(difference.removed)} "
            f"changed={len(difference.changed)}"
        )
        return 0 if difference.is_unchanged else 1

    write_manifest_atomic(
        current_manifest,
        args.output,
        protected_roots=(args.dataset_root, args.manuals_root),
    )
    print(
        "dataset_contract=PASS "
        f"sensors={len(SENSOR_SPECS)} cycles={report.cycle_count} "
        f"profile_labels={len(PROFILE_LABELS)}"
    )
    print(f"source_manifest={args.output} files={len(current_manifest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

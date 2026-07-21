from __future__ import annotations

from pathlib import Path

from industrial_energy_agent.data_processing.hydraulic_schema import (
    EXPECTED_CYCLE_COUNT,
    PROFILE_LABELS,
    SENSOR_SPECS,
    inspect_hydraulic_dataset,
)
from industrial_energy_agent.data_processing.manifest import build_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATASET_ROOT = PROJECT_ROOT / "data" / "raw_dataset" / "hydraulic_systems"
MANUALS_ROOT = PROJECT_ROOT / "data" / "manuals"


def _protected_manifest():
    entries = [
        *build_manifest(DATASET_ROOT, relative_to=PROJECT_ROOT),
        *build_manifest(MANUALS_ROOT, relative_to=PROJECT_ROOT),
    ]
    return tuple(sorted(entries, key=lambda entry: entry.relative_path))


def test_real_hydraulic_input_contract_and_source_integrity() -> None:
    before = _protected_manifest()

    report = inspect_hydraulic_dataset(DATASET_ROOT)

    after = _protected_manifest()
    assert after == before
    assert report.is_valid, report.errors
    assert report.cycle_count == EXPECTED_CYCLE_COUNT == 2_205
    assert len(report.sensor_files) == len(SENSOR_SPECS) == 17
    assert len(PROFILE_LABELS) == 5
    assert set(report.inspected_files) == {
        *(spec.file_name for spec in SENSOR_SPECS.values()),
        "profile.txt",
    }
    for name, spec in SENSOR_SPECS.items():
        file_report = report.sensor_files[name]
        assert file_report.row_count == EXPECTED_CYCLE_COUNT
        assert file_report.min_columns == file_report.max_columns == spec.points_per_cycle

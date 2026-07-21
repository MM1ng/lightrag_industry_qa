from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pytest

from industrial_energy_agent.data_processing.hydraulic_schema import (
    CYCLE_DURATION_SECONDS,
    EXPECTED_CYCLE_COUNT,
    PROFILE_LABELS,
    SENSOR_SPECS,
    inspect_hydraulic_dataset,
    inspect_numeric_matrix,
    inspect_profile,
)

EXPECTED_SENSOR_NAMES = {
    "PS1",
    "PS2",
    "PS3",
    "PS4",
    "PS5",
    "PS6",
    "EPS1",
    "FS1",
    "FS2",
    "TS1",
    "TS2",
    "TS3",
    "TS4",
    "VS1",
    "CE",
    "CP",
    "SE",
}


def _error(report: object, code: str):
    errors = [error for error in report.errors if error.code == code]  # type: ignore[attr-defined]
    assert errors, f"expected error code {code}, got {report.errors!r}"  # type: ignore[attr-defined]
    return errors[0]


def test_sensor_registry_is_the_exact_seventeen_file_allowlist() -> None:
    assert set(SENSOR_SPECS) == EXPECTED_SENSOR_NAMES
    assert len(SENSOR_SPECS) == 17
    assert all(spec.file_name == f"{name}.txt" for name, spec in SENSOR_SPECS.items())
    assert sum(spec.points_per_cycle for spec in SENSOR_SPECS.values()) == 43_680


def test_sensor_registry_encodes_the_three_sampling_shapes_and_units() -> None:
    assert CYCLE_DURATION_SECONDS == 60
    assert EXPECTED_CYCLE_COUNT == 2_205
    assert SENSOR_SPECS["PS1"].sample_rate_hz == 100
    assert SENSOR_SPECS["PS1"].points_per_cycle == 6_000
    assert SENSOR_SPECS["PS1"].unit == "bar"
    assert SENSOR_SPECS["EPS1"].unit == "W"
    assert SENSOR_SPECS["FS1"].sample_rate_hz == 10
    assert SENSOR_SPECS["FS1"].points_per_cycle == 600
    assert SENSOR_SPECS["FS1"].unit == "l/min"
    assert SENSOR_SPECS["TS1"].sample_rate_hz == 1
    assert SENSOR_SPECS["TS1"].points_per_cycle == 60
    assert SENSOR_SPECS["TS1"].unit == "°C"
    assert SENSOR_SPECS["VS1"].unit == "mm/s"
    assert SENSOR_SPECS["CE"].unit == "%"
    assert SENSOR_SPECS["CP"].unit == "kW"
    assert SENSOR_SPECS["SE"].unit == "%"


def test_profile_registry_has_five_ordered_labels_and_exact_values() -> None:
    assert [label.name for label in PROFILE_LABELS] == [
        "cooler_condition_pct",
        "valve_condition_pct",
        "pump_leakage_level",
        "accumulator_pressure_bar",
        "stable_flag",
    ]
    assert [label.allowed_values for label in PROFILE_LABELS] == [
        frozenset({3, 20, 100}),
        frozenset({73, 80, 90, 100}),
        frozenset({0, 1, 2}),
        frozenset({90, 100, 115, 130}),
        frozenset({0, 1}),
    ]


def test_tab_delimited_numeric_matrix_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "matrix.txt"
    path.write_bytes(b"1\t2\t3\n4\t5\t6\n")

    report = inspect_numeric_matrix(path, expected_columns=3, expected_cycles=2)

    assert report.is_valid
    assert report.row_count == 2
    assert report.min_columns == report.max_columns == 3


def test_space_delimited_row_is_not_silently_accepted(tmp_path: Path) -> None:
    path = tmp_path / "matrix.txt"
    path.write_bytes(b"1 2 3\n")

    report = inspect_numeric_matrix(path, expected_columns=3, expected_cycles=1)

    error = _error(report, "INVALID_DELIMITER")
    assert (error.cycle, error.column) == (1, 1)


def test_unmatched_data_warning_falls_back_to_strict_token_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "matrix.txt"
    path.write_bytes(b"1\t2\t3foo\n")

    def partially_parse_with_warning(*args: object, **kwargs: object) -> np.ndarray:
        warnings.warn("unmatched data", DeprecationWarning, stacklevel=2)
        return np.array([1.0, 2.0, 3.0], dtype=np.float64)

    monkeypatch.setattr(np, "fromstring", partially_parse_with_warning)

    report = inspect_numeric_matrix(path, expected_columns=3, expected_cycles=1)

    error = _error(report, "NONNUMERIC_VALUE")
    assert (error.cycle, error.column) == (1, 3)


@pytest.mark.parametrize(
    ("payload", "code", "column"),
    [
        (b"1\t2\n", "SHORT_ROW", 3),
        (b"1\t2\t3\t4\n", "LONG_ROW", 4),
        (b"1\tbad\t3\n", "NONNUMERIC_VALUE", 2),
        (b"1\t\t3\n", "MISSING_VALUE", 2),
        (b"1\tnan\t3\n", "NONFINITE_VALUE", 2),
        (b"1\tinf\t3\n", "NONFINITE_VALUE", 2),
        (b"1\t-inf\t3\n", "NONFINITE_VALUE", 2),
    ],
)
def test_matrix_errors_include_one_based_cycle_and_column(
    tmp_path: Path,
    payload: bytes,
    code: str,
    column: int,
) -> None:
    path = tmp_path / "matrix.txt"
    path.write_bytes(b"1\t2\t3\n" + payload)

    report = inspect_numeric_matrix(path, expected_columns=3, expected_cycles=2)

    error = _error(report, code)
    assert error.relative_path == "matrix.txt"
    assert (error.cycle, error.column) == (2, column)


def test_cycle_count_mismatch_reports_first_missing_cycle(tmp_path: Path) -> None:
    path = tmp_path / "matrix.txt"
    path.write_bytes(b"1\t2\t3\n")

    report = inspect_numeric_matrix(path, expected_columns=3, expected_cycles=2)

    error = _error(report, "CYCLE_COUNT_MISMATCH")
    assert (error.cycle, error.column) == (2, None)


def test_profile_rejects_value_outside_the_five_label_domains(tmp_path: Path) -> None:
    path = tmp_path / "profile.txt"
    path.write_bytes(b"3\t100\t0\t130\t0\n20\t90\t1\t999\t1\n")

    report = inspect_profile(path, expected_cycles=2)

    error = _error(report, "INVALID_LABEL_VALUE")
    assert (error.cycle, error.column) == (2, 4)
    assert error.field_name == "accumulator_pressure_bar"


def test_dataset_inspector_reports_all_missing_allowlisted_inputs(tmp_path: Path) -> None:
    (tmp_path / "description.txt").write_text("not a matrix", encoding="utf-8")
    (tmp_path / "documentation.txt").write_text("not a matrix", encoding="utf-8")

    report = inspect_hydraulic_dataset(tmp_path)

    missing = [error.relative_path for error in report.errors if error.code == "MISSING_FILE"]
    assert set(missing) == {f"{name}.txt" for name in EXPECTED_SENSOR_NAMES} | {"profile.txt"}
    assert "description.txt" not in report.inspected_files
    assert "documentation.txt" not in report.inspected_files


def test_dataset_inspector_reports_inconsistent_cycle_counts_between_files(
    tmp_path: Path,
) -> None:
    for name, spec in SENSOR_SPECS.items():
        row = b"\t".join([b"1"] * spec.points_per_cycle) + b"\n"
        (tmp_path / spec.file_name).write_bytes(row * (2 if name == "PS1" else 1))
    (tmp_path / "profile.txt").write_bytes(b"3\t100\t0\t130\t0\n")

    report = inspect_hydraulic_dataset(tmp_path)

    error = _error(report, "INCONSISTENT_CYCLE_COUNT")
    assert error.relative_path == "<dataset>"
    assert error.cycle is None

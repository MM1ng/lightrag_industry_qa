"""Strict, streaming contract inspection for the real UCI hydraulic dataset."""

from __future__ import annotations

import math
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import numpy as np

CYCLE_DURATION_SECONDS = 60
EXPECTED_CYCLE_COUNT = 2_205


@dataclass(frozen=True, slots=True)
class SensorSpec:
    name: str
    file_name: str
    physical_quantity: str
    unit: str
    sample_rate_hz: int
    points_per_cycle: int
    virtual: bool = False


def _sensor(
    name: str,
    physical_quantity: str,
    unit: str,
    sample_rate_hz: int,
    *,
    virtual: bool = False,
) -> SensorSpec:
    return SensorSpec(
        name=name,
        file_name=f"{name}.txt",
        physical_quantity=physical_quantity,
        unit=unit,
        sample_rate_hz=sample_rate_hz,
        points_per_cycle=CYCLE_DURATION_SECONDS * sample_rate_hz,
        virtual=virtual,
    )


_SENSOR_SPECS = {
    **{name: _sensor(name, "pressure", "bar", 100) for name in [f"PS{i}" for i in range(1, 7)]},
    "EPS1": _sensor("EPS1", "motor power", "W", 100),
    "FS1": _sensor("FS1", "volume flow", "l/min", 10),
    "FS2": _sensor("FS2", "volume flow", "l/min", 10),
    **{name: _sensor(name, "temperature", "°C", 1) for name in [f"TS{i}" for i in range(1, 5)]},
    "VS1": _sensor("VS1", "vibration", "mm/s", 1),
    "CE": _sensor("CE", "cooling efficiency", "%", 1, virtual=True),
    "CP": _sensor("CP", "cooling power", "kW", 1, virtual=True),
    "SE": _sensor("SE", "efficiency factor", "%", 1),
}
SENSOR_SPECS: Mapping[str, SensorSpec] = MappingProxyType(_SENSOR_SPECS)


@dataclass(frozen=True, slots=True)
class ProfileLabelSpec:
    name: str
    unit: str | None
    allowed_values: frozenset[int]
    meanings: Mapping[int, str]


PROFILE_LABELS = (
    ProfileLabelSpec(
        "cooler_condition_pct",
        "%",
        frozenset({3, 20, 100}),
        MappingProxyType(
            {3: "close to total failure", 20: "reduced efficiency", 100: "full efficiency"}
        ),
    ),
    ProfileLabelSpec(
        "valve_condition_pct",
        "%",
        frozenset({73, 80, 90, 100}),
        MappingProxyType(
            {
                73: "close to total failure",
                80: "severe lag",
                90: "small lag",
                100: "optimal switching behavior",
            }
        ),
    ),
    ProfileLabelSpec(
        "pump_leakage_level",
        None,
        frozenset({0, 1, 2}),
        MappingProxyType({0: "no leakage", 1: "weak leakage", 2: "severe leakage"}),
    ),
    ProfileLabelSpec(
        "accumulator_pressure_bar",
        "bar",
        frozenset({90, 100, 115, 130}),
        MappingProxyType(
            {
                90: "close to total failure",
                100: "severely reduced pressure",
                115: "slightly reduced pressure",
                130: "optimal pressure",
            }
        ),
    ),
    ProfileLabelSpec(
        "stable_flag",
        None,
        frozenset({0, 1}),
        MappingProxyType(
            {0: "conditions were stable", 1: "static conditions might not have been reached"}
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class InspectionError:
    code: str
    relative_path: str
    message: str
    cycle: int | None = None
    column: int | None = None
    field_name: str | None = None


@dataclass(frozen=True, slots=True)
class FileInspection:
    relative_path: str
    expected_columns: int
    expected_cycles: int
    row_count: int
    min_columns: int | None
    max_columns: int | None
    errors: tuple[InspectionError, ...]

    @property
    def is_valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True, slots=True)
class HydraulicDatasetInspection:
    dataset_root: str
    cycle_count: int | None
    sensor_files: Mapping[str, FileInspection]
    profile_file: FileInspection | None
    inspected_files: tuple[str, ...]
    errors: tuple[InspectionError, ...]

    @property
    def is_valid(self) -> bool:
        return not self.errors


def _strip_newline(raw_line: bytes) -> bytes:
    if raw_line.endswith(b"\n"):
        raw_line = raw_line[:-1]
    if raw_line.endswith(b"\r"):
        raw_line = raw_line[:-1]
    return raw_line


def _shape_errors(
    line: bytes,
    *,
    relative_path: str,
    cycle: int,
    expected_columns: int,
) -> tuple[int, list[InspectionError]]:
    errors: list[InspectionError] = []
    actual_columns = line.count(b"\t") + 1 if line else 0
    if (
        expected_columns > 1
        and b"\t" not in line
        and any(separator in line for separator in (b" ", b",", b";"))
    ):
        errors.append(
            InspectionError(
                code="INVALID_DELIMITER",
                relative_path=relative_path,
                cycle=cycle,
                column=1,
                message="row is not TAB-delimited",
            )
        )
    if actual_columns < expected_columns:
        errors.append(
            InspectionError(
                code="SHORT_ROW",
                relative_path=relative_path,
                cycle=cycle,
                column=actual_columns + 1,
                message=f"expected {expected_columns} columns, found {actual_columns}",
            )
        )
    elif actual_columns > expected_columns:
        errors.append(
            InspectionError(
                code="LONG_ROW",
                relative_path=relative_path,
                cycle=cycle,
                column=expected_columns + 1,
                message=f"expected {expected_columns} columns, found {actual_columns}",
            )
        )
    return actual_columns, errors


def _token_error(
    token: bytes,
    *,
    relative_path: str,
    cycle: int,
    column: int,
) -> InspectionError | None:
    stripped = token.strip()
    if not stripped:
        return InspectionError(
            code="MISSING_VALUE",
            relative_path=relative_path,
            cycle=cycle,
            column=column,
            message="field is empty",
        )
    try:
        value = float(stripped)
    except ValueError:
        return InspectionError(
            code="NONNUMERIC_VALUE",
            relative_path=relative_path,
            cycle=cycle,
            column=column,
            message="field is not numeric",
        )
    if not math.isfinite(value):
        return InspectionError(
            code="NONFINITE_VALUE",
            relative_path=relative_path,
            cycle=cycle,
            column=column,
            message="field is NaN or infinite",
        )
    return None


def _cycle_count_error(
    relative_path: str,
    row_count: int,
    expected_cycles: int,
) -> InspectionError | None:
    if row_count == expected_cycles:
        return None
    first_mismatch = row_count + 1 if row_count < expected_cycles else expected_cycles + 1
    return InspectionError(
        code="CYCLE_COUNT_MISMATCH",
        relative_path=relative_path,
        cycle=first_mismatch,
        message=f"expected {expected_cycles} cycles, found {row_count}",
    )


def inspect_numeric_matrix(
    path: Path | str,
    *,
    expected_columns: int,
    expected_cycles: int,
    relative_path: str | None = None,
) -> FileInspection:
    """Stream a headerless TAB matrix and localize every contract violation."""

    source = Path(path)
    display_path = relative_path or source.name
    errors: list[InspectionError] = []
    row_count = 0
    min_columns: int | None = None
    max_columns: int | None = None

    with source.open("rb") as stream:
        for row_count, raw_line in enumerate(stream, start=1):
            line = _strip_newline(raw_line)
            actual_columns, row_errors = _shape_errors(
                line,
                relative_path=display_path,
                cycle=row_count,
                expected_columns=expected_columns,
            )
            errors.extend(row_errors)
            min_columns = (
                actual_columns if min_columns is None else min(min_columns, actual_columns)
            )
            max_columns = (
                actual_columns if max_columns is None else max(max_columns, actual_columns)
            )

            strict_token_validation = False
            with warnings.catch_warnings(record=True) as parse_warnings:
                warnings.simplefilter("always", DeprecationWarning)
                try:
                    values = np.fromstring(line, dtype=np.float64, sep="\t")
                except ValueError:
                    values = np.empty(0, dtype=np.float64)
                    strict_token_validation = True
            if parse_warnings:
                strict_token_validation = True

            if not strict_token_validation and values.size == actual_columns:
                for zero_based_column in np.flatnonzero(~np.isfinite(values)):
                    errors.append(
                        InspectionError(
                            code="NONFINITE_VALUE",
                            relative_path=display_path,
                            cycle=row_count,
                            column=int(zero_based_column) + 1,
                            message="field is NaN or infinite",
                        )
                    )
            else:
                for column, token in enumerate(line.split(b"\t"), start=1):
                    if error := _token_error(
                        token,
                        relative_path=display_path,
                        cycle=row_count,
                        column=column,
                    ):
                        errors.append(error)

    if error := _cycle_count_error(display_path, row_count, expected_cycles):
        errors.append(error)
    return FileInspection(
        relative_path=display_path,
        expected_columns=expected_columns,
        expected_cycles=expected_cycles,
        row_count=row_count,
        min_columns=min_columns,
        max_columns=max_columns,
        errors=tuple(errors),
    )


def _profile_token_error(
    token: bytes,
    *,
    relative_path: str,
    cycle: int,
    column: int,
    label: ProfileLabelSpec,
) -> InspectionError | None:
    if numeric_error := _token_error(
        token,
        relative_path=relative_path,
        cycle=cycle,
        column=column,
    ):
        return numeric_error
    try:
        value = int(token.strip())
    except ValueError:
        return InspectionError(
            code="NONNUMERIC_VALUE",
            relative_path=relative_path,
            cycle=cycle,
            column=column,
            field_name=label.name,
            message="profile label must be an integer",
        )
    if value not in label.allowed_values:
        return InspectionError(
            code="INVALID_LABEL_VALUE",
            relative_path=relative_path,
            cycle=cycle,
            column=column,
            field_name=label.name,
            message=f"value is outside the allowed domain for {label.name}",
        )
    return None


def inspect_profile(
    path: Path | str,
    *,
    expected_cycles: int = EXPECTED_CYCLE_COUNT,
    relative_path: str | None = None,
) -> FileInspection:
    """Inspect the five ordered integer profile labels."""

    source = Path(path)
    display_path = relative_path or source.name
    errors: list[InspectionError] = []
    row_count = 0
    min_columns: int | None = None
    max_columns: int | None = None
    expected_columns = len(PROFILE_LABELS)

    with source.open("rb") as stream:
        for row_count, raw_line in enumerate(stream, start=1):
            line = _strip_newline(raw_line)
            actual_columns, row_errors = _shape_errors(
                line,
                relative_path=display_path,
                cycle=row_count,
                expected_columns=expected_columns,
            )
            errors.extend(row_errors)
            min_columns = (
                actual_columns if min_columns is None else min(min_columns, actual_columns)
            )
            max_columns = (
                actual_columns if max_columns is None else max(max_columns, actual_columns)
            )
            for column, (token, label) in enumerate(
                zip(line.split(b"\t"), PROFILE_LABELS, strict=False),
                start=1,
            ):
                if error := _profile_token_error(
                    token,
                    relative_path=display_path,
                    cycle=row_count,
                    column=column,
                    label=label,
                ):
                    errors.append(error)

    if error := _cycle_count_error(display_path, row_count, expected_cycles):
        errors.append(error)
    return FileInspection(
        relative_path=display_path,
        expected_columns=expected_columns,
        expected_cycles=expected_cycles,
        row_count=row_count,
        min_columns=min_columns,
        max_columns=max_columns,
        errors=tuple(errors),
    )


def _missing_file_error(file_name: str) -> InspectionError:
    return InspectionError(
        code="MISSING_FILE",
        relative_path=file_name,
        message="required source file is missing",
    )


def inspect_hydraulic_dataset(path: Path | str) -> HydraulicDatasetInspection:
    """Inspect only the fixed 17 matrix allowlist plus ``profile.txt``."""

    dataset_root = Path(path)
    sensor_reports: dict[str, FileInspection] = {}
    profile_report: FileInspection | None = None
    errors: list[InspectionError] = []
    inspected_files: list[str] = []

    for name, spec in SENSOR_SPECS.items():
        source = dataset_root / spec.file_name
        if not source.is_file():
            errors.append(_missing_file_error(spec.file_name))
            continue
        report = inspect_numeric_matrix(
            source,
            expected_columns=spec.points_per_cycle,
            expected_cycles=EXPECTED_CYCLE_COUNT,
            relative_path=spec.file_name,
        )
        sensor_reports[name] = report
        inspected_files.append(spec.file_name)
        errors.extend(report.errors)

    profile_path = dataset_root / "profile.txt"
    if not profile_path.is_file():
        errors.append(_missing_file_error("profile.txt"))
    else:
        profile_report = inspect_profile(profile_path, relative_path="profile.txt")
        inspected_files.append("profile.txt")
        errors.extend(profile_report.errors)

    row_counts = {report.row_count for report in sensor_reports.values()}
    if profile_report is not None:
        row_counts.add(profile_report.row_count)
    cycle_count = next(iter(row_counts)) if len(row_counts) == 1 else None
    if len(row_counts) > 1:
        errors.append(
            InspectionError(
                code="INCONSISTENT_CYCLE_COUNT",
                relative_path="<dataset>",
                message="source files do not contain the same number of cycles",
            )
        )

    return HydraulicDatasetInspection(
        dataset_root=str(dataset_root),
        cycle_count=cycle_count,
        sensor_files=MappingProxyType(sensor_reports),
        profile_file=profile_report,
        inspected_files=tuple(inspected_files),
        errors=tuple(errors),
    )

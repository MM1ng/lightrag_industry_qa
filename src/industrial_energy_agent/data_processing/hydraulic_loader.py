"""Strict streaming loaders for hydraulic cycles and profile labels."""

from __future__ import annotations

import math
import warnings
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from industrial_energy_agent.data_processing.hydraulic_schema import PROFILE_LABELS


class HydraulicLoadError(ValueError):
    """Localized failure raised before any invalid cycle reaches feature code."""

    def __init__(
        self,
        code: str,
        relative_path: str,
        message: str,
        *,
        cycle: int | None = None,
        column: int | None = None,
    ) -> None:
        self.code = code
        self.relative_path = relative_path
        self.cycle = cycle
        self.column = column
        self.message = message
        location = relative_path
        if cycle is not None:
            location += f":cycle={cycle}"
        if column is not None:
            location += f":column={column}"
        super().__init__(f"{code} {location}: {message}")


def _strip_newline(raw_line: bytes) -> bytes:
    if raw_line.endswith(b"\n"):
        raw_line = raw_line[:-1]
    if raw_line.endswith(b"\r"):
        raw_line = raw_line[:-1]
    return raw_line


def _shape_error(
    actual_points: int,
    expected_points: int,
    *,
    relative_path: str,
    cycle: int,
) -> HydraulicLoadError | None:
    if actual_points < expected_points:
        return HydraulicLoadError(
            "SHORT_ROW",
            relative_path,
            f"expected {expected_points} points, found {actual_points}",
            cycle=cycle,
            column=actual_points + 1,
        )
    if actual_points > expected_points:
        return HydraulicLoadError(
            "LONG_ROW",
            relative_path,
            f"expected {expected_points} points, found {actual_points}",
            cycle=cycle,
            column=expected_points + 1,
        )
    return None


def _strict_values(
    line: bytes,
    expected_points: int,
    *,
    relative_path: str,
    cycle: int,
) -> np.ndarray:
    values = np.empty(expected_points, dtype=np.float64)
    for column, token in enumerate(line.split(b"\t"), start=1):
        stripped = token.strip()
        if not stripped:
            raise HydraulicLoadError(
                "MISSING_VALUE",
                relative_path,
                "field is empty",
                cycle=cycle,
                column=column,
            )
        try:
            value = float(stripped)
        except ValueError as error:
            raise HydraulicLoadError(
                "NONNUMERIC_VALUE",
                relative_path,
                "field is not numeric",
                cycle=cycle,
                column=column,
            ) from error
        if not math.isfinite(value):
            raise HydraulicLoadError(
                "NONFINITE_VALUE",
                relative_path,
                "field is NaN or infinite",
                cycle=cycle,
                column=column,
            )
        values[column - 1] = value
    return values


def _parse_numeric_row(
    line: bytes,
    expected_points: int,
    *,
    relative_path: str,
    cycle: int,
) -> np.ndarray:
    actual_points = line.count(b"\t") + 1 if line else 0
    if error := _shape_error(
        actual_points,
        expected_points,
        relative_path=relative_path,
        cycle=cycle,
    ):
        raise error

    use_strict_parser = False
    with warnings.catch_warnings(record=True) as parse_warnings:
        warnings.simplefilter("always", DeprecationWarning)
        try:
            values = np.fromstring(line, dtype=np.float64, sep="\t")
        except ValueError:
            values = np.empty(0, dtype=np.float64)
            use_strict_parser = True
    if parse_warnings or values.size != expected_points:
        use_strict_parser = True
    if use_strict_parser:
        return _strict_values(
            line,
            expected_points,
            relative_path=relative_path,
            cycle=cycle,
        )

    nonfinite_columns = np.flatnonzero(~np.isfinite(values))
    if nonfinite_columns.size:
        column = int(nonfinite_columns[0]) + 1
        raise HydraulicLoadError(
            "NONFINITE_VALUE",
            relative_path,
            "field is NaN or infinite",
            cycle=cycle,
            column=column,
        )
    return values


def iter_numeric_cycles(
    path: Path | str,
    *,
    expected_points: int,
    expected_cycles: int,
    relative_path: str | None = None,
) -> Iterator[tuple[int, np.ndarray]]:
    """Yield one validated float64 cycle at a time using one-based IDs."""

    if expected_points <= 0 or expected_cycles <= 0:
        raise ValueError("expected_points and expected_cycles must be positive")
    source = Path(path)
    display_path = relative_path or source.name
    row_count = 0
    with source.open("rb") as stream:
        for row_count, raw_line in enumerate(stream, start=1):
            if row_count > expected_cycles:
                raise HydraulicLoadError(
                    "CYCLE_COUNT_MISMATCH",
                    display_path,
                    f"expected {expected_cycles} cycles, found more",
                    cycle=row_count,
                )
            yield (
                row_count,
                _parse_numeric_row(
                    _strip_newline(raw_line),
                    expected_points,
                    relative_path=display_path,
                    cycle=row_count,
                ),
            )
    if row_count != expected_cycles:
        raise HydraulicLoadError(
            "CYCLE_COUNT_MISMATCH",
            display_path,
            f"expected {expected_cycles} cycles, found {row_count}",
            cycle=row_count + 1,
        )


def load_profile_labels(
    path: Path | str,
    *,
    expected_cycles: int,
    relative_path: str | None = None,
) -> np.ndarray:
    """Load the small five-column profile matrix after domain validation."""

    source = Path(path)
    display_path = relative_path or source.name
    labels = np.empty((expected_cycles, len(PROFILE_LABELS)), dtype=np.int64)
    for cycle_id, values in iter_numeric_cycles(
        source,
        expected_points=len(PROFILE_LABELS),
        expected_cycles=expected_cycles,
        relative_path=display_path,
    ):
        for column, (value, label) in enumerate(
            zip(values, PROFILE_LABELS, strict=True),
            start=1,
        ):
            if not value.is_integer() or int(value) not in label.allowed_values:
                raise HydraulicLoadError(
                    "INVALID_LABEL_VALUE",
                    display_path,
                    f"value is outside the allowed domain for {label.name}",
                    cycle=cycle_id,
                    column=column,
                )
            labels[cycle_id - 1, column - 1] = int(value)
    return labels

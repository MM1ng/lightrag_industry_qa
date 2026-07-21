from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pytest

from industrial_energy_agent.data_processing.hydraulic_loader import (
    HydraulicLoadError,
    iter_numeric_cycles,
    load_profile_labels,
)


def test_numeric_cycles_stream_float64_rows_with_one_based_cycle_ids(tmp_path: Path) -> None:
    path = tmp_path / "PS1.txt"
    path.write_bytes(b"1\t2\t3\n4\t5\t6\n")

    cycles = list(iter_numeric_cycles(path, expected_points=3, expected_cycles=2))

    assert [cycle_id for cycle_id, _ in cycles] == [1, 2]
    assert [values.dtype for _, values in cycles] == [np.dtype("float64")] * 2
    np.testing.assert_array_equal(cycles[0][1], np.array([1.0, 2.0, 3.0]))
    np.testing.assert_array_equal(cycles[1][1], np.array([4.0, 5.0, 6.0]))
    assert not np.shares_memory(cycles[0][1], cycles[1][1])


@pytest.mark.parametrize(
    ("payload", "code", "column"),
    [
        (b"1\t2\n", "SHORT_ROW", 3),
        (b"1\t2\t3\t4\n", "LONG_ROW", 4),
        (b"1\tbad\t3\n", "NONNUMERIC_VALUE", 2),
        (b"1\t\t3\n", "MISSING_VALUE", 2),
        (b"1\tnan\t3\n", "NONFINITE_VALUE", 2),
        (b"1\tinf\t3\n", "NONFINITE_VALUE", 2),
    ],
)
def test_numeric_cycle_error_localizes_file_cycle_and_column(
    tmp_path: Path,
    payload: bytes,
    code: str,
    column: int,
) -> None:
    path = tmp_path / "PS1.txt"
    path.write_bytes(b"1\t2\t3\n" + payload)

    with pytest.raises(HydraulicLoadError) as caught:
        list(iter_numeric_cycles(path, expected_points=3, expected_cycles=2))

    error = caught.value
    assert error.code == code
    assert error.relative_path == "PS1.txt"
    assert (error.cycle, error.column) == (2, column)


@pytest.mark.parametrize("actual_cycles", [1, 3])
def test_numeric_loader_rejects_cycle_count_mismatch(
    tmp_path: Path,
    actual_cycles: int,
) -> None:
    path = tmp_path / "PS1.txt"
    path.write_bytes(b"1\t2\t3\n" * actual_cycles)

    with pytest.raises(HydraulicLoadError) as caught:
        list(iter_numeric_cycles(path, expected_points=3, expected_cycles=2))

    assert caught.value.code == "CYCLE_COUNT_MISMATCH"
    assert caught.value.relative_path == "PS1.txt"


def test_unmatched_data_warning_uses_strict_token_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "PS1.txt"
    path.write_bytes(b"1\t2\t3foo\n")

    def partially_parse_with_warning(*args: object, **kwargs: object) -> np.ndarray:
        warnings.warn("unmatched data", DeprecationWarning, stacklevel=2)
        return np.array([1.0, 2.0, 3.0], dtype=np.float64)

    monkeypatch.setattr(np, "fromstring", partially_parse_with_warning)

    with pytest.raises(HydraulicLoadError) as caught:
        list(iter_numeric_cycles(path, expected_points=3, expected_cycles=1))

    assert (caught.value.code, caught.value.column) == ("NONNUMERIC_VALUE", 3)


def test_profile_labels_preserve_integer_domains_and_shape(tmp_path: Path) -> None:
    path = tmp_path / "profile.txt"
    path.write_bytes(b"3\t100\t0\t130\t0\n20\t90\t1\t115\t1\n")

    labels = load_profile_labels(path, expected_cycles=2)

    assert labels.dtype == np.int64
    np.testing.assert_array_equal(
        labels,
        np.array([[3, 100, 0, 130, 0], [20, 90, 1, 115, 1]], dtype=np.int64),
    )


def test_profile_labels_reject_noninteger_or_out_of_domain_value(tmp_path: Path) -> None:
    path = tmp_path / "profile.txt"
    path.write_bytes(b"3\t100\t0\t129.5\t0\n")

    with pytest.raises(HydraulicLoadError) as caught:
        load_profile_labels(path, expected_cycles=1)

    assert caught.value.code == "INVALID_LABEL_VALUE"
    assert (caught.value.cycle, caught.value.column) == (1, 4)

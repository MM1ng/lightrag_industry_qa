from __future__ import annotations

import numpy as np
import pytest

from industrial_energy_agent.data_processing.feature_engineering import (
    FEATURE_NAMES,
    compute_cycle_features,
)


def test_cycle_features_use_population_std_and_real_seconds() -> None:
    values = np.array([1.0, 3.0, 5.0], dtype=np.float64)

    result = compute_cycle_features(values, sample_rate_hz=1.0)

    assert tuple(result) == FEATURE_NAMES
    assert result == pytest.approx(
        {
            "mean": 3.0,
            "std": np.std(values, ddof=0),
            "min": 1.0,
            "max": 5.0,
            "median": 3.0,
            "range": 4.0,
            "first": 1.0,
            "last": 5.0,
            "trend": 4.0,
            "slope": 2.0,
        }
    )


def test_hundred_hz_slope_uses_seconds_not_sample_indices() -> None:
    values = np.arange(6_000, dtype=np.float64) / 100.0

    result = compute_cycle_features(values, sample_rate_hz=100.0)

    assert result["slope"] == pytest.approx(1.0)


def test_constant_cycle_has_zero_population_std_range_trend_and_slope() -> None:
    values = np.full(60, 7.5, dtype=np.float64)

    result = compute_cycle_features(values, sample_rate_hz=1.0)

    assert result["std"] == 0.0
    assert result["range"] == 0.0
    assert result["trend"] == 0.0
    assert result["slope"] == pytest.approx(0.0, abs=1e-15)


def test_single_point_cycle_has_zero_slope() -> None:
    result = compute_cycle_features(np.array([4.0]), sample_rate_hz=1.0)

    assert result["slope"] == 0.0


@pytest.mark.parametrize(
    "values",
    [
        np.array([], dtype=np.float64),
        np.array([[1.0, 2.0]], dtype=np.float64),
        np.array([1.0, np.nan], dtype=np.float64),
        np.array([1.0, np.inf], dtype=np.float64),
    ],
)
def test_cycle_features_reject_invalid_or_nonfinite_values(values: np.ndarray) -> None:
    with pytest.raises(ValueError):
        compute_cycle_features(values, sample_rate_hz=1.0)


@pytest.mark.parametrize("sample_rate_hz", [0.0, -1.0, np.nan, np.inf])
def test_cycle_features_reject_invalid_sample_rate(sample_rate_hz: float) -> None:
    with pytest.raises(ValueError, match="sample_rate_hz"):
        compute_cycle_features(np.array([1.0, 2.0]), sample_rate_hz=sample_rate_hz)

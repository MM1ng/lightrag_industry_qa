"""Deterministic cycle-level hydraulic feature calculations."""

from __future__ import annotations

import math
from typing import Final

import numpy as np

FEATURE_NAMES: Final = (
    "mean",
    "std",
    "min",
    "max",
    "median",
    "range",
    "first",
    "last",
    "trend",
    "slope",
)


def _least_squares_slope(values: np.ndarray, sample_rate_hz: float) -> float:
    if values.size == 1:
        return 0.0
    seconds = np.arange(values.size, dtype=np.float64) / sample_rate_hz
    centered_seconds = seconds - seconds.mean(dtype=np.float64)
    centered_values = values - values.mean(dtype=np.float64)
    denominator = np.dot(centered_seconds, centered_seconds)
    return float(np.dot(centered_seconds, centered_values) / denominator)


def compute_cycle_features(
    values: np.ndarray,
    sample_rate_hz: float,
) -> dict[str, float]:
    """Compute the fixed ten-feature contract for one complete cycle."""

    if isinstance(sample_rate_hz, bool) or not math.isfinite(sample_rate_hz):
        raise ValueError("sample_rate_hz must be finite and positive")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be finite and positive")

    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1 or array.size == 0:
        raise ValueError("cycle values must be a non-empty one-dimensional array")
    if not np.all(np.isfinite(array)):
        raise ValueError("cycle values must all be finite")

    minimum = float(np.min(array))
    maximum = float(np.max(array))
    first = float(array[0])
    last = float(array[-1])
    return {
        "mean": float(np.mean(array, dtype=np.float64)),
        "std": float(np.std(array, ddof=0, dtype=np.float64)),
        "min": minimum,
        "max": maximum,
        "median": float(np.median(array)),
        "range": maximum - minimum,
        "first": first,
        "last": last,
        "trend": last - first,
        "slope": _least_squares_slope(array, sample_rate_hz),
    }

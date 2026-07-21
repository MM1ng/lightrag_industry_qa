"""Read-only access to processed hydraulic cycle summaries."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]
import pyarrow.parquet as pq

from industrial_energy_agent.data_processing.feature_engineering import FEATURE_NAMES
from industrial_energy_agent.data_processing.hydraulic_schema import (
    PROFILE_LABELS,
    SENSOR_SPECS,
    SensorSpec,
)

_UNSTABLE_WARNING = "可能尚未达到稳态"


@dataclass(frozen=True, slots=True)
class CycleSummary:
    cycle_id: int
    artifact_version: str
    labels: Mapping[str, int]
    features: Mapping[str, float]
    units: Mapping[str, str]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CycleComparison:
    baseline_cycle_id: int
    cycle_ids: tuple[int, ...]
    artifact_version: str
    deltas: Mapping[int, Mapping[str, float]]
    units: Mapping[str, str]
    warnings: tuple[str, ...]


class SensorRepository:
    """Query only the processed Parquet artifact, never the raw sensor arrays."""

    def __init__(
        self,
        parquet_path: Path | str,
        *,
        report_path: Path | str | None = None,
        sensor_specs: Mapping[str, SensorSpec] = SENSOR_SPECS,
    ) -> None:
        self._parquet_path = Path(parquet_path).resolve(strict=True)
        if not self._parquet_path.is_file():
            raise ValueError("processed sensor artifact must be a Parquet file")
        self._sensor_specs = dict(sensor_specs)
        report = (
            Path(report_path).resolve(strict=True)
            if report_path is not None
            else self._parquet_path.with_name("processing_report.json").resolve(strict=True)
        )
        payload: object = json.loads(report.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("processing report must be an object")
        artifact_version = payload.get("artifact_version")
        if not isinstance(artifact_version, str) or not artifact_version.startswith("sha256:"):
            raise ValueError("processing report has an invalid artifact_version")
        self._artifact_version = artifact_version

        self._columns = tuple(
            pq.ParquetFile(self._parquet_path).schema.names  # type: ignore[no-untyped-call]
        )
        if "cycle_id" not in self._columns:
            raise ValueError("processed sensor artifact is missing cycle_id")
        cycle_frame = pd.read_parquet(self._parquet_path, columns=["cycle_id"])
        raw_cycle_ids = cycle_frame["cycle_id"].tolist()
        if any(isinstance(value, bool) or not isinstance(value, int) for value in raw_cycle_ids):
            raise ValueError("processed cycle_id values must be integers")
        self._cycle_ids = frozenset(int(value) for value in raw_cycle_ids)
        if len(self._cycle_ids) != len(raw_cycle_ids):
            raise ValueError("processed cycle_id values must be unique")

    def _validate_cycle_id(self, cycle_id: int) -> None:
        if isinstance(cycle_id, bool) or not isinstance(cycle_id, int):
            raise ValueError("cycle_id must be an integer in the processed range")
        if cycle_id not in self._cycle_ids:
            raise ValueError("cycle_id is outside the processed range")

    def _feature_units(self, feature_names: Sequence[str]) -> dict[str, str]:
        units: dict[str, str] = {}
        for feature_name in feature_names:
            sensor_name, separator, statistic = feature_name.partition("__")
            spec = self._sensor_specs.get(sensor_name)
            if not separator or spec is None or statistic not in FEATURE_NAMES:
                raise ValueError(f"unknown processed feature column: {feature_name}")
            units[feature_name] = f"{spec.unit}/s" if statistic == "slope" else spec.unit
        return units

    def get_cycle(self, cycle_id: int) -> CycleSummary:
        """Return one 1-based cycle summary from the processed artifact."""

        self._validate_cycle_id(cycle_id)
        frame = pd.read_parquet(
            self._parquet_path,
            filters=[("cycle_id", "=", cycle_id)],
        )
        if len(frame) != 1:
            raise RuntimeError("processed artifact did not return exactly one cycle")
        row = frame.iloc[0]
        labels = {
            label.name: int(row[label.name])
            for label in PROFILE_LABELS
            if label.name in frame.columns
        }
        if len(labels) != len(PROFILE_LABELS):
            raise ValueError("processed sensor artifact is missing profile labels")
        feature_names = [name for name in self._columns if "__" in name]
        features = {name: float(row[name]) for name in feature_names}
        warnings = (_UNSTABLE_WARNING,) if labels["stable_flag"] == 1 else ()
        return CycleSummary(
            cycle_id=cycle_id,
            artifact_version=self._artifact_version,
            labels=labels,
            features=features,
            units=self._feature_units(feature_names),
            warnings=warnings,
        )

    def compare_cycles(self, cycle_ids: Sequence[int]) -> CycleComparison:
        """Compare unique cycles against the first cycle as the baseline."""

        unique_cycle_ids = tuple(dict.fromkeys(cycle_ids))
        if len(unique_cycle_ids) < 2:
            raise ValueError("compare_cycles requires at least two unique cycle IDs")
        summaries = tuple(self.get_cycle(cycle_id) for cycle_id in unique_cycle_ids)
        baseline = summaries[0]
        deltas = {
            summary.cycle_id: {
                name: summary.features[name] - baseline.features[name] for name in baseline.features
            }
            for summary in summaries[1:]
        }
        warnings = tuple(
            f"周期{summary.cycle_id}{_UNSTABLE_WARNING}"
            for summary in summaries
            if summary.warnings
        )
        return CycleComparison(
            baseline_cycle_id=baseline.cycle_id,
            cycle_ids=unique_cycle_ids,
            artifact_version=self._artifact_version,
            deltas=deltas,
            units=baseline.units,
            warnings=warnings,
        )

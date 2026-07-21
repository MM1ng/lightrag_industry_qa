"""Deterministic raw-to-cycle-feature processing with validated atomic outputs."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from industrial_energy_agent.data_processing.feature_engineering import (
    FEATURE_NAMES,
    compute_cycle_features,
)
from industrial_energy_agent.data_processing.hydraulic_loader import (
    HydraulicLoadError,
    iter_numeric_cycles,
    load_profile_labels,
)
from industrial_energy_agent.data_processing.hydraulic_schema import (
    EXPECTED_CYCLE_COUNT,
    PROFILE_LABELS,
    SENSOR_SPECS,
    SensorSpec,
)

PROCESSING_VERSION: Final = "hydraulic-cycle-features-v1"
PROJECT_ROOT: Final = Path(__file__).resolve().parents[3]
PROTECTED_OUTPUT_ROOTS: Final = (
    PROJECT_ROOT / "data" / "raw_dataset",
    PROJECT_ROOT / "data" / "manuals",
)
OUTPUT_FILENAMES: Final = (
    "cycle_features.parquet",
    "cycle_features.csv",
    "data_dictionary.json",
    "processing_report.json",
)
_HASH_CHUNK_SIZE = 1024 * 1024
_FEATURE_FORMULAS: Final = {
    "mean": "arithmetic mean",
    "std": "population standard deviation, ddof=0",
    "min": "minimum value",
    "max": "maximum value",
    "median": "median value",
    "range": "max - min",
    "first": "first sample value",
    "last": "last sample value",
    "trend": "last - first",
    "slope": "ordinary least-squares slope over real seconds",
}


@dataclass(frozen=True, slots=True)
class HydraulicPipelineResult:
    artifact_version: str
    source_fingerprint: str
    processing_fingerprint: str
    output_rows: int
    output_columns: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _required_paths(
    dataset_root: Path,
    sensor_specs: Mapping[str, SensorSpec],
) -> dict[str, Path]:
    paths = {spec.file_name: dataset_root / spec.file_name for spec in sensor_specs.values()}
    paths["profile.txt"] = dataset_root / "profile.txt"
    for relative_path, source in paths.items():
        if not source.is_file():
            raise HydraulicLoadError(
                "MISSING_FILE",
                relative_path,
                "required source file is missing",
            )
    return paths


def _source_hashes(paths: Mapping[str, Path]) -> dict[str, str]:
    return {name: _sha256_file(path) for name, path in paths.items()}


def _json_fingerprint(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _processing_contract(
    sensor_specs: Mapping[str, SensorSpec],
    expected_cycles: int,
) -> dict[str, object]:
    return {
        "processing_version": PROCESSING_VERSION,
        "expected_cycles": expected_cycles,
        "features": list(FEATURE_NAMES),
        "sensors": [
            {
                "name": name,
                "file_name": spec.file_name,
                "unit": spec.unit,
                "sample_rate_hz": spec.sample_rate_hz,
                "points_per_cycle": spec.points_per_cycle,
            }
            for name, spec in sensor_specs.items()
        ],
        "profile_labels": [
            {"name": label.name, "allowed_values": sorted(label.allowed_values)}
            for label in PROFILE_LABELS
        ],
    }


def _artifact_version(source_fingerprint: str, processing_fingerprint: str) -> str:
    digest = hashlib.sha256(
        f"{source_fingerprint}:{processing_fingerprint}".encode("ascii")
    ).hexdigest()
    return f"sha256:{digest}"


def _feature_frame(
    dataset_root: Path,
    sensor_specs: Mapping[str, SensorSpec],
    expected_cycles: int,
) -> pd.DataFrame:
    profile = load_profile_labels(
        dataset_root / "profile.txt",
        expected_cycles=expected_cycles,
        relative_path="profile.txt",
    )
    columns: dict[str, np.ndarray] = {"cycle_id": np.arange(1, expected_cycles + 1, dtype=np.int64)}
    for column, label in enumerate(PROFILE_LABELS):
        columns[label.name] = profile[:, column]

    for sensor_name, spec in sensor_specs.items():
        sensor_features = np.empty(
            (expected_cycles, len(FEATURE_NAMES)),
            dtype=np.float64,
        )
        for cycle_id, values in iter_numeric_cycles(
            dataset_root / spec.file_name,
            expected_points=spec.points_per_cycle,
            expected_cycles=expected_cycles,
            relative_path=spec.file_name,
        ):
            features = compute_cycle_features(values, spec.sample_rate_hz)
            sensor_features[cycle_id - 1] = [features[name] for name in FEATURE_NAMES]
        for feature_index, feature_name in enumerate(FEATURE_NAMES):
            columns[f"{sensor_name}__{feature_name}"] = sensor_features[:, feature_index]

    frame = pd.DataFrame(columns)
    expected_columns = 1 + len(PROFILE_LABELS) + len(sensor_specs) * len(FEATURE_NAMES)
    if frame.shape != (expected_cycles, expected_columns):
        raise RuntimeError("feature frame shape does not match the processing contract")
    if frame.isna().to_numpy().any():
        raise RuntimeError("feature frame contains missing values")
    feature_values = frame.iloc[:, 1 + len(PROFILE_LABELS) :].to_numpy(dtype=np.float64)
    if not np.all(np.isfinite(feature_values)):
        raise RuntimeError("feature frame contains nonfinite values")
    return frame


def _data_dictionary(
    sensor_specs: Mapping[str, SensorSpec],
    artifact_version: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "dataset": "UCI hydraulic_systems",
        "artifact_version": artifact_version,
        "cycle_id": {"base": 1, "description": "one-based hydraulic cycle identifier"},
        "labels": {
            label.name: {
                "unit": label.unit,
                "allowed_values": sorted(label.allowed_values),
                "meanings": {str(key): value for key, value in label.meanings.items()},
            }
            for label in PROFILE_LABELS
        },
        "sensors": {
            name: {
                "physical_quantity": spec.physical_quantity,
                "unit": spec.unit,
                "sample_rate_hz": spec.sample_rate_hz,
                "points_per_cycle": spec.points_per_cycle,
            }
            for name, spec in sensor_specs.items()
        },
        "features": {
            name: {
                "formula": _FEATURE_FORMULAS[name],
                "unit_rule": ("sensor unit per second" if name == "slope" else "sensor unit"),
            }
            for name in FEATURE_NAMES
        },
    }


def _processing_report(
    frame: pd.DataFrame,
    sensor_specs: Mapping[str, SensorSpec],
    expected_cycles: int,
    source_sha256: Mapping[str, str],
    source_fingerprint: str,
    processing_fingerprint: str,
    artifact_version: str,
) -> dict[str, object]:
    measured_shapes = {
        spec.file_name: {"rows": expected_cycles, "columns": spec.points_per_cycle}
        for spec in sensor_specs.values()
    }
    measured_shapes["profile.txt"] = {
        "rows": expected_cycles,
        "columns": len(PROFILE_LABELS),
    }
    return {
        "schema_version": 1,
        "dataset": "UCI hydraulic_systems",
        "processing_version": PROCESSING_VERSION,
        "artifact_version": artifact_version,
        "source_fingerprint": source_fingerprint,
        "processing_fingerprint": processing_fingerprint,
        "source_sha256": dict(source_sha256),
        "measured_shapes": measured_shapes,
        "missing_value_count": int(frame.isna().sum().sum()),
        "output_shape": {"rows": len(frame), "columns": len(frame.columns)},
        "warnings": [],
    }


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(serialized)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_file(path: Path) -> None:
    with path.open("rb+") as stream:
        os.fsync(stream.fileno())


def _temporary_outputs(output_dir: Path) -> dict[str, Path]:
    temporary: dict[str, Path] = {}
    for filename in OUTPUT_FILENAMES:
        descriptor, raw_path = tempfile.mkstemp(
            dir=output_dir,
            prefix=f".{filename}.",
            suffix=".tmp",
        )
        os.close(descriptor)
        temporary[filename] = Path(raw_path)
    return temporary


def _validate_temporary_outputs(
    temporary: Mapping[str, Path],
    expected_frame: pd.DataFrame,
    artifact_version: str,
) -> None:
    csv_frame = pd.read_csv(temporary["cycle_features.csv"], float_precision="round_trip")
    parquet_frame = pd.read_parquet(temporary["cycle_features.parquet"])
    if list(csv_frame.columns) != list(expected_frame.columns):
        raise RuntimeError("temporary CSV columns do not match the feature contract")
    if list(parquet_frame.columns) != list(expected_frame.columns):
        raise RuntimeError("temporary Parquet columns do not match the feature contract")
    if csv_frame.shape != expected_frame.shape or parquet_frame.shape != expected_frame.shape:
        raise RuntimeError("temporary output shape does not match the feature contract")

    identity_columns = 1 + len(PROFILE_LABELS)
    expected_identity = expected_frame.iloc[:, :identity_columns].to_numpy(dtype=np.int64)
    if not np.array_equal(
        csv_frame.iloc[:, :identity_columns].to_numpy(dtype=np.int64),
        expected_identity,
    ):
        raise RuntimeError("temporary CSV keys or labels do not match")
    if not np.array_equal(
        parquet_frame.iloc[:, :identity_columns].to_numpy(dtype=np.int64),
        expected_identity,
    ):
        raise RuntimeError("temporary Parquet keys or labels do not match")

    expected_features = expected_frame.iloc[:, identity_columns:].to_numpy(dtype=np.float64)
    for name, frame in (("CSV", csv_frame), ("Parquet", parquet_frame)):
        actual_features = frame.iloc[:, identity_columns:].to_numpy(dtype=np.float64)
        if not np.allclose(
            actual_features,
            expected_features,
            rtol=1e-9,
            atol=1e-12,
            equal_nan=False,
        ):
            raise RuntimeError(f"temporary {name} feature values do not match")

    for filename in ("data_dictionary.json", "processing_report.json"):
        payload = json.loads(temporary[filename].read_text(encoding="utf-8"))
        if payload.get("artifact_version") != artifact_version:
            raise RuntimeError(f"temporary {filename} artifact_version does not match")


def _validated_output_dir(dataset_root: Path, output_dir: Path | str) -> Path:
    destination = Path(output_dir).resolve(strict=False)
    for protected_root in (dataset_root, *PROTECTED_OUTPUT_ROOTS):
        protected = protected_root.resolve(strict=False)
        if destination == protected or destination.is_relative_to(protected):
            raise ValueError("hydraulic output directory is inside a protected source")
    return destination


def run_hydraulic_pipeline(
    dataset_root: Path | str,
    output_dir: Path | str,
    *,
    sensor_specs: Mapping[str, SensorSpec] = SENSOR_SPECS,
    expected_cycles: int = EXPECTED_CYCLE_COUNT,
) -> HydraulicPipelineResult:
    """Validate sources, compute features, and atomically publish four artifacts."""

    source_root = Path(dataset_root).resolve(strict=True)
    if not source_root.is_dir():
        raise ValueError("hydraulic dataset root must be a directory")
    if expected_cycles <= 0 or not sensor_specs:
        raise ValueError("sensor_specs and expected_cycles must be non-empty and positive")
    destination = _validated_output_dir(source_root, output_dir)
    required_paths = _required_paths(source_root, sensor_specs)
    source_sha256 = _source_hashes(required_paths)
    source_fingerprint = _json_fingerprint(source_sha256)
    processing_fingerprint = _json_fingerprint(_processing_contract(sensor_specs, expected_cycles))
    artifact_version = _artifact_version(source_fingerprint, processing_fingerprint)

    frame = _feature_frame(source_root, sensor_specs, expected_cycles)
    if _source_hashes(required_paths) != source_sha256:
        raise HydraulicLoadError(
            "SOURCE_CHANGED",
            source_root.name,
            "source files changed during processing",
        )

    dictionary = _data_dictionary(sensor_specs, artifact_version)
    report = _processing_report(
        frame,
        sensor_specs,
        expected_cycles,
        source_sha256,
        source_fingerprint,
        processing_fingerprint,
        artifact_version,
    )

    destination.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_outputs(destination)
    try:
        frame.to_parquet(
            temporary["cycle_features.parquet"],
            index=False,
            engine="pyarrow",
        )
        _fsync_file(temporary["cycle_features.parquet"])
        frame.to_csv(
            temporary["cycle_features.csv"],
            index=False,
            encoding="utf-8",
            lineterminator="\n",
            float_format="%.17g",
        )
        _fsync_file(temporary["cycle_features.csv"])
        _write_json(temporary["data_dictionary.json"], dictionary)
        _write_json(temporary["processing_report.json"], report)
        _validate_temporary_outputs(temporary, frame, artifact_version)
        for filename in OUTPUT_FILENAMES:
            os.replace(temporary[filename], destination / filename)
    finally:
        for temporary_path in temporary.values():
            temporary_path.unlink(missing_ok=True)

    return HydraulicPipelineResult(
        artifact_version=artifact_version,
        source_fingerprint=source_fingerprint,
        processing_fingerprint=processing_fingerprint,
        output_rows=len(frame),
        output_columns=len(frame.columns),
    )

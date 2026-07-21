from __future__ import annotations

import json
import os
from pathlib import Path
from types import MappingProxyType

import numpy as np
import pandas as pd
import pytest
from scripts import preprocess_hydraulic_data as preprocess_script

from industrial_energy_agent.data_processing import hydraulic_pipeline
from industrial_energy_agent.data_processing.hydraulic_loader import HydraulicLoadError
from industrial_energy_agent.data_processing.hydraulic_pipeline import (
    OUTPUT_FILENAMES,
    HydraulicPipelineResult,
    run_hydraulic_pipeline,
)
from industrial_energy_agent.data_processing.hydraulic_schema import SensorSpec
from industrial_energy_agent.data_processing.sensor_repository import SensorRepository

TEST_SENSOR_SPECS = MappingProxyType(
    {
        "PS1": SensorSpec("PS1", "PS1.txt", "pressure", "bar", 1, 3),
        "FS1": SensorSpec("FS1", "FS1.txt", "volume flow", "l/min", 1, 2),
    }
)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
REAL_OUTPUT = PROJECT_ROOT / "data" / "processed" / "hydraulic"


def _write_valid_source(source: Path) -> None:
    source.mkdir()
    (source / "PS1.txt").write_bytes(b"1\t2\t3\n4\t5\t6\n")
    (source / "FS1.txt").write_bytes(b"10\t12\n20\t24\n")
    (source / "profile.txt").write_bytes(b"3\t100\t0\t130\t0\n20\t90\t1\t115\t1\n")
    (source / "description.txt").write_text("must be ignored", encoding="utf-8")


def test_pipeline_writes_exact_validated_outputs_and_stable_fingerprint(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "processed"
    _write_valid_source(source)

    first = run_hydraulic_pipeline(
        source,
        output,
        sensor_specs=TEST_SENSOR_SPECS,
        expected_cycles=2,
    )
    second = run_hydraulic_pipeline(
        source,
        output,
        sensor_specs=TEST_SENSOR_SPECS,
        expected_cycles=2,
    )

    assert first == second
    assert first.artifact_version.startswith("sha256:")
    assert len(first.artifact_version) == 71
    assert first.output_rows == 2
    assert first.output_columns == 26
    assert {path.name for path in output.iterdir()} == set(OUTPUT_FILENAMES)

    csv_frame = pd.read_csv(output / "cycle_features.csv")
    parquet_frame = pd.read_parquet(output / "cycle_features.parquet")
    assert list(csv_frame.columns) == list(parquet_frame.columns)
    assert list(csv_frame.columns[:6]) == [
        "cycle_id",
        "cooler_condition_pct",
        "valve_condition_pct",
        "pump_leakage_level",
        "accumulator_pressure_bar",
        "stable_flag",
    ]
    assert csv_frame.shape == parquet_frame.shape == (2, 26)
    pd.testing.assert_frame_equal(
        csv_frame.iloc[:, :6],
        parquet_frame.iloc[:, :6],
        check_dtype=False,
    )
    np.testing.assert_allclose(
        csv_frame.iloc[:, 6:].to_numpy(dtype=np.float64),
        parquet_frame.iloc[:, 6:].to_numpy(dtype=np.float64),
        rtol=1e-9,
        atol=1e-12,
    )

    dictionary = json.loads((output / "data_dictionary.json").read_text(encoding="utf-8"))
    report = json.loads((output / "processing_report.json").read_text(encoding="utf-8"))
    assert dictionary["artifact_version"] == report["artifact_version"]
    assert dictionary["sensors"]["PS1"] == {
        "physical_quantity": "pressure",
        "unit": "bar",
        "sample_rate_hz": 1,
        "points_per_cycle": 3,
    }
    assert dictionary["features"]["std"]["formula"] == "population standard deviation, ddof=0"
    assert dictionary["features"]["slope"]["unit_rule"] == "sensor unit per second"
    assert report["output_shape"] == {"rows": 2, "columns": 26}
    assert report["missing_value_count"] == 0
    assert report["warnings"] == []
    assert set(report["source_sha256"]) == {"PS1.txt", "FS1.txt", "profile.txt"}


def test_pipeline_prepares_all_sibling_temps_before_first_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "processed"
    _write_valid_source(source)
    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def recording_replace(
        source_path: str | os.PathLike[str],
        destination_path: str | os.PathLike[str],
    ) -> None:
        source_file = Path(source_path)
        destination_file = Path(destination_path)
        if not replacements:
            assert len(list(output.glob(".*.tmp"))) == len(OUTPUT_FILENAMES)
        replacements.append((source_file, destination_file))
        real_replace(source_file, destination_file)

    monkeypatch.setattr(
        "industrial_energy_agent.data_processing.hydraulic_pipeline.os.replace",
        recording_replace,
    )

    run_hydraulic_pipeline(
        source,
        output,
        sensor_specs=TEST_SENSOR_SPECS,
        expected_cycles=2,
    )

    assert [destination.name for _, destination in replacements] == list(OUTPUT_FILENAMES)
    assert all(source_file.parent == output for source_file, _ in replacements)
    assert not list(output.glob(".*.tmp"))


def test_invalid_source_fails_before_output_directory_is_created(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "processed"
    _write_valid_source(source)
    (source / "PS1.txt").write_bytes(b"1\tbad\t3\n4\t5\t6\n")

    with pytest.raises(HydraulicLoadError) as caught:
        run_hydraulic_pipeline(
            source,
            output,
            sensor_specs=TEST_SENSOR_SPECS,
            expected_cycles=2,
        )

    assert (caught.value.relative_path, caught.value.cycle, caught.value.column) == (
        "PS1.txt",
        1,
        2,
    )
    assert not output.exists()


def test_pipeline_rejects_output_anywhere_under_protected_source_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_dataset = tmp_path / "data" / "raw_dataset"
    manuals = tmp_path / "data" / "manuals"
    source = raw_dataset / "hydraulic_systems"
    output = raw_dataset / "generated-sibling"
    raw_dataset.mkdir(parents=True)
    manuals.mkdir(parents=True)
    _write_valid_source(source)
    monkeypatch.setattr(
        hydraulic_pipeline,
        "PROTECTED_OUTPUT_ROOTS",
        (raw_dataset, manuals),
        raising=False,
    )

    with pytest.raises(ValueError, match="protected source"):
        run_hydraulic_pipeline(
            source,
            output,
            sensor_specs=TEST_SENSOR_SPECS,
            expected_cycles=2,
        )

    assert not output.exists()


def test_sensor_repository_returns_summaries_warnings_and_unit_deltas(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "processed"
    _write_valid_source(source)
    result = run_hydraulic_pipeline(
        source,
        output,
        sensor_specs=TEST_SENSOR_SPECS,
        expected_cycles=2,
    )
    repository = SensorRepository(
        output / "cycle_features.parquet",
        report_path=output / "processing_report.json",
        sensor_specs=TEST_SENSOR_SPECS,
    )

    summary = repository.get_cycle(2)

    assert summary.cycle_id == 2
    assert summary.artifact_version == result.artifact_version
    assert summary.labels["stable_flag"] == 1
    assert summary.features["PS1__mean"] == pytest.approx(5.0)
    assert summary.units["PS1__mean"] == "bar"
    assert summary.units["PS1__slope"] == "bar/s"
    assert summary.warnings == ("可能尚未达到稳态",)

    comparison = repository.compare_cycles([1, 2])

    assert comparison.baseline_cycle_id == 1
    assert comparison.cycle_ids == (1, 2)
    assert comparison.deltas[2]["PS1__mean"] == pytest.approx(3.0)
    assert comparison.units["PS1__mean"] == "bar"
    assert comparison.warnings == ("周期2可能尚未达到稳态",)


@pytest.mark.parametrize("cycle_id", [0, 3, True])
def test_sensor_repository_rejects_out_of_range_cycle_id(
    tmp_path: Path,
    cycle_id: object,
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "processed"
    _write_valid_source(source)
    run_hydraulic_pipeline(
        source,
        output,
        sensor_specs=TEST_SENSOR_SPECS,
        expected_cycles=2,
    )
    repository = SensorRepository(output / "cycle_features.parquet")

    with pytest.raises(ValueError, match="cycle_id"):
        repository.get_cycle(cycle_id)  # type: ignore[arg-type]


def test_sensor_repository_comparison_requires_two_unique_cycles(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "processed"
    _write_valid_source(source)
    run_hydraulic_pipeline(
        source,
        output,
        sensor_specs=TEST_SENSOR_SPECS,
        expected_cycles=2,
    )
    repository = SensorRepository(output / "cycle_features.parquet")

    with pytest.raises(ValueError, match="two unique"):
        repository.compare_cycles([1, 1])


def test_preprocess_cli_delegates_paths_and_prints_safe_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset_root = tmp_path / "source"
    output_dir = tmp_path / "processed"
    captured: list[tuple[Path, Path]] = []

    def fake_pipeline(source: Path, output: Path) -> HydraulicPipelineResult:
        captured.append((source, output))
        return HydraulicPipelineResult(
            artifact_version="sha256:" + "a" * 64,
            source_fingerprint="b" * 64,
            processing_fingerprint="c" * 64,
            output_rows=2_205,
            output_columns=176,
        )

    monkeypatch.setattr(preprocess_script, "run_hydraulic_pipeline", fake_pipeline)

    exit_code = preprocess_script.main(
        ["--dataset-root", str(dataset_root), "--output-dir", str(output_dir)]
    )

    assert exit_code == 0
    assert captured == [(dataset_root, output_dir)]
    output = capsys.readouterr().out
    assert "rows=2205 columns=176" in output
    assert "artifact_version=sha256:" in output


def test_real_processed_output_contract_after_preprocess_script() -> None:
    assert {path.name for path in REAL_OUTPUT.iterdir() if path.is_file()} == set(OUTPUT_FILENAMES)
    csv_frame = pd.read_csv(REAL_OUTPUT / "cycle_features.csv")
    parquet_frame = pd.read_parquet(REAL_OUTPUT / "cycle_features.parquet")
    report = json.loads((REAL_OUTPUT / "processing_report.json").read_text(encoding="utf-8"))
    dictionary = json.loads((REAL_OUTPUT / "data_dictionary.json").read_text(encoding="utf-8"))

    assert csv_frame.shape == parquet_frame.shape == (2_205, 176)
    assert csv_frame["cycle_id"].tolist() == list(range(1, 2_206))
    assert parquet_frame["cycle_id"].tolist() == list(range(1, 2_206))
    identity_columns = 6
    pd.testing.assert_frame_equal(
        csv_frame.iloc[:, :identity_columns],
        parquet_frame.iloc[:, :identity_columns],
        check_dtype=False,
    )
    np.testing.assert_allclose(
        csv_frame.iloc[:, identity_columns:].to_numpy(dtype=np.float64),
        parquet_frame.iloc[:, identity_columns:].to_numpy(dtype=np.float64),
        rtol=1e-9,
        atol=1e-12,
    )
    assert not csv_frame.isna().to_numpy().any()
    assert np.isfinite(parquet_frame.iloc[:, identity_columns:].to_numpy(dtype=np.float64)).all()
    assert len(dictionary["sensors"]) == 17
    assert len(dictionary["features"]) == 10
    assert report["output_shape"] == {"rows": 2_205, "columns": 176}
    assert report["missing_value_count"] == 0
    assert dictionary["artifact_version"] == report["artifact_version"]

    unstable_cycle_id = int(
        parquet_frame.loc[parquet_frame["stable_flag"] == 1, "cycle_id"].iloc[0]
    )
    repository = SensorRepository(REAL_OUTPUT / "cycle_features.parquet")
    assert repository.get_cycle(unstable_cycle_id).warnings == ("可能尚未达到稳态",)

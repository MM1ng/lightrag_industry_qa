from __future__ import annotations

import json
from pathlib import Path

import pytest

from industrial_energy_agent.data_processing.sensor_repository import (
    CycleComparison,
    CycleNotFoundError,
    CycleSummary,
)
from industrial_energy_agent.tools.sensor_tools import (
    CompareSensorCyclesInput,
    CompareSensorCyclesResult,
    QuerySensorCycleInput,
    QuerySensorCycleResult,
    build_compare_sensor_cycles_tool,
    build_query_sensor_cycle_tool,
)

_ARTIFACT_VERSION = "sha256:" + "a" * 64


def _summary(cycle_id: int, value: float) -> CycleSummary:
    return CycleSummary(
        cycle_id=cycle_id,
        artifact_version=_ARTIFACT_VERSION,
        labels={"stable_flag": 0},
        features={"PS1__mean": value},
        units={"PS1__mean": "bar"},
        warnings=(),
    )


class ContractSensorRepository:
    def __init__(self) -> None:
        self.summaries = {1: _summary(1, 1.0), 2: _summary(2, 2.5)}

    def get_cycle(self, cycle_id: int) -> CycleSummary:
        try:
            return self.summaries[cycle_id]
        except KeyError:
            raise CycleNotFoundError("cycle absent") from None

    def compare_cycles(self, cycle_ids: list[int] | tuple[int, ...]) -> CycleComparison:
        summaries = tuple(self.get_cycle(cycle_id) for cycle_id in cycle_ids)
        baseline = summaries[0]
        return CycleComparison(
            baseline_cycle_id=baseline.cycle_id,
            cycle_ids=tuple(cycle_ids),
            artifact_version=_ARTIFACT_VERSION,
            deltas={
                summary.cycle_id: {
                    "PS1__mean": summary.features["PS1__mean"] - baseline.features["PS1__mean"]
                }
                for summary in summaries[1:]
            },
            units=baseline.units,
            warnings=(),
            summaries=summaries,
        )


class FailingSensorRepository:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def get_cycle(self, cycle_id: int) -> object:
        raise self.error

    def compare_cycles(self, cycle_ids: list[int]) -> object:
        raise self.error


class IncompleteComparisonRepository(ContractSensorRepository):
    def compare_cycles(self, cycle_ids: list[int] | tuple[int, ...]) -> CycleComparison:
        comparison = super().compare_cycles(cycle_ids)
        return CycleComparison(
            baseline_cycle_id=comparison.baseline_cycle_id,
            cycle_ids=comparison.cycle_ids,
            artifact_version=comparison.artifact_version,
            deltas=comparison.deltas,
            units=comparison.units,
            warnings=comparison.warnings,
            summaries=comparison.summaries[:1],
        )


class NonFiniteSensorRepository(ContractSensorRepository):
    def get_cycle(self, cycle_id: int) -> CycleSummary:
        summary = super().get_cycle(cycle_id)
        return CycleSummary(
            cycle_id=summary.cycle_id,
            artifact_version=summary.artifact_version,
            labels=summary.labels,
            features={"PS1__mean": float("nan")},
            units=summary.units,
            warnings=summary.warnings,
        )


class InconsistentComparisonRepository(ContractSensorRepository):
    def __init__(self, inconsistency: str) -> None:
        super().__init__()
        self.inconsistency = inconsistency

    def compare_cycles(self, cycle_ids: list[int] | tuple[int, ...]) -> CycleComparison:
        comparison = super().compare_cycles(cycle_ids)
        deltas = {cycle_id: dict(values) for cycle_id, values in comparison.deltas.items()}
        units = dict(comparison.units)
        if self.inconsistency == "wrong_delta":
            deltas[2]["PS1__mean"] = 999.0
        elif self.inconsistency == "wrong_units":
            units["PS1__mean"] = "psi"
        elif self.inconsistency == "missing_cycle":
            deltas = {}
        elif self.inconsistency == "extra_cycle":
            deltas[3] = {"PS1__mean": 7.0}
        elif self.inconsistency == "missing_feature":
            deltas[2] = {}
        elif self.inconsistency == "extra_feature":
            deltas[2]["FS1__mean"] = 4.0
        return CycleComparison(
            baseline_cycle_id=comparison.baseline_cycle_id,
            cycle_ids=comparison.cycle_ids,
            artifact_version=comparison.artifact_version,
            deltas=deltas,
            units=units,
            warnings=comparison.warnings,
            summaries=comparison.summaries,
        )


@pytest.fixture
def sensor_repository() -> ContractSensorRepository:
    return ContractSensorRepository()


def test_query_sensor_cycle_returns_typed_cycle_summary(
    sensor_repository: ContractSensorRepository,
) -> None:
    tool = build_query_sensor_cycle_tool(sensor_repository)

    result = tool.invoke({"cycle_id": 1, "request_id": "req-sensor-success"})

    parsed = QuerySensorCycleResult.model_validate(result)
    assert parsed.root.ok is True
    assert result["cycle"]["cycle_id"] == 1
    assert result["cycle"]["citation"]["source_type"] == "sensor"
    assert result["trace"]["evidence_count"] == 1
    assert tool.name == "query_sensor_cycle"
    assert tool.args_schema is QuerySensorCycleInput


def test_cycle_2206_returns_structured_range_error(
    sensor_repository: ContractSensorRepository,
) -> None:
    tool = build_query_sensor_cycle_tool(sensor_repository)

    result = tool.invoke({"cycle_id": 2206, "request_id": "req-sensor-range"})

    assert result["ok"] is False
    assert result["error"]["code"] == "CYCLE_OUT_OF_RANGE"
    assert result["error"]["details"]["valid_range"] == [1, 2205]


def test_query_sensor_cycle_returns_not_found_without_fake_success() -> None:
    tool = build_query_sensor_cycle_tool(
        FailingSensorRepository(CycleNotFoundError("cycle_id is outside the processed range"))
    )

    result = tool.invoke({"cycle_id": 12, "request_id": "req-sensor-missing"})

    assert result["ok"] is False
    assert result["error"]["code"] == "CYCLE_NOT_FOUND"
    assert "cycle" not in result


def test_query_sensor_cycle_sanitizes_dependency_failure() -> None:
    tool = build_query_sensor_cycle_tool(
        FailingSensorRepository(
            RuntimeError('Traceback File "D:\\private\\sensor.py" LIGHTRAG_API_KEY=test-secret')
        )
    )

    result = tool.invoke({"cycle_id": 1, "request_id": "req-sensor-dependency"})
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is False
    assert result["error"]["code"] == "SENSOR_DEPENDENCY_ERROR"
    assert result["trace"]["status"] == "failure"
    assert "Traceback" not in rendered
    assert "test-secret" not in rendered
    assert "sensor.py" not in rendered


def test_compare_sensor_cycles_returns_typed_deltas(
    sensor_repository: ContractSensorRepository,
) -> None:
    tool = build_compare_sensor_cycles_tool(sensor_repository)

    result = tool.invoke({"cycle_ids": [1, 2], "request_id": "req-compare-success"})

    parsed = CompareSensorCyclesResult.model_validate(result)
    assert parsed.root.ok is True
    assert result["comparison"]["baseline_cycle_id"] == 1
    assert result["comparison"]["cycle_ids"] == [1, 2]
    assert result["comparison"]["deltas"]
    citations = result["comparison"]["citations"]
    assert [citation["cycle_id"] for citation in citations] == [1, 2]
    assert all(citation["source_type"] == "sensor" for citation in citations)
    assert all(citation["artifact_version"] for citation in citations)
    assert all(citation["features"].keys() == citation["units"].keys() for citation in citations)
    assert all(citation["features"] for citation in citations)
    assert tool.name == "compare_sensor_cycles"
    assert tool.args_schema is CompareSensorCyclesInput


def test_compare_sensor_cycles_rejects_incomplete_citation_summaries() -> None:
    tool = build_compare_sensor_cycles_tool(IncompleteComparisonRepository())

    result = tool.invoke({"cycle_ids": [1, 2], "request_id": "req-compare-incomplete"})

    assert result["ok"] is False
    assert result["error"]["code"] == "SENSOR_ARTIFACT_ERROR"
    assert "comparison" not in result


@pytest.mark.parametrize(
    "inconsistency",
    [
        "wrong_delta",
        "wrong_units",
        "missing_cycle",
        "extra_cycle",
        "missing_feature",
        "extra_feature",
    ],
)
def test_compare_sensor_cycles_rejects_inconsistent_repository_comparison(
    inconsistency: str,
) -> None:
    tool = build_compare_sensor_cycles_tool(InconsistentComparisonRepository(inconsistency))

    result = tool.invoke({"cycle_ids": [1, 2], "request_id": f"req-compare-{inconsistency}"})

    assert result["ok"] is False
    assert result["error"]["code"] == "SENSOR_ARTIFACT_ERROR"
    assert "comparison" not in result


def test_compare_sensor_cycles_requires_two_unique_cycles(
    sensor_repository: ContractSensorRepository,
) -> None:
    tool = build_compare_sensor_cycles_tool(sensor_repository)

    result = tool.invoke({"cycle_ids": [1, 1], "request_id": "req-compare-invalid"})

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_CYCLE_SELECTION"


def test_compare_sensor_cycles_returns_not_found() -> None:
    tool = build_compare_sensor_cycles_tool(
        FailingSensorRepository(CycleNotFoundError("cycle_id is outside the processed range"))
    )

    result = tool.invoke({"cycle_ids": [1, 2], "request_id": "req-compare-missing"})

    assert result["ok"] is False
    assert result["error"]["code"] == "CYCLE_NOT_FOUND"


def test_compare_sensor_cycles_reports_dependency_failure() -> None:
    tool = build_compare_sensor_cycles_tool(FailingSensorRepository(OSError("parquet unavailable")))

    result = tool.invoke({"cycle_ids": [1, 2], "request_id": "req-compare-dependency"})

    assert result["ok"] is False
    assert result["error"]["code"] == "SENSOR_DEPENDENCY_ERROR"
    assert "comparison" not in result


@pytest.mark.parametrize(
    ("tool_kind", "payload"),
    [
        ("query", {"cycle_id": 1, "request_id": "req-query-corrupt"}),
        ("compare", {"cycle_ids": [1, 2], "request_id": "req-compare-corrupt"}),
    ],
)
def test_sensor_artifact_corruption_is_not_mislabeled_as_cycle_not_found(
    tool_kind: str,
    payload: dict[str, object],
) -> None:
    repository = FailingSensorRepository(ValueError("corrupt labels or feature schema"))
    tool = (
        build_query_sensor_cycle_tool(repository)
        if tool_kind == "query"
        else build_compare_sensor_cycles_tool(repository)
    )

    result = tool.invoke(payload)

    assert result["ok"] is False
    assert result["error"]["code"] == "SENSOR_ARTIFACT_ERROR"
    assert result["error"]["retryable"] is False


@pytest.mark.parametrize(
    ("tool_kind", "payload"),
    [
        ("query", {"cycle_id": 1, "request_id": "req-query-nonfinite"}),
        ("compare", {"cycle_ids": [1, 2], "request_id": "req-compare-nonfinite"}),
    ],
)
def test_nonfinite_sensor_output_returns_typed_artifact_error(
    tool_kind: str,
    payload: dict[str, object],
) -> None:
    repository = NonFiniteSensorRepository()
    tool = (
        build_query_sensor_cycle_tool(repository)
        if tool_kind == "query"
        else build_compare_sensor_cycles_tool(repository)
    )

    result = tool.invoke(payload)

    assert result["ok"] is False
    assert result["error"]["code"] == "SENSOR_ARTIFACT_ERROR"
    assert "cycle" not in result
    assert "comparison" not in result


def test_tool_unit_tests_do_not_depend_on_ignored_processed_sensor_artifact() -> None:
    tools_tests = Path(__file__).parent
    references = "\n".join(
        path.read_text(encoding="utf-8") for path in tools_tests.glob("test_*.py")
    )

    ignored_artifact = "data/processed/hydraulic/" + "cycle_features.parquet"
    assert ignored_artifact not in references

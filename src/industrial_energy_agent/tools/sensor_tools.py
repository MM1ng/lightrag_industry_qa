"""Structured read-only tools over processed hydraulic cycle summaries."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, Literal, Protocol

from pydantic import ConfigDict, Field, RootModel

from industrial_energy_agent.data_processing.sensor_repository import (
    CycleComparison,
    CycleNotFoundError,
    CycleSummary,
    SensorArtifactError,
)
from industrial_energy_agent.domain.models import SensorCitation, TraceEvent
from industrial_energy_agent.tools.common import (
    SafeStructuredTool,
    ToolFailure,
    ToolInputModel,
    ToolModel,
    build_safe_structured_tool,
    dump_result,
    make_error,
    make_trace,
    new_request_id,
    started_at,
)

_MIN_CYCLE_ID = 1
_MAX_CYCLE_ID = 2_205
_DATASET = "UCI hydraulic test-rig cycle summaries"


class SensorRepositoryBoundary(Protocol):
    def get_cycle(self, cycle_id: int) -> CycleSummary: ...

    def compare_cycles(self, cycle_ids: Sequence[int]) -> CycleComparison: ...


class QuerySensorCycleInput(ToolInputModel):
    cycle_id: Annotated[int, Field(strict=True)]


class SensorCycleData(ToolModel):
    model_config = ConfigDict(allow_inf_nan=False)

    cycle_id: int
    artifact_version: str
    labels: dict[str, int]
    features: dict[str, float]
    units: dict[str, str]
    warnings: tuple[str, ...]
    citation: SensorCitation


class QuerySensorCycleSuccess(ToolModel):
    ok: Literal[True] = True
    cycle: SensorCycleData
    trace: TraceEvent


class QuerySensorCycleFailure(ToolFailure):
    pass


class QuerySensorCycleResult(RootModel[QuerySensorCycleSuccess | QuerySensorCycleFailure]):
    pass


class CompareSensorCyclesInput(ToolInputModel):
    cycle_ids: list[Annotated[int, Field(strict=True, ge=1, le=2_205)]] = Field(
        min_length=2,
        max_length=20,
    )


class SensorComparisonData(ToolModel):
    model_config = ConfigDict(allow_inf_nan=False)

    baseline_cycle_id: int
    cycle_ids: tuple[int, ...]
    artifact_version: str
    deltas: dict[int, dict[str, float]]
    units: dict[str, str]
    warnings: tuple[str, ...]
    citations: tuple[SensorCitation, ...]


class CompareSensorCyclesSuccess(ToolModel):
    ok: Literal[True] = True
    comparison: SensorComparisonData
    trace: TraceEvent


class CompareSensorCyclesFailure(ToolFailure):
    pass


class CompareSensorCyclesResult(RootModel[CompareSensorCyclesSuccess | CompareSensorCyclesFailure]):
    pass


def _range_error(
    request_id: str, tool: str, started: float, cycle_ids: Sequence[int]
) -> ToolFailure:
    code = "CYCLE_OUT_OF_RANGE"
    return ToolFailure(
        error=make_error(
            code,
            "周期编号超出可查询范围。",
            details={"valid_range": [_MIN_CYCLE_ID, _MAX_CYCLE_ID]},
        ),
        trace=make_trace(
            request_id=request_id,
            tool=tool,
            started=started,
            status="failure",
            parameter_summary={"cycle_count": len(cycle_ids)},
            error_code=code,
        ),
    )


def _citation(summary: CycleSummary) -> SensorCitation:
    return SensorCitation(
        citation_id=f"sensor-cycle-{summary.cycle_id}-{summary.artifact_version[-12:]}",
        dataset=_DATASET,
        cycle_id=summary.cycle_id,
        artifact_version=summary.artifact_version,
        features=dict(summary.features),
        units=dict(summary.units),
    )


def _validate_comparison(
    comparison: CycleComparison,
    requested_cycle_ids: tuple[int, ...],
) -> None:
    summary_ids = tuple(summary.cycle_id for summary in comparison.summaries)
    if (
        comparison.cycle_ids != requested_cycle_ids
        or comparison.baseline_cycle_id != requested_cycle_ids[0]
        or summary_ids != requested_cycle_ids
        or any(
            summary.artifact_version != comparison.artifact_version
            for summary in comparison.summaries
        )
    ):
        raise SensorArtifactError("comparison citation summaries are inconsistent")

    baseline = comparison.summaries[0]
    feature_names = set(baseline.features)
    if (
        not feature_names
        or set(baseline.units) != feature_names
        or dict(comparison.units) != dict(baseline.units)
        or any(
            set(summary.features) != feature_names or dict(summary.units) != dict(baseline.units)
            for summary in comparison.summaries
        )
    ):
        raise SensorArtifactError("comparison feature units are inconsistent")

    expected_delta_cycles = set(requested_cycle_ids[1:])
    if set(comparison.deltas) != expected_delta_cycles:
        raise SensorArtifactError("comparison delta cycles are inconsistent")
    for summary in comparison.summaries[1:]:
        deltas = comparison.deltas[summary.cycle_id]
        if set(deltas) != feature_names or any(
            deltas[name] != summary.features[name] - baseline.features[name]
            for name in feature_names
        ):
            raise SensorArtifactError("comparison deltas are inconsistent")


class QuerySensorCycleService:
    def __init__(self, repository: SensorRepositoryBoundary) -> None:
        self._repository = repository

    def execute(self, args: QuerySensorCycleInput) -> QuerySensorCycleResult:
        started = started_at()
        if not _MIN_CYCLE_ID <= args.cycle_id <= _MAX_CYCLE_ID:
            failure = _range_error(args.request_id, "query_sensor_cycle", started, [args.cycle_id])
            return QuerySensorCycleResult(root=QuerySensorCycleFailure(**failure.model_dump()))
        try:
            summary = self._repository.get_cycle(args.cycle_id)
            features = dict(summary.features)
            units = dict(summary.units)
            cycle = SensorCycleData(
                cycle_id=summary.cycle_id,
                artifact_version=summary.artifact_version,
                labels=dict(summary.labels),
                features=features,
                units=units,
                warnings=summary.warnings,
                citation=_citation(summary),
            )
        except CycleNotFoundError:
            code = "CYCLE_NOT_FOUND"
            failure = QuerySensorCycleFailure(
                error=make_error(code, "未找到指定周期。"),
                trace=make_trace(
                    request_id=args.request_id,
                    tool="query_sensor_cycle",
                    started=started,
                    status="failure",
                    parameter_summary={"cycle_id": args.cycle_id},
                    error_code=code,
                ),
            )
            return QuerySensorCycleResult(root=failure)
        except (SensorArtifactError, ValueError):
            code = "SENSOR_ARTIFACT_ERROR"
            failure = QuerySensorCycleFailure(
                error=make_error(code, "传感器处理产物不满足读取契约。"),
                trace=make_trace(
                    request_id=args.request_id,
                    tool="query_sensor_cycle",
                    started=started,
                    status="failure",
                    parameter_summary={"cycle_id": args.cycle_id},
                    error_code=code,
                ),
            )
            return QuerySensorCycleResult(root=failure)
        except Exception:
            code = "SENSOR_DEPENDENCY_ERROR"
            failure = QuerySensorCycleFailure(
                error=make_error(code, "传感器数据暂时不可用。", retryable=True),
                trace=make_trace(
                    request_id=args.request_id,
                    tool="query_sensor_cycle",
                    started=started,
                    status="failure",
                    parameter_summary={"cycle_id": args.cycle_id},
                    error_code=code,
                ),
            )
            return QuerySensorCycleResult(root=failure)
        success = QuerySensorCycleSuccess(
            cycle=cycle,
            trace=make_trace(
                request_id=args.request_id,
                tool="query_sensor_cycle",
                started=started,
                status="success",
                evidence_count=1,
                parameter_summary={"cycle_id": args.cycle_id},
            ),
        )
        return QuerySensorCycleResult(root=success)


class CompareSensorCyclesService:
    def __init__(self, repository: SensorRepositoryBoundary) -> None:
        self._repository = repository

    def execute(self, args: CompareSensorCyclesInput) -> CompareSensorCyclesResult:
        started = started_at()
        unique_ids = tuple(dict.fromkeys(args.cycle_ids))
        if len(unique_ids) < 2:
            code = "INVALID_CYCLE_SELECTION"
            failure = CompareSensorCyclesFailure(
                error=make_error(code, "至少需要两个不同的周期编号。"),
                trace=make_trace(
                    request_id=args.request_id,
                    tool="compare_sensor_cycles",
                    started=started,
                    status="failure",
                    parameter_summary={"cycle_count": len(unique_ids)},
                    error_code=code,
                ),
            )
            return CompareSensorCyclesResult(root=failure)
        if any(not _MIN_CYCLE_ID <= cycle_id <= _MAX_CYCLE_ID for cycle_id in unique_ids):
            range_failure = _range_error(
                args.request_id,
                "compare_sensor_cycles",
                started,
                unique_ids,
            )
            return CompareSensorCyclesResult(
                root=CompareSensorCyclesFailure(**range_failure.model_dump())
            )
        try:
            comparison = self._repository.compare_cycles(unique_ids)
            _validate_comparison(comparison, unique_ids)
            comparison_data = SensorComparisonData(
                baseline_cycle_id=comparison.baseline_cycle_id,
                cycle_ids=comparison.cycle_ids,
                artifact_version=comparison.artifact_version,
                deltas={key: dict(value) for key, value in comparison.deltas.items()},
                units=dict(comparison.units),
                warnings=comparison.warnings,
                citations=tuple(_citation(summary) for summary in comparison.summaries),
            )
        except CycleNotFoundError:
            code = "CYCLE_NOT_FOUND"
            failure = CompareSensorCyclesFailure(
                error=make_error(code, "一个或多个周期不存在。"),
                trace=make_trace(
                    request_id=args.request_id,
                    tool="compare_sensor_cycles",
                    started=started,
                    status="failure",
                    parameter_summary={"cycle_count": len(unique_ids)},
                    error_code=code,
                ),
            )
            return CompareSensorCyclesResult(root=failure)
        except (SensorArtifactError, ValueError):
            code = "SENSOR_ARTIFACT_ERROR"
            failure = CompareSensorCyclesFailure(
                error=make_error(code, "传感器处理产物不满足读取契约。"),
                trace=make_trace(
                    request_id=args.request_id,
                    tool="compare_sensor_cycles",
                    started=started,
                    status="failure",
                    parameter_summary={"cycle_count": len(unique_ids)},
                    error_code=code,
                ),
            )
            return CompareSensorCyclesResult(root=failure)
        except Exception:
            code = "SENSOR_DEPENDENCY_ERROR"
            failure = CompareSensorCyclesFailure(
                error=make_error(code, "传感器数据暂时不可用。", retryable=True),
                trace=make_trace(
                    request_id=args.request_id,
                    tool="compare_sensor_cycles",
                    started=started,
                    status="failure",
                    parameter_summary={"cycle_count": len(unique_ids)},
                    error_code=code,
                ),
            )
            return CompareSensorCyclesResult(root=failure)
        success = CompareSensorCyclesSuccess(
            comparison=comparison_data,
            trace=make_trace(
                request_id=args.request_id,
                tool="compare_sensor_cycles",
                started=started,
                status="success",
                evidence_count=len(comparison.cycle_ids),
                parameter_summary={"cycle_count": len(comparison.cycle_ids)},
            ),
        )
        return CompareSensorCyclesResult(root=success)


def build_query_sensor_cycle_tool(repository: SensorRepositoryBoundary) -> SafeStructuredTool:
    service = QuerySensorCycleService(repository)

    def query_sensor_cycle(cycle_id: int, request_id: str = "") -> dict[str, Any]:
        args = QuerySensorCycleInput(
            cycle_id=cycle_id,
            request_id=request_id or new_request_id(),
        )
        return dump_result(service.execute(args))

    return build_safe_structured_tool(
        func=query_sensor_cycle,
        name="query_sensor_cycle",
        description="查询一个 UCI 液压实验台周期的只读结构化摘要。",
        args_schema=QuerySensorCycleInput,
    )


def build_compare_sensor_cycles_tool(repository: SensorRepositoryBoundary) -> SafeStructuredTool:
    service = CompareSensorCyclesService(repository)

    def compare_sensor_cycles(cycle_ids: list[int], request_id: str = "") -> dict[str, Any]:
        args = CompareSensorCyclesInput(
            cycle_ids=cycle_ids,
            request_id=request_id or new_request_id(),
        )
        return dump_result(service.execute(args))

    return build_safe_structured_tool(
        func=compare_sensor_cycles,
        name="compare_sensor_cycles",
        description="比较至少两个 UCI 液压实验台周期的特征差值。",
        args_schema=CompareSensorCyclesInput,
    )

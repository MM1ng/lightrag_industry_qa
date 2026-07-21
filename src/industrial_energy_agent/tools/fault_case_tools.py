"""Deterministic search over explicitly synthetic demonstration fault cases."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import Field, RootModel, TypeAdapter

from industrial_energy_agent.domain.models import SyntheticCitation, TraceEvent
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

_PROJECT_FAULT_CASE_PATH = (
    Path(__file__).resolve().parents[3] / "data/synthetic/fault_cases.json"
).resolve()


class FaultCase(ToolModel):
    applicable_equipment_ids: list[str]
    candidate_causes: list[str]
    case_id: str
    data_type: Literal["synthetic_demo"]
    entity_id: str
    evidence_scope: Literal["synthetic_pattern_only"]
    generator_version: str
    provenance_note: str
    recommended_checks: list[str]
    safety_notes: list[str]
    seed: int
    symptoms: list[str]
    title: str


class FaultCaseRepositoryBoundary(Protocol):
    def list_cases(self) -> tuple[FaultCase, ...]: ...


class JsonFaultCaseRepository:
    """Read only the scoped local synthetic fault-case file."""

    def __init__(self) -> None:
        pass

    def list_cases(self) -> tuple[FaultCase, ...]:
        resource = files("industrial_energy_agent").joinpath("resources/fault_cases.json")
        try:
            text = resource.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = _PROJECT_FAULT_CASE_PATH.read_text(encoding="utf-8")
        payload: object = json.loads(text)
        return tuple(TypeAdapter(list[FaultCase]).validate_python(payload))


class SearchFaultCasesInput(ToolInputModel):
    query: str = Field(min_length=2, max_length=500)
    equipment_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    top_k: Annotated[int, Field(strict=True, ge=1, le=20)] = 5


class FaultCaseMatch(ToolModel):
    case_id: str
    title: str
    data_type: Literal["synthetic_demo"] = "synthetic_demo"
    applicable_equipment_ids: list[str]
    symptoms: list[str]
    candidate_causes: list[str]
    recommended_checks: list[str]
    safety_notes: list[str]
    provenance_note: str
    citation: SyntheticCitation


class SearchFaultCasesSuccess(ToolModel):
    ok: Literal[True] = True
    cases: list[FaultCaseMatch]
    trace: TraceEvent


class SearchFaultCasesFailure(ToolFailure):
    pass


class SearchFaultCasesResult(RootModel[SearchFaultCasesSuccess | SearchFaultCasesFailure]):
    pass


def _matches(case: FaultCase, query: str, equipment_id: str | None) -> bool:
    if equipment_id is not None and equipment_id not in case.applicable_equipment_ids:
        return False
    fields = [
        case.title,
        *case.symptoms,
        *case.candidate_causes,
        *case.recommended_checks,
    ]
    return query.casefold() in " ".join(fields).casefold()


class SearchFaultCasesService:
    def __init__(self, repository: FaultCaseRepositoryBoundary) -> None:
        self._repository = repository

    def execute(self, args: SearchFaultCasesInput) -> SearchFaultCasesResult:
        started = started_at()
        if len(args.query.strip()) < 2 or not 1 <= args.top_k <= 20:
            return self._failure(
                args,
                started,
                "INVALID_INPUT",
                "查询词不能为空, top_k 必须在 1 到 20 之间。",
            )
        try:
            cases = self._repository.list_cases()
        except Exception:
            return self._failure(
                args,
                started,
                "FAULT_CASE_DEPENDENCY_ERROR",
                "模拟故障案例暂时不可用。",
                retryable=True,
            )
        matches = [case for case in cases if _matches(case, args.query.strip(), args.equipment_id)]
        output = [
            FaultCaseMatch(
                case_id=case.case_id,
                title=case.title,
                applicable_equipment_ids=case.applicable_equipment_ids,
                symptoms=case.symptoms,
                candidate_causes=case.candidate_causes,
                recommended_checks=case.recommended_checks,
                safety_notes=case.safety_notes,
                provenance_note=case.provenance_note,
                citation=SyntheticCitation(
                    citation_id=case.case_id,
                    source_file="fault_cases.json",
                    entity_id=case.entity_id,
                    case_id=case.case_id,
                ),
            )
            for case in matches[: args.top_k]
        ]
        success = SearchFaultCasesSuccess(
            cases=output,
            trace=make_trace(
                request_id=args.request_id,
                tool="search_fault_cases",
                started=started,
                status="success",
                evidence_count=len(output),
                parameter_summary={
                    "query_length": len(args.query),
                    "equipment_filter": args.equipment_id is not None,
                    "top_k": args.top_k,
                },
            ),
        )
        return SearchFaultCasesResult(root=success)

    @staticmethod
    def _failure(
        args: SearchFaultCasesInput,
        started: float,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> SearchFaultCasesResult:
        failure = SearchFaultCasesFailure(
            error=make_error(code, message, retryable=retryable),
            trace=make_trace(
                request_id=args.request_id,
                tool="search_fault_cases",
                started=started,
                status="failure",
                parameter_summary={
                    "query_length": len(args.query),
                    "equipment_filter": args.equipment_id is not None,
                    "top_k": args.top_k,
                },
                error_code=code,
            ),
        )
        return SearchFaultCasesResult(root=failure)


def build_search_fault_cases_tool(repository: FaultCaseRepositoryBoundary) -> SafeStructuredTool:
    service = SearchFaultCasesService(repository)

    def search_fault_cases(
        query: str,
        equipment_id: str | None = None,
        top_k: int = 5,
        request_id: str = "",
    ) -> dict[str, Any]:
        args = SearchFaultCasesInput(
            query=query,
            equipment_id=equipment_id,
            top_k=top_k,
            request_id=request_id or new_request_id(),
        )
        return dump_result(service.execute(args))

    return build_safe_structured_tool(
        func=search_fault_cases,
        name="search_fault_cases",
        description="只读检索 synthetic_demo 故障案例, 结果不代表真实企业或工业验证。",
        args_schema=SearchFaultCasesInput,
    )

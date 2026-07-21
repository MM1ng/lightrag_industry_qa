from __future__ import annotations

import inspect
import json
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

from industrial_energy_agent.tools.fault_case_tools import (
    FaultCase,
    JsonFaultCaseRepository,
    SearchFaultCasesInput,
    SearchFaultCasesResult,
    build_search_fault_cases_tool,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class FailingFaultCaseRepository:
    def list_cases(self) -> tuple[FaultCase, ...]:
        raise RuntimeError('Traceback File "D:\\private\\cases.py" SERVICE_TOKEN=case-secret')


def _repository() -> JsonFaultCaseRepository:
    return JsonFaultCaseRepository()


def test_json_repository_accepts_only_the_scoped_synthetic_fault_case_file(
    tmp_path: Path,
) -> None:
    with pytest.raises(TypeError):
        JsonFaultCaseRepository(tmp_path / "other_cases.json")


def test_json_repository_rejects_same_suffix_outside_configured_project_path(
    tmp_path: Path,
) -> None:
    impostor = tmp_path / "data/synthetic/fault_cases.json"
    impostor.parent.mkdir(parents=True)
    impostor.write_text("[]", encoding="utf-8")

    with pytest.raises(TypeError):
        JsonFaultCaseRepository(impostor)


def test_json_repository_cannot_self_authorize_an_external_allowed_path(
    tmp_path: Path,
) -> None:
    untrusted = tmp_path / "untrusted/fault_cases.json"
    untrusted.parent.mkdir(parents=True)
    untrusted.write_text("[]", encoding="utf-8")

    with pytest.raises((TypeError, ValueError)):
        JsonFaultCaseRepository(untrusted, allowed_path=untrusted)


def test_json_repository_public_constructor_exposes_no_path_trust_override() -> None:
    assert inspect.signature(JsonFaultCaseRepository).parameters == {}


def test_wheel_configuration_force_includes_authoritative_fault_case_resource() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    force_include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert force_include == {
        "data/synthetic/fault_cases.json": ("industrial_energy_agent/resources/fault_cases.json")
    }


def test_fault_case_resource_loads_from_isolated_wheel_archive(tmp_path: Path) -> None:
    wheel_path = tmp_path / "energyops_copilot-0.1.0-py3-none-any.whl"
    source_package = PROJECT_ROOT / "src/industrial_energy_agent"
    resource_source = PROJECT_ROOT / "data/synthetic/fault_cases.json"

    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
        for source_file in source_package.rglob("*.py"):
            wheel.write(
                source_file,
                source_file.relative_to(source_package.parent).as_posix(),
            )
        wheel.write(
            resource_source,
            "industrial_energy_agent/resources/fault_cases.json",
        )

    probe = """
import json
import sys

sys.path.insert(0, sys.argv[1])
from industrial_energy_agent.tools.fault_case_tools import JsonFaultCaseRepository

cases = JsonFaultCaseRepository().list_cases()
print(json.dumps({"count": len(cases), "first": cases[0].case_id}))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(wheel_path)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"count": 3, "first": "CASE-DEMO-001"}


def test_search_fault_cases_returns_synthetic_demo_provenance() -> None:
    tool = build_search_fault_cases_tool(_repository())

    result = tool.invoke(
        {
            "query": "出口压力偏低",
            "equipment_id": "PUMP-001",
            "request_id": "req-cases-success",
        }
    )

    parsed = SearchFaultCasesResult.model_validate(result)
    assert parsed.root.ok is True
    assert result["cases"][0]["case_id"] == "CASE-DEMO-001"
    assert result["cases"][0]["data_type"] == "synthetic_demo"
    assert result["cases"][0]["citation"]["data_type"] == "synthetic_demo"
    assert tool.name == "search_fault_cases"
    assert tool.args_schema is SearchFaultCasesInput


def test_search_fault_cases_returns_empty_success() -> None:
    tool = build_search_fault_cases_tool(_repository())

    result = tool.invoke({"query": "完全不存在的现象", "request_id": "req-cases-empty"})

    assert result["ok"] is True
    assert result["cases"] == []


def test_search_fault_cases_returns_structured_invalid_input() -> None:
    tool = build_search_fault_cases_tool(_repository())

    result = tool.invoke({"query": "  ", "request_id": "req-cases-invalid"})

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"


def test_search_fault_cases_does_not_fake_success_on_dependency_failure() -> None:
    tool = build_search_fault_cases_tool(FailingFaultCaseRepository())

    result = tool.invoke({"query": "压力偏低", "request_id": "req-cases-error"})
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is False
    assert result["error"]["code"] == "FAULT_CASE_DEPENDENCY_ERROR"
    assert "cases" not in result
    assert "Traceback" not in rendered
    assert "case-secret" not in rendered
    assert "cases.py" not in rendered

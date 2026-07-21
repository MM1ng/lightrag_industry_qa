from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from industrial_energy_agent.domain.enums import EvidenceGrade, RiskLevel
from industrial_energy_agent.domain.errors import DomainValidationError
from industrial_energy_agent.domain.models import DiagnosisRecord, TraceEvent
from industrial_energy_agent.persistence.database import Database
from industrial_energy_agent.persistence.session_repository import SessionRepository

FIXED_NOW = datetime(2026, 7, 21, 8, 30, tzinfo=UTC)


def _repository(tmp_path: Path) -> SessionRepository:
    database = Database(tmp_path / "energyops.sqlite")
    database.initialize()
    return SessionRepository(database, clock=lambda: FIXED_NOW)


def _diagnosis(diagnosis_id: str, conversation_id: str) -> DiagnosisRecord:
    return DiagnosisRecord(
        diagnosis_id=diagnosis_id,
        request_id=f"request-{diagnosis_id}",
        conversation_id=conversation_id,
        equipment="PUMP-001",
        observed_anomalies=["用户描述出口压力下降"],
        manual_evidence=[],
        sensor_evidence=[],
        synthetic_case_evidence=[],
        candidate_causes=[],
        recommended_checks=["先确认隔离边界"],
        risk_level=RiskLevel.LOW,
        approval_required=False,
        evidence_grade=EvidenceGrade.INSUFFICIENT,
        limitations=["未提供周期证据"],
        unknowns=["现场测量值未知"],
    )


def test_session_preserves_one_based_selected_cycle_and_can_clear_it(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.ensure_session("conv-a", summary={"client": "streamlit"})

    repository.set_selected_cycle("conv-a", 1_200)
    selected = repository.get_session("conv-a")

    assert selected is not None
    assert selected.selected_cycle_id == 1_200
    assert selected.summary == {"client": "streamlit"}
    assert selected.created_at == selected.updated_at == FIXED_NOW

    repository.set_selected_cycle("conv-a", None)
    assert repository.get_session("conv-a").selected_cycle_id is None  # type: ignore[union-attr]


@pytest.mark.parametrize("cycle_id", [0, 2_206, True])
def test_session_rejects_invalid_selected_cycle(tmp_path: Path, cycle_id: object) -> None:
    repository = _repository(tmp_path)
    repository.ensure_session("conv-a")

    with pytest.raises(DomainValidationError, match="cycle"):
        repository.set_selected_cycle("conv-a", cycle_id)  # type: ignore[arg-type]


def test_request_summary_and_trace_round_trip_as_validated_data(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.ensure_session("conv-a")
    repository.save_request_summary(
        request_id="request-1",
        conversation_id="conv-a",
        intent="sensor_query",
        summary={"cycle_ids": [1_200], "evidence_count": 1},
    )
    trace = TraceEvent(
        request_id="request-1",
        node="sensor_query",
        action="load_processed_cycle",
        status="success",
        duration_ms=2.5,
        evidence_count=1,
        parameter_summary={"cycle_id": 1_200},
    )
    repository.append_trace("conv-a", trace)

    summary = repository.get_request_summary("request-1")
    traces = repository.list_traces("request-1")

    assert summary is not None
    assert summary.conversation_id == "conv-a"
    assert summary.intent == "sensor_query"
    assert summary.summary == {"cycle_ids": [1_200], "evidence_count": 1}
    assert traces == (trace,)


def test_diagnosis_lookup_is_scoped_to_the_same_conversation(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.ensure_session("conv-a")
    repository.ensure_session("conv-b")
    first = _diagnosis("diag-1", "conv-a")
    second = _diagnosis("diag-2", "conv-a")
    repository.save_diagnosis(first)
    repository.save_diagnosis(second)

    assert repository.get_diagnosis("diag-1", conversation_id="conv-a") == first
    assert repository.get_diagnosis("diag-1", conversation_id="conv-b") is None
    assert repository.get_latest_diagnosis("conv-a") == second
    assert repository.get_latest_diagnosis("conv-b") is None

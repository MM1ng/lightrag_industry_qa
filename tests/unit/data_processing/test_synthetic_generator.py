from __future__ import annotations

import csv
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from industrial_energy_agent.data_processing import synthetic_generator
from industrial_energy_agent.data_processing.synthetic_generator import (
    GENERATOR_VERSION,
    SyntheticWorkOrderRecord,
    generate_synthetic_data,
)
from industrial_energy_agent.domain.models import WorkOrderDraft

EXPECTED_FILES = (
    "equipment_master.csv",
    "alarm_events.csv",
    "fault_cases.json",
    "work_orders.json",
)
EXPECTED_EQUIPMENT_IDS = {
    "PUMP-001",
    "PUMP-002",
    "VALVE-001",
    "COOLER-001",
    "ACC-001",
}
EXPECTED_WORK_ORDER_FIELDS = {
    "work_order_id",
    "request_id",
    "conversation_id",
    "diagnosis_id",
    "equipment",
    "symptom",
    "candidate_causes",
    "checks",
    "safety_items",
    "status",
    "approval_status",
    "executed",
    "created_at",
    "entity_id",
    "data_type",
    "generator_version",
    "seed",
    "provenance_note",
    "source_case_id",
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, list)
    assert all(isinstance(item, dict) for item in value)
    return value


def _artifact_bytes(output_dir: Path) -> dict[str, bytes]:
    return {name: (output_dir / name).read_bytes() for name in EXPECTED_FILES}


def test_fixed_seed_repeated_run_has_identical_result_and_bytes(tmp_path: Path) -> None:
    first = generate_synthetic_data(tmp_path, seed=20260721)
    first_bytes = _artifact_bytes(tmp_path)

    second = generate_synthetic_data(tmp_path, seed=20260721)

    assert second == first
    assert _artifact_bytes(tmp_path) == first_bytes
    assert first.seed == 20260721
    assert first.generator_version == GENERATOR_VERSION
    assert tuple(artifact.filename for artifact in first.artifacts) == EXPECTED_FILES


def test_only_five_agreed_assets_and_every_csv_row_has_provenance(tmp_path: Path) -> None:
    generate_synthetic_data(tmp_path, seed=20260721)

    equipment = _read_csv(tmp_path / "equipment_master.csv")
    alarms = _read_csv(tmp_path / "alarm_events.csv")

    assert {row["equipment_id"] for row in equipment} == EXPECTED_EQUIPMENT_IDS
    assert len(equipment) == len(EXPECTED_EQUIPMENT_IDS)
    assert {row["equipment_id"] for row in alarms} <= EXPECTED_EQUIPMENT_IDS
    assert [row["alarm_id"] for row in alarms] == [
        "ALARM-DEMO-001",
        "ALARM-DEMO-002",
        "ALARM-DEMO-003",
        "ALARM-DEMO-004",
        "ALARM-DEMO-005",
    ]
    for row in [*equipment, *alarms]:
        assert row["data_type"] == "synthetic_demo"
        assert row["generator_version"] == GENERATOR_VERSION
        assert row["seed"] == "20260721"
        assert row["entity_id"]


def test_json_entities_are_labeled_and_work_orders_can_never_execute(tmp_path: Path) -> None:
    generate_synthetic_data(tmp_path, seed=20260721)

    cases = _read_json(tmp_path / "fault_cases.json")
    orders = _read_json(tmp_path / "work_orders.json")
    case_ids = {case["case_id"] for case in cases}

    assert [case["case_id"] for case in cases] == [
        "CASE-DEMO-001",
        "CASE-DEMO-002",
        "CASE-DEMO-003",
    ]
    assert [order["work_order_id"] for order in orders] == [
        "WO-DEMO-001",
        "WO-DEMO-002",
    ]
    for entity in [*cases, *orders]:
        assert entity["data_type"] == "synthetic_demo"
        assert entity["generator_version"] == GENERATOR_VERSION
        assert entity["seed"] == 20260721
    validated_orders = [SyntheticWorkOrderRecord.model_validate(order) for order in orders]
    assert [order.created_at for order in validated_orders] == [
        datetime(2026, 7, 21, 9, tzinfo=UTC),
        datetime(2026, 7, 21, 10, tzinfo=UTC),
    ]
    for raw_order, order in zip(orders, validated_orders, strict=True):
        assert set(raw_order) == EXPECTED_WORK_ORDER_FIELDS
        assert order.equipment in EXPECTED_EQUIPMENT_IDS
        assert order.source_case_id in case_ids
        assert order.status == "DRAFT"
        assert order.approval_status == "PENDING_REVIEW"
        assert order.executed is False

        draft = WorkOrderDraft.model_validate(order.to_work_order_draft_payload())
        assert draft.work_order_id == order.work_order_id
        assert draft.executed is False


def test_synthetic_work_order_schema_forbids_legacy_or_unknown_fields(tmp_path: Path) -> None:
    generate_synthetic_data(tmp_path, seed=20260721)
    order = _read_json(tmp_path / "work_orders.json")[0]

    for legacy_field in (
        "equipment_id",
        "check_items",
        "review_status",
        "execution_allowed",
        "lifecycle_policy",
        "title",
    ):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            SyntheticWorkOrderRecord.model_validate({**order, legacy_field: "forbidden"})


def test_content_has_no_real_enterprise_claim_or_unsupported_probability(tmp_path: Path) -> None:
    generate_synthetic_data(tmp_path, seed=20260721)

    serialized = "\n".join(
        (tmp_path / filename).read_text(encoding="utf-8") for filename in EXPECTED_FILES
    )

    for prohibited in ("真实电厂", "真实企业", "某某电厂", "某某公司", "故障概率", "故障率"):
        assert prohibited not in serialized
    assert "probability" not in serialized.casefold()


def test_json_is_pretty_utf8_with_stable_key_order(tmp_path: Path) -> None:
    generate_synthetic_data(tmp_path, seed=20260721)

    for filename in ("fault_cases.json", "work_orders.json"):
        raw = (tmp_path / filename).read_bytes()
        text = raw.decode("utf-8", errors="strict")
        parsed = json.loads(text)
        expected = json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

        assert text == expected


def test_seed_changes_provenance_but_not_stable_entity_ids(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    generate_synthetic_data(first_dir, seed=1)
    generate_synthetic_data(second_dir, seed=2)

    assert (first_dir / "equipment_master.csv").read_bytes() != (
        second_dir / "equipment_master.csv"
    ).read_bytes()
    assert [row["entity_id"] for row in _read_csv(first_dir / "equipment_master.csv")] == [
        row["entity_id"] for row in _read_csv(second_dir / "equipment_master.csv")
    ]


def test_each_artifact_is_atomically_replaced_from_a_sibling_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def recording_replace(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        replacements.append((source_path, destination_path))
        real_replace(source, destination)

    monkeypatch.setattr(synthetic_generator.os, "replace", recording_replace)

    generate_synthetic_data(tmp_path, seed=20260721)

    assert [destination.name for _, destination in replacements] == list(EXPECTED_FILES)
    for source, destination in replacements:
        assert source.parent == destination.parent == tmp_path
        assert source.name.startswith(f".{destination.name}.")
        assert source.suffix == ".tmp"
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("protected_name", ["raw_dataset", "manuals"])
def test_generation_rejects_output_inside_protected_source_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    protected_name: str,
) -> None:
    raw_dataset = tmp_path / "data" / "raw_dataset"
    manuals = tmp_path / "data" / "manuals"
    raw_dataset.mkdir(parents=True)
    manuals.mkdir(parents=True)
    monkeypatch.setattr(
        synthetic_generator,
        "PROTECTED_OUTPUT_ROOTS",
        (raw_dataset, manuals),
        raising=False,
    )
    output_dir = tmp_path / "data" / protected_name / "generated"

    with pytest.raises(ValueError, match="protected source"):
        generate_synthetic_data(output_dir, seed=20260721)

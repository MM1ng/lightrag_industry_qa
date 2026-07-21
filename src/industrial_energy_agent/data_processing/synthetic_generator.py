"""Deterministic, explicitly labeled synthetic business-demo data."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

GENERATOR_VERSION: Final = "synthetic-demo-v1"
DATA_TYPE: Final = "synthetic_demo"
PROVENANCE_NOTE: Final = "仅用于功能演示; 非现场数据"
PROJECT_ROOT: Final = Path(__file__).resolve().parents[3]
PROTECTED_OUTPUT_ROOTS: Final = (
    PROJECT_ROOT / "data" / "raw_dataset",
    PROJECT_ROOT / "data" / "manuals",
)

_EQUIPMENT_IDS: Final = (
    "PUMP-001",
    "PUMP-002",
    "VALVE-001",
    "COOLER-001",
    "ACC-001",
)
_PROHIBITED_CONTENT: Final = (
    "真实电厂",
    "真实企业",
    "某某电厂",
    "某某公司",
    "故障概率",
    "故障率",
    "probability",
)
_WORK_ORDER_DRAFT_FIELDS: Final = {
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
}


class SyntheticWorkOrderRecord(BaseModel):
    """Synthetic provenance wrapped around the WorkOrderDraft-compatible core."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    work_order_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    diagnosis_id: str = Field(min_length=1, max_length=128)
    equipment: str = Field(min_length=1, max_length=500)
    symptom: str = Field(min_length=1, max_length=2000)
    candidate_causes: list[str] = Field(min_length=1)
    checks: list[str] = Field(min_length=1)
    safety_items: list[str] = Field(min_length=1)
    status: Literal["DRAFT"] = "DRAFT"
    approval_status: Literal["PENDING_REVIEW"] = "PENDING_REVIEW"
    executed: Literal[False] = False
    created_at: datetime

    entity_id: str = Field(min_length=1, max_length=128)
    data_type: Literal["synthetic_demo"] = DATA_TYPE
    generator_version: Literal["synthetic-demo-v1"] = GENERATOR_VERSION
    seed: int
    provenance_note: str = Field(min_length=1, max_length=500)
    source_case_id: str = Field(min_length=1, max_length=128)

    def to_work_order_draft_payload(self) -> dict[str, object]:
        """Project only fields accepted by the domain WorkOrderDraft contract."""

        return self.model_dump(include=_WORK_ORDER_DRAFT_FIELDS, mode="python")


@dataclass(frozen=True, slots=True)
class GeneratedArtifact:
    """Stable description of one generated artifact."""

    filename: str
    record_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """Deterministic result returned by a generation run."""

    seed: int
    generator_version: str
    artifacts: tuple[GeneratedArtifact, ...]


def _common(entity_id: str, seed: int) -> dict[str, object]:
    return {
        "entity_id": entity_id,
        "data_type": DATA_TYPE,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "provenance_note": PROVENANCE_NOTE,
    }


def _equipment_records(seed: int) -> tuple[dict[str, object], ...]:
    definitions = (
        ("PUMP-001", "演示循环泵一", "centrifugal_pump", "READY_FOR_DEMO"),
        ("PUMP-002", "演示循环泵二", "centrifugal_pump", "READY_FOR_DEMO"),
        ("VALVE-001", "演示调节阀", "control_valve", "READY_FOR_DEMO"),
        ("COOLER-001", "演示冷却器", "cooler", "READY_FOR_DEMO"),
        ("ACC-001", "演示蓄能器", "hydraulic_accumulator", "READY_FOR_DEMO"),
    )
    return tuple(
        {
            **_common(equipment_id, seed),
            "equipment_id": equipment_id,
            "equipment_name": name,
            "equipment_type": equipment_type,
            "demo_area": "DEMO-HYDRAULIC-LOOP",
            "demo_state": demo_state,
        }
        for equipment_id, name, equipment_type, demo_state in definitions
    )


def _alarm_records(seed: int) -> tuple[dict[str, object], ...]:
    definitions = (
        (
            "ALARM-DEMO-001",
            "PUMP-001",
            "LOW_DISCHARGE_PRESSURE",
            "WARNING",
            "演示现象: 出口压力偏低",
        ),
        (
            "ALARM-DEMO-002",
            "PUMP-002",
            "ELEVATED_VIBRATION",
            "WARNING",
            "演示现象: 振动偏高",
        ),
        (
            "ALARM-DEMO-003",
            "VALVE-001",
            "POSITION_RESPONSE_DELAY",
            "WARNING",
            "演示现象: 阀位响应延迟",
        ),
        (
            "ALARM-DEMO-004",
            "COOLER-001",
            "COOLING_PERFORMANCE_LOW",
            "WARNING",
            "演示现象: 冷却表现下降",
        ),
        (
            "ALARM-DEMO-005",
            "ACC-001",
            "ACCUMULATOR_PRESSURE_LOW",
            "WARNING",
            "演示现象: 蓄能器压力偏低",
        ),
    )
    return tuple(
        {
            **_common(alarm_id, seed),
            "alarm_id": alarm_id,
            "equipment_id": equipment_id,
            "alarm_code": alarm_code,
            "severity": severity,
            "observed_at": f"2026-07-21T{index:02d}:00:00Z",
            "status": "DEMO_OPEN",
            "description": description,
        }
        for index, (alarm_id, equipment_id, alarm_code, severity, description) in enumerate(
            definitions,
            start=8,
        )
    )


def _fault_case_records(seed: int) -> tuple[dict[str, object], ...]:
    return (
        {
            **_common("CASE-DEMO-001", seed),
            "case_id": "CASE-DEMO-001",
            "title": "泵出口压力与流量偏低演示案例",
            "applicable_equipment_ids": ["PUMP-001"],
            "symptoms": ["出口压力偏低", "流量偏低"],
            "candidate_causes": ["入口条件异常", "阀位异常", "泵内部磨损迹象"],
            "recommended_checks": ["核对隔离边界", "检查入口条件", "核对阀位反馈"],
            "safety_notes": ["仅允许只读检查", "任何现场处置必须由授权人员审批"],
            "evidence_scope": "synthetic_pattern_only",
        },
        {
            **_common("CASE-DEMO-002", seed),
            "case_id": "CASE-DEMO-002",
            "title": "泵振动与温度升高演示案例",
            "applicable_equipment_ids": ["PUMP-002", "COOLER-001"],
            "symptoms": ["振动偏高", "温度升高"],
            "candidate_causes": ["对中状态异常迹象", "轴承状态异常迹象", "冷却表现下降"],
            "recommended_checks": ["核对振动摘要", "核对温度趋势", "检查冷却回路状态"],
            "safety_notes": ["不得在线拆检", "需要现场确认隔离和泄压条件"],
            "evidence_scope": "synthetic_pattern_only",
        },
        {
            **_common("CASE-DEMO-003", seed),
            "case_id": "CASE-DEMO-003",
            "title": "辅助液压响应迟缓演示案例",
            "applicable_equipment_ids": ["VALVE-001", "ACC-001"],
            "symptoms": ["阀位响应延迟", "蓄能器压力偏低"],
            "candidate_causes": ["阀响应异常迹象", "蓄能器预充状态待核对"],
            "recommended_checks": ["核对阀位反馈", "核对蓄能器只读状态"],
            "safety_notes": ["禁止旁路联锁", "调整前必须执行正式风险审查"],
            "evidence_scope": "synthetic_pattern_only",
        },
    )


def _work_order_records(seed: int) -> tuple[dict[str, object], ...]:
    definitions = (
        SyntheticWorkOrderRecord(
            work_order_id="WO-DEMO-001",
            request_id="REQ-DEMO-WO-001",
            conversation_id="CONV-DEMO-001",
            diagnosis_id="DIAG-DEMO-001",
            equipment="PUMP-001",
            symptom="出口压力与流量偏低",
            candidate_causes=["入口条件异常", "阀位异常", "泵内部磨损迹象"],
            checks=["核对入口条件", "核对阀位反馈", "记录只读测量摘要"],
            safety_items=["确认隔离边界", "未经审批不得执行设备操作"],
            created_at=datetime(2026, 7, 21, 9, tzinfo=UTC),
            entity_id="WO-DEMO-001",
            seed=seed,
            provenance_note=PROVENANCE_NOTE,
            source_case_id="CASE-DEMO-001",
        ),
        SyntheticWorkOrderRecord(
            work_order_id="WO-DEMO-002",
            request_id="REQ-DEMO-WO-002",
            conversation_id="CONV-DEMO-002",
            diagnosis_id="DIAG-DEMO-002",
            equipment="PUMP-002",
            symptom="振动偏高且温度升高",
            candidate_causes=["对中状态异常迹象", "轴承状态异常迹象", "冷却表现下降"],
            checks=["核对振动摘要", "核对温度趋势", "检查冷却回路状态"],
            safety_items=["不得在线拆检", "现场处置前完成风险审查"],
            created_at=datetime(2026, 7, 21, 10, tzinfo=UTC),
            entity_id="WO-DEMO-002",
            seed=seed,
            provenance_note=PROVENANCE_NOTE,
            source_case_id="CASE-DEMO-002",
        ),
    )
    return tuple(record.model_dump(mode="json") for record in definitions)


def _csv_payload(
    fieldnames: tuple[str, ...],
    records: tuple[dict[str, object], ...],
) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=fieldnames,
        extrasaction="raise",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(records)
    return buffer.getvalue().encode("utf-8")


def _json_payload(records: tuple[dict[str, object], ...]) -> bytes:
    text = json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return text.encode("utf-8")


def _validate_records(
    equipment: tuple[dict[str, object], ...],
    alarms: tuple[dict[str, object], ...],
    cases: tuple[dict[str, object], ...],
    orders: tuple[dict[str, object], ...],
) -> None:
    if tuple(record["equipment_id"] for record in equipment) != _EQUIPMENT_IDS:
        raise ValueError("synthetic equipment set does not match the five agreed assets")
    all_records = (*equipment, *alarms, *cases, *orders)
    if any(record.get("data_type") != DATA_TYPE for record in all_records):
        raise ValueError("every synthetic business entity must carry data_type=synthetic_demo")
    for order in orders:
        SyntheticWorkOrderRecord.model_validate(order)
    serialized = json.dumps(all_records, ensure_ascii=False).casefold()
    if any(value.casefold() in serialized for value in _PROHIBITED_CONTENT):
        raise ValueError("synthetic data contains prohibited attribution or probability content")


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _validated_output_dir(output_dir: str | Path) -> Path:
    destination = Path(output_dir).resolve(strict=False)
    for protected_root in PROTECTED_OUTPUT_ROOTS:
        protected = protected_root.resolve(strict=False)
        if destination == protected or destination.is_relative_to(protected):
            raise ValueError("synthetic output directory is inside a protected source")
    return destination


def generate_synthetic_data(
    output_dir: str | Path,
    *,
    seed: int = 20260721,
) -> GenerationResult:
    """Generate four deterministic synthetic-demo artifacts.

    The seed is recorded as provenance. It is deliberately not used to create
    unsupported fault likelihoods or pseudo-measurements.
    """

    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer")

    equipment = _equipment_records(seed)
    alarms = _alarm_records(seed)
    cases = _fault_case_records(seed)
    orders = _work_order_records(seed)
    _validate_records(equipment, alarms, cases, orders)

    payloads = (
        (
            "equipment_master.csv",
            len(equipment),
            _csv_payload(
                (
                    "entity_id",
                    "equipment_id",
                    "equipment_name",
                    "equipment_type",
                    "demo_area",
                    "demo_state",
                    "data_type",
                    "generator_version",
                    "seed",
                    "provenance_note",
                ),
                equipment,
            ),
        ),
        (
            "alarm_events.csv",
            len(alarms),
            _csv_payload(
                (
                    "entity_id",
                    "alarm_id",
                    "equipment_id",
                    "alarm_code",
                    "severity",
                    "observed_at",
                    "status",
                    "description",
                    "data_type",
                    "generator_version",
                    "seed",
                    "provenance_note",
                ),
                alarms,
            ),
        ),
        ("fault_cases.json", len(cases), _json_payload(cases)),
        ("work_orders.json", len(orders), _json_payload(orders)),
    )

    destination = _validated_output_dir(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    artifacts: list[GeneratedArtifact] = []
    for filename, record_count, payload in payloads:
        _atomic_write(destination / filename, payload)
        artifacts.append(
            GeneratedArtifact(
                filename=filename,
                record_count=record_count,
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        )

    return GenerationResult(
        seed=seed,
        generator_version=GENERATOR_VERSION,
        artifacts=tuple(artifacts),
    )

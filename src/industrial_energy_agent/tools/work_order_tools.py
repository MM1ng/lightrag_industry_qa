"""Creation of persisted, review-only work-order drafts from diagnoses."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal, Protocol

from pydantic import Field, RootModel, ValidationError

from industrial_energy_agent.domain.models import DiagnosisRecord, TraceEvent, WorkOrderDraft
from industrial_energy_agent.persistence.work_order_repository import (
    WorkOrderConflictError,
    same_idempotent_payload,
)
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

_DEFAULT_SAFETY_ITEMS = (
    "由授权人员按现场规程复核隔离边界。",
    "执行任何现场操作前完成正式风险审查和审批。",
)


def _stable_work_order_id(*, conversation_id: str, diagnosis_id: str) -> str:
    identity = json.dumps(
        ["work-order-draft-v1", conversation_id, diagnosis_id],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return f"wo-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"


class DiagnosisRepositoryBoundary(Protocol):
    def get_diagnosis(
        self,
        diagnosis_id: str,
        *,
        conversation_id: str,
    ) -> DiagnosisRecord | None: ...


class WorkOrderRepositoryBoundary(Protocol):
    def create(self, draft: WorkOrderDraft) -> WorkOrderDraft: ...


class CreateWorkOrderDraftInput(ToolInputModel):
    diagnosis_id: str = Field(min_length=1, max_length=128)
    safety_items: list[Annotated[str, Field(min_length=1, max_length=500)]] | None = Field(
        default=None, max_length=20
    )


class CreateWorkOrderDraftSuccess(ToolModel):
    ok: Literal[True] = True
    work_order: WorkOrderDraft
    trace: TraceEvent


class CreateWorkOrderDraftFailure(ToolFailure):
    pass


class CreateWorkOrderDraftResult(
    RootModel[CreateWorkOrderDraftSuccess | CreateWorkOrderDraftFailure]
):
    pass


class CreateWorkOrderDraftService:
    def __init__(
        self,
        diagnoses: DiagnosisRepositoryBoundary,
        work_orders: WorkOrderRepositoryBoundary,
        *,
        conversation_id: str,
    ) -> None:
        if not conversation_id.strip():
            raise ValueError("conversation_id must be non-empty")
        self._diagnoses = diagnoses
        self._work_orders = work_orders
        self._conversation_id = conversation_id

    def execute(self, args: CreateWorkOrderDraftInput) -> CreateWorkOrderDraftResult:
        started = started_at()
        if not args.diagnosis_id.strip():
            return self._failure(
                args,
                started,
                code="INVALID_INPUT",
                message="诊断编号不能为空。",
            )
        if args.safety_items is not None and (
            not args.safety_items or any(not item.strip() for item in args.safety_items)
        ):
            return self._failure(
                args,
                started,
                code="INVALID_INPUT",
                message="安全事项必须为非空条目。",
            )
        try:
            diagnosis = self._diagnoses.get_diagnosis(
                args.diagnosis_id,
                conversation_id=self._conversation_id,
            )
        except Exception:
            return self._dependency_failure(args, started)
        if diagnosis is None or diagnosis.conversation_id != self._conversation_id:
            return self._failure(
                args,
                started,
                code="DIAGNOSIS_NOT_FOUND",
                message="未找到已持久化的诊断记录。",
            )
        if (
            not diagnosis.observed_anomalies
            or not diagnosis.candidate_causes
            or not diagnosis.recommended_checks
        ):
            return self._failure(
                args,
                started,
                code="DIAGNOSIS_INCOMPLETE",
                message="诊断信息不足, 无法生成工单草稿。",
            )
        try:
            draft = WorkOrderDraft(
                work_order_id=_stable_work_order_id(
                    conversation_id=self._conversation_id,
                    diagnosis_id=diagnosis.diagnosis_id,
                ),
                request_id=args.request_id,
                conversation_id=self._conversation_id,
                diagnosis_id=diagnosis.diagnosis_id,
                equipment=diagnosis.equipment,
                symptom="; ".join(diagnosis.observed_anomalies),
                candidate_causes=[cause.cause for cause in diagnosis.candidate_causes],
                checks=list(diagnosis.recommended_checks),
                safety_items=list(args.safety_items or _DEFAULT_SAFETY_ITEMS),
            )
        except ValidationError:
            return self._failure(
                args,
                started,
                code="DIAGNOSIS_INCOMPLETE",
                message="诊断信息无法生成有效的工单草稿。",
            )
        try:
            persisted = WorkOrderDraft.model_validate(self._work_orders.create(draft).model_dump())
            if not same_idempotent_payload(persisted, draft):
                raise ValueError("persisted work order differs from requested draft")
        except WorkOrderConflictError:
            return self._failure(
                args,
                started,
                code="WORK_ORDER_CONFLICT",
                message="相同请求身份已存在不同的工单草稿。",
            )
        except Exception:
            return self._dependency_failure(args, started)
        success = CreateWorkOrderDraftSuccess(
            work_order=persisted,
            trace=make_trace(
                request_id=args.request_id,
                tool="create_work_order_draft",
                started=started,
                status="success",
                evidence_count=1,
                parameter_summary={
                    "diagnosis_id_length": len(args.diagnosis_id),
                    "risk_level": diagnosis.risk_level.value,
                },
            ),
        )
        return CreateWorkOrderDraftResult(root=success)

    @staticmethod
    def _failure(
        args: CreateWorkOrderDraftInput,
        started: float,
        *,
        code: str,
        message: str,
    ) -> CreateWorkOrderDraftResult:
        failure = CreateWorkOrderDraftFailure(
            error=make_error(code, message),
            trace=make_trace(
                request_id=args.request_id,
                tool="create_work_order_draft",
                started=started,
                status="failure",
                parameter_summary={"diagnosis_id_length": len(args.diagnosis_id)},
                error_code=code,
            ),
        )
        return CreateWorkOrderDraftResult(root=failure)

    @classmethod
    def _dependency_failure(
        cls,
        args: CreateWorkOrderDraftInput,
        started: float,
    ) -> CreateWorkOrderDraftResult:
        code = "WORK_ORDER_DEPENDENCY_ERROR"
        failure = CreateWorkOrderDraftFailure(
            error=make_error(code, "工单草稿暂时无法持久化。", retryable=True),
            trace=make_trace(
                request_id=args.request_id,
                tool="create_work_order_draft",
                started=started,
                status="failure",
                parameter_summary={"diagnosis_id_length": len(args.diagnosis_id)},
                error_code=code,
            ),
        )
        return CreateWorkOrderDraftResult(root=failure)


def build_create_work_order_draft_tool(
    diagnoses: DiagnosisRepositoryBoundary,
    work_orders: WorkOrderRepositoryBoundary,
    *,
    conversation_id: str,
) -> SafeStructuredTool:
    service = CreateWorkOrderDraftService(
        diagnoses,
        work_orders,
        conversation_id=conversation_id,
    )

    def create_work_order_draft(
        diagnosis_id: str,
        safety_items: list[str] | None = None,
        request_id: str = "",
    ) -> dict[str, Any]:
        args = CreateWorkOrderDraftInput(
            diagnosis_id=diagnosis_id,
            safety_items=safety_items,
            request_id=request_id or new_request_id(),
        )
        return dump_result(service.execute(args))

    return build_safe_structured_tool(
        func=create_work_order_draft,
        name="create_work_order_draft",
        description="从已持久化诊断生成并保存待人工审阅、永不执行的工单草稿。",
        args_schema=CreateWorkOrderDraftInput,
    )

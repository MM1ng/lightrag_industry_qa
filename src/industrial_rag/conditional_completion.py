"""Pure, deterministic policy for bounded conditional evidence completion.

This module plans completion only.  It does not perform retrieval, call an
LLM, inspect PDFs, or mutate a generation registry.  The query service can
adopt the plan in a later phase without changing this policy's semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from industrial_rag.evidence_completion import ContextRecord


@dataclass(frozen=True, slots=True)
class CompletionCandidate:
    chunk_id: str
    relation: str
    reason: str
    record: ContextRecord


@dataclass(frozen=True, slots=True)
class ConditionalCompletionPlan:
    coverage_requirements: tuple[str, ...]
    before: tuple[str, ...]
    missing: tuple[str, ...]
    candidates: tuple[CompletionCandidate, ...]
    accepted: tuple[CompletionCandidate, ...]
    rejected: tuple[CompletionCandidate, ...]
    reasons: dict[str, Any]
    after: tuple[str, ...]

    @property
    def accepted_chunk_ids(self) -> tuple[str, ...]:
        return tuple(item.chunk_id for item in self.accepted)

    def to_dict(self) -> dict[str, Any]:
        return {
            "coverage_requirements": list(self.coverage_requirements),
            "before": list(self.before),
            "missing": list(self.missing),
            "candidates": [item.chunk_id for item in self.candidates],
            "accepted": [item.chunk_id for item in self.accepted],
            "rejected": [item.chunk_id for item in self.rejected],
            "reasons": self.reasons,
            "after": list(self.after),
        }


_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "parameter": ("object", "parameter", "value", "unit", "condition"),
    "procedure": ("precondition", "step", "warning"),
    "safety": ("restriction", "prohibition", "condition", "consequence"),
    "safety_warning": ("restriction", "prohibition", "condition", "consequence"),
    "troubleshooting": ("symptom", "cause", "handling"),
    "maintenance": ("object", "period", "condition"),
    "maintenance_interval": ("object", "period", "condition"),
    "multi_evidence": ("independent_answer_point",),
    "cross_page": ("independent_answer_point",),
    "table": ("object", "parameter", "value", "unit", "condition"),
}

_TOKENS: dict[str, tuple[str, ...]] = {
    "object": ("设备", "泵", "轴承", "对象", "系统", "组件", "equipment", "pump"),
    "parameter": ("参数", "压力", "温度", "流量", "转速", "名称", "parameter", "pressure", "temperature"),
    "value": ("数值", "值", "为", "达到", "范围", "value", "range"),
    "unit": ("单位", "°c", "℃", "mpa", "bar", "mm", "kw", "hz", "unit"),
    "condition": ("条件", "适用", "正常", "当", "如果", "condition", "provided"),
    "precondition": ("前置", "准备", "停机", "断电", "确认", "before", "precondition"),
    "step": ("步骤", "操作", "执行", "拆", "装", "安装", "step", "remove", "install"),
    "warning": ("警告", "注意", "禁止", "危险", "warning", "caution", "禁止"),
    "restriction": ("限制", "不得", "不能", "禁止", "restriction", "limit"),
    "prohibition": ("禁止", "不得", "严禁", "prohibited"),
    "consequence": ("后果", "导致", "否则", "损坏", "injury", "result"),
    "symptom": ("现象", "故障", "异常", "症状", "symptom", "fault"),
    "cause": ("原因", "由于", "因为", "cause", "because"),
    "handling": ("处理", "排除", "解决", "检查", "修复", "handling", "fix"),
    "period": ("周期", "每", "小时", "月", "年", "定期", "interval", "period"),
    "independent_answer_point": ("；", ";", "步骤", "以及", "同时", "and", "point"),
}


def _requirements(question_type: str, explicit: tuple[str, ...] | None) -> tuple[str, ...]:
    if explicit:
        return tuple(dict.fromkeys(explicit))
    return _REQUIREMENTS.get(question_type, ("independent_answer_point",))


def _covered(records: list[ContextRecord] | tuple[ContextRecord, ...], requirements: tuple[str, ...]) -> tuple[str, ...]:
    text = "\n".join(item.text.casefold() for item in records)
    return tuple(requirement for requirement in requirements if any(token.casefold() in text for token in _TOKENS.get(requirement, ())))


def plan_conditional_completion(
    question_type: str,
    selected: list[ContextRecord] | tuple[ContextRecord, ...],
    registry: dict[str, ContextRecord],
    *,
    is_negative: bool = False,
    coverage_requirements: tuple[str, ...] | None = None,
    max_completion: int = 2,
) -> ConditionalCompletionPlan:
    """Return a deterministic bounded completion plan without side effects."""
    requirements = _requirements(question_type, coverage_requirements)
    selected = tuple(selected)
    before = _covered(selected, requirements)
    missing = tuple(item for item in requirements if item not in before)
    if max_completion <= 0 or not missing:
        return ConditionalCompletionPlan(requirements, before, missing, (), (), (), {"complete": "no_gap"}, before)

    selected_ids = {item.chunk_id for item in selected}
    candidates: list[CompletionCandidate] = []
    rejected: list[CompletionCandidate] = []
    seen: set[str] = set()
    considered_order: list[str] = []

    def consider(chunk_id: str | None, relation: str, reason: str) -> CompletionCandidate | None:
        if not chunk_id or chunk_id in selected_ids or chunk_id in seen:
            return None
        seen.add(chunk_id)
        considered_order.append(chunk_id)
        candidate = registry.get(chunk_id)
        if candidate is None:
            return None
        source = selected[0] if selected else None
        if source is None or candidate.document_id != source.document_id or candidate.generation_id != source.generation_id:
            item = CompletionCandidate(chunk_id, relation, "identity_mismatch", candidate)
            rejected.append(item)
            return None
        item = CompletionCandidate(chunk_id, relation, reason, candidate)
        candidates.append(item)
        return item

    # Parent candidates are evaluated first and only accepted if they add a
    # currently missing requirement.
    parent_items: list[CompletionCandidate] = []
    for item in selected:
        candidate = consider(item.parent_chunk_id, "parent", "coverage_gap")
        if candidate:
            parent_items.append(candidate)
    accepted: list[CompletionCandidate] = []
    covered_records = list(selected)
    for candidate in parent_items:
        if len(accepted) >= max_completion:
            break
        improved = [name for name in missing if name in _covered((candidate.record,), requirements)]
        if improved:
            accepted.append(candidate)
            covered_records.append(candidate.record)
        else:
            rejected.append(CompletionCandidate(candidate.chunk_id, candidate.relation, "no_coverage_gain", candidate.record))

    after_parent = _covered(covered_records, requirements)
    remaining = tuple(name for name in requirements if name not in after_parent)

    # Adjacent context is considered only after parent evaluation.  It is
    # prohibited for negative questions and never recurses through added rows.
    adjacent_disabled = is_negative
    for item in selected:
        for relation, chunk_id in (("previous", item.previous_chunk_id), ("next", item.next_chunk_id)):
            if adjacent_disabled or len(accepted) >= max_completion:
                continue
            candidate = consider(chunk_id, "adjacent", "explicit_remaining_gap")
            if candidate is None:
                continue
            if not remaining:
                rejected.append(CompletionCandidate(candidate.chunk_id, candidate.relation, "not_needed_after_parent", candidate.record))
                continue
            improved = [name for name in remaining if name in _covered((candidate.record,), requirements)]
            if improved:
                accepted.append(candidate)
                covered_records.append(candidate.record)
                remaining = tuple(name for name in requirements if name not in _covered(covered_records, requirements))
            else:
                rejected.append(CompletionCandidate(candidate.chunk_id, candidate.relation, "unrelated", candidate.record))

    after = _covered(covered_records, requirements)
    adjacent_accepted = any(item.relation == "adjacent" for item in accepted)
    reasons: dict[str, Any] = {
        "candidate_order": considered_order,
        "adjacent": "adjacent_disabled" if adjacent_disabled else ("remaining_gap" if adjacent_accepted else "not_needed_after_parent"),
    }
    if is_negative:
        reasons["negative"] = "adjacent_disabled"
    return ConditionalCompletionPlan(requirements, before, tuple(name for name in requirements if name not in before), tuple(candidates), tuple(accepted), tuple(rejected), reasons, after)

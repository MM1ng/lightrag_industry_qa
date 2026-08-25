"""Bounded selection from an already-retrieved Top20 evidence window.

The policy neither retrieves nor reranks.  It only chooses a subset of the
caller-supplied Top20 and returns references in their original order, making
it safe to replay and audit independently from the query service.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_COVERAGE_TERMS: dict[str, tuple[str, ...]] = {
    "object": ("设备", "泵", "轴承", "系统", "组件", "equipment", "pump"),
    "parameter": ("参数", "压力", "温度", "流量", "转速", "parameter", "pressure", "temperature"),
    "value": ("数值", "值", "为", "达到", "范围", "value", "range"),
    "unit": ("单位", "°c", "℃", "mpa", "bar", "mm", "kw", "hz", "unit"),
    "condition": ("条件", "适用", "正常", "当", "如果", "condition", "provided"),
    "precondition": ("前置", "准备", "停机", "断电", "确认", "before", "precondition"),
    "step": ("步骤", "操作", "执行", "拆", "装", "安装", "step", "remove", "install"),
    "warning": ("警告", "注意", "禁止", "危险", "warning", "caution"),
    "restriction": ("限制", "不得", "不能", "禁止", "restriction", "limit"),
    "prohibition": ("禁止", "不得", "严禁", "prohibited"),
    "consequence": ("后果", "导致", "否则", "损坏", "injury", "result"),
}


@dataclass(frozen=True, slots=True)
class CoverageSelectionDecision:
    """An auditable no-rerank selection decision."""

    selected_chunk_ids: tuple[str, ...]
    considered_chunk_ids: tuple[str, ...]
    coverage_before: tuple[str, ...]
    coverage_after: tuple[str, ...]
    excluded_outside_top20: tuple[str, ...]
    max_evidence: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_chunk_ids": list(self.selected_chunk_ids),
            "considered_chunk_ids": list(self.considered_chunk_ids),
            "coverage_before": list(self.coverage_before),
            "coverage_after": list(self.coverage_after),
            "excluded_outside_top20": list(self.excluded_outside_top20),
            "max_evidence": self.max_evidence,
            "retrieval_performed": False,
            "rerank_performed": False,
        }


def _chunk_id(item: Mapping[str, Any]) -> str:
    return str(item.get("chunk_id") or "")


def _covered(items: Sequence[Mapping[str, Any]], requirements: tuple[str, ...]) -> tuple[str, ...]:
    text = "\n".join(str(item.get("text") or item.get("excerpt") or "").casefold() for item in items)
    return tuple(
        requirement
        for requirement in requirements
        if any(term.casefold() in text for term in _COVERAGE_TERMS.get(requirement, ()))
    )


def select_coverage_aware_evidence(
    top20_candidates: Sequence[Mapping[str, Any]],
    *,
    current_selection: Sequence[Mapping[str, Any]] = (),
    coverage_requirements: Sequence[str] = (),
    max_evidence: int = 5,
) -> CoverageSelectionDecision:
    """Select at most five unique items from the given Top20, without reranking.

    The first 20 supplied candidates are the hard boundary even if a caller
    accidentally passes more.  Output preserves that input order rather than
    promoting selected evidence, so original retrieval ranks remain intact.
    """
    if not 0 <= max_evidence <= 5:
        raise ValueError("max_evidence must be between 0 and 5")
    requirements = tuple(dict.fromkeys(str(item) for item in coverage_requirements if str(item)))
    window = tuple(top20_candidates[:20])
    outside = tuple(
        chunk_id for item in top20_candidates[20:]
        if (chunk_id := _chunk_id(item))
    )
    by_id = {_chunk_id(item): item for item in window if _chunk_id(item)}
    current_ids = tuple(dict.fromkeys(_chunk_id(item) for item in current_selection if _chunk_id(item)))
    selected_ids = [chunk_id for chunk_id in current_ids if chunk_id in by_id][:max_evidence]
    selected = [by_id[chunk_id] for chunk_id in selected_ids]
    before = _covered(selected, requirements)

    # Scan in existing rank order, retaining a candidate only when it covers a
    # currently missing requirement.  No scores or list order are changed.
    for item in window:
        if len(selected_ids) >= max_evidence:
            break
        chunk_id = _chunk_id(item)
        if not chunk_id or chunk_id in selected_ids:
            continue
        after_candidate = _covered((*selected, item), requirements)
        if len(after_candidate) > len(_covered(selected, requirements)):
            selected_ids.append(chunk_id)
            selected.append(item)

    selected_set = set(selected_ids)
    ordered_ids = tuple(_chunk_id(item) for item in window if _chunk_id(item) in selected_set)
    return CoverageSelectionDecision(
        selected_chunk_ids=ordered_ids,
        considered_chunk_ids=tuple(_chunk_id(item) for item in window if _chunk_id(item)),
        coverage_before=before,
        coverage_after=_covered(selected, requirements),
        excluded_outside_top20=outside,
        max_evidence=max_evidence,
    )


__all__ = ["CoverageSelectionDecision", "select_coverage_aware_evidence"]

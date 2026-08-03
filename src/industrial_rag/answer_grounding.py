"""Deterministic answer-point grounding and citation validation."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from industrial_rag.citation_formatter import Citation
from industrial_rag.evidence_policy import EvidenceCandidate, _tokens

GroundingStatus = Literal["success", "partial_answer", "insufficient_evidence", "safety_blocked"]

_SPLIT = re.compile(r"\n+|(?<=[。！？!?])")
_NUMBER = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?%?(?:\s*[°℃℃CF]|\s*(?:mm|cm|m|kPa|MPa|bar|rpm|Hz|秒|分钟|小时|天|周|月|年|N·m))?", re.I)
_UNIT = re.compile(r"(?:°C|℃|°F|mm|cm|m|kPa|MPa|bar|rpm|Hz|秒|分钟|小时|天|周|月|年|%)", re.I)
_GENERIC = frozenset(["根据", "手册", "内容", "如下", "需要", "应当", "可以", "进行", "相关", "要求", "说明", "建议"])


@dataclass(frozen=True, slots=True)
class AnswerPoint:
    point_id: str
    content: str
    evidence_ids: tuple[str, ...]
    support_status: Literal["supported", "unsupported"]

    def to_payload(self) -> dict[str, object]:
        return {
            "point_id": self.point_id,
            "content": self.content,
            "evidence_ids": list(self.evidence_ids),
            "support_status": self.support_status,
        }


@dataclass(frozen=True, slots=True)
class GroundedAnswer:
    answer: str
    citations: tuple[Citation, ...]
    answer_points: tuple[AnswerPoint, ...]
    status: GroundingStatus
    failure_categories: tuple[str, ...] = ()


def classify_question_type(question: str) -> str:
    text = question.casefold()
    if any(term in text for term in ("警告", "危险", "安全", "禁止", "防止")):
        return "safety"
    if any(term in text for term in ("步骤", "如何", "怎么", "操作", "安装", "拆卸")):
        return "procedure"
    if any(term in text for term in ("原因", "故障", "异常", "排除", "诊断")):
        return "troubleshooting"
    if any(term in text for term in ("多久", "周期", "频率", "维护", "保养", "存放")):
        return "maintenance"
    if any(term in text for term in ("型号", "部件", "组件", "哪个")):
        return "component"
    if any(term in text for term in ("条件", "上限", "下限", "最高", "最低", "温度", "压力")):
        return "condition_limit"
    if any(term in text for term in ("参数", "多少", "数值", "尺寸", "流量", "转速")):
        return "parameter"
    return "multi_evidence" if any(term in text for term in ("以及", "同时", "分别", "和")) else "parameter"


def _claim_tokens(text: str) -> set[str]:
    return {token for token in _tokens(text) if token not in _GENERIC}


def _has_numeric_support(claim: str, evidence: str) -> bool:
    numbers = _NUMBER.findall(claim)
    if not numbers:
        return True
    evidence_folded = evidence.casefold().replace(" ", "")
    for number in numbers:
        if number.casefold().replace(" ", "") not in evidence_folded:
            return False
    claim_has_unit = bool(_UNIT.search(claim))
    return not claim_has_unit or bool(_UNIT.search(evidence))


def _supports(claim: str, evidence: str) -> bool:
    claim_tokens = _claim_tokens(claim)
    evidence_tokens = _claim_tokens(evidence)
    if not claim_tokens or len(claim_tokens & evidence_tokens) < min(2, len(claim_tokens)):
        return False
    return _has_numeric_support(claim, evidence)


def build_answer_plan(
    answer: str,
    selected: Sequence[EvidenceCandidate],
    citations: Sequence[Citation],
) -> GroundedAnswer:
    candidates = tuple(selected)
    evidence_ids = {candidate.citation.chunk_id: f"E{index}" for index, candidate in enumerate(candidates, 1)}
    citation_by_chunk = {citation.chunk_id: citation for citation in citations}
    fragments = [fragment.strip(" -•\t") for fragment in _SPLIT.split(answer) if fragment.strip(" -•\t")]
    points: list[AnswerPoint] = []
    supported_citations: list[Citation] = []
    unsupported_categories: list[str] = []
    for index, fragment in enumerate(fragments, 1):
        supporting = tuple(
            evidence_ids[candidate.citation.chunk_id]
            for candidate in candidates
            if _supports(fragment, candidate.text)
        )
        status = "supported" if supporting else "unsupported"
        points.append(AnswerPoint(f"P{index}", fragment, supporting, status))
        if supporting:
            for candidate in candidates:
                if evidence_ids[candidate.citation.chunk_id] in supporting:
                    citation = citation_by_chunk.get(candidate.citation.chunk_id)
                    if citation and citation not in supported_citations:
                        supported_citations.append(citation)
        else:
            unsupported_categories.append("unsupported_generation_claim")
    supported_points = tuple(point for point in points if point.support_status == "supported")
    if not supported_points:
        return GroundedAnswer("手册中未检索到充分依据，无法可靠回答该问题。", (), tuple(points), "insufficient_evidence", tuple(unsupported_categories))
    if len(supported_points) != len(points):
        return GroundedAnswer(
            "\n".join(point.content for point in supported_points),
            tuple(supported_citations),
            tuple(points),
            "partial_answer",
            tuple(unsupported_categories),
        )
    return GroundedAnswer(answer, tuple(supported_citations), tuple(points), "success")

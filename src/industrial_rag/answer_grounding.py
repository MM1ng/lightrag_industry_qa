"""Deterministic answer-point grounding and citation validation."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from industrial_rag.citation_formatter import Citation
from industrial_rag.evidence_policy import EvidenceCandidate, _tokens
from industrial_rag.query_normalization import _replace_aliases

GroundingStatus = Literal["success", "partial_answer", "insufficient_evidence", "safety_blocked"]

_SPLIT = re.compile(r"\n+|(?<=[。！？!?])")
_NUMBER = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?(?:\s*(?:%|°C|℃|°F|°|mm|cm|m|kPa|MPa|bar|rpm|Hz|秒|分钟|小时|天|周|月|年|N·m))?", re.I)
_UNIT = re.compile(r"(?<![A-Za-z])(?:°C|℃|°F|mm|cm|m|kPa|MPa|bar|rpm|Hz|秒|分钟|小时|天|周|月|年|%)(?![A-Za-z])", re.I)
_PROVENANCE = re.compile(r"[（(][^）)]*(?:依据|证据)来源：[^）)]*[）)]")
_GENERIC = frozenset(["根据", "手册", "内容", "如下", "需要", "应当", "可以", "进行", "相关", "要求", "说明", "建议"])
_REFUSAL_PREFIX = "手册中未检索到充分依据"
_AUDIT_MAX_ANSWER_CHARS = 16_000
_SECRET_PATTERN = re.compile(r"(?i)(?:bearer\s+|api[_-]?key\s*[:=]\s*|sk-[A-Za-z0-9_-]{8,})\S+")


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
    grounding_audit: GroundingAudit | None = None


@dataclass(frozen=True, slots=True)
class GroundingAudit:
    """Admin-only capture of the answer before deterministic grounding."""

    audit_version: str
    generation_invoked: bool
    generation_empty: bool
    generation_returned_refusal: bool
    pre_grounding_answer: str
    pre_grounding_answer_sha256: str | None
    pre_grounding_answer_truncated: bool
    pre_grounding_answer_redacted: bool
    replay_eligible: bool
    replay_ineligible_reason: str | None
    input_fragments: tuple[dict[str, Any], ...]
    point_decisions: tuple[dict[str, Any], ...]
    removed_answer_points: tuple[dict[str, Any], ...]
    retained_answer_points: tuple[dict[str, Any], ...]
    grounding_output_answer: str
    grounding_output_status: GroundingStatus
    grounding_failure_categories: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "audit_version": self.audit_version,
            "generation_invoked": self.generation_invoked,
            "generation_empty": self.generation_empty,
            "generation_returned_refusal": self.generation_returned_refusal,
            "pre_grounding_answer": self.pre_grounding_answer,
            "pre_grounding_answer_sha256": self.pre_grounding_answer_sha256,
            "pre_grounding_answer_truncated": self.pre_grounding_answer_truncated,
            "pre_grounding_answer_redacted": self.pre_grounding_answer_redacted,
            "replay_eligible": self.replay_eligible,
            "replay_ineligible_reason": self.replay_ineligible_reason,
            "input_fragments": list(self.input_fragments),
            "point_decisions": list(self.point_decisions),
            "removed_answer_points": list(self.removed_answer_points),
            "retained_answer_points": list(self.retained_answer_points),
            "grounding_output_answer": self.grounding_output_answer,
            "grounding_output_status": self.grounding_output_status,
            "grounding_failure_categories": list(self.grounding_failure_categories),
        }


def _capture_answer(answer: str) -> tuple[str, str | None, bool, bool, str | None]:
    redacted_answer = _SECRET_PATTERN.sub("[REDACTED]", answer)
    redacted = redacted_answer != answer
    truncated = len(redacted_answer) > _AUDIT_MAX_ANSWER_CHARS
    captured = redacted_answer[:_AUDIT_MAX_ANSWER_CHARS]
    reason = "answer_truncated" if truncated else ("answer_redacted" if redacted else None)
    digest = hashlib.sha256(captured.encode("utf-8")).hexdigest() if captured else None
    return captured, digest, truncated, redacted, reason


def build_non_generation_audit(
    *,
    answer: str = "",
    generation_invoked: bool = False,
    output_status: GroundingStatus = "insufficient_evidence",
    failure_categories: Sequence[str] = (),
) -> GroundingAudit:
    captured, digest, truncated, redacted, reason = _capture_answer(answer)
    refusal = bool(captured.strip().startswith(_REFUSAL_PREFIX))
    if not generation_invoked:
        reason = reason or "generation_not_invoked"
    elif not captured:
        reason = reason or "generation_empty"
    elif refusal:
        reason = reason or "generation_returned_refusal"
    return GroundingAudit(
        audit_version="phase10b3f-grounding-audit-v1",
        generation_invoked=generation_invoked,
        generation_empty=not bool(answer.strip()),
        generation_returned_refusal=refusal,
        pre_grounding_answer=captured,
        pre_grounding_answer_sha256=digest,
        pre_grounding_answer_truncated=truncated,
        pre_grounding_answer_redacted=redacted,
        replay_eligible=False,
        replay_ineligible_reason=reason,
        input_fragments=(),
        point_decisions=(),
        removed_answer_points=(),
        retained_answer_points=(),
        grounding_output_answer=answer,
        grounding_output_status=output_status,
        grounding_failure_categories=tuple(failure_categories),
    )


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
    normalized, _ = _replace_aliases(text)
    return {token for token in _tokens(_PROVENANCE.sub("", normalized)) if token not in _GENERIC}


def _has_numeric_support(claim: str, evidence: str) -> bool:
    numbers = _NUMBER.findall(_PROVENANCE.sub("", claim))
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
    captured, digest, truncated, redacted, capture_reason = _capture_answer(answer)
    input_fragments = tuple(
        {"fragment_id": f"F{index}", "text": fragment, "fragment_order": index, "split_reason": "newline_or_sentence"}
        for index, fragment in enumerate(fragments, 1)
    )
    points: list[AnswerPoint] = []
    point_decisions: list[dict[str, Any]] = []
    removed_points: list[dict[str, Any]] = []
    retained_points: list[dict[str, Any]] = []
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
        decision = {
            "point_id": f"P{index}",
            "fragment_id": f"F{index}",
            "support_status": status,
            "evidence_ids": list(supporting),
            "candidate_chunk_ids": [candidate.citation.chunk_id for candidate in candidates],
            "support_reason_codes": ["deterministic_token_and_numeric_match"] if supporting else ["no_candidate_satisfied_support_gate"],
            "numeric_values": _NUMBER.findall(fragment),
            "unit_values": _UNIT.findall(fragment),
            "object_terms": sorted(_claim_tokens(fragment)),
            "condition_terms": [],
        }
        point_decisions.append(decision)
        if supporting:
            retained_points.append({"point_id": f"P{index}", "text": fragment, "evidence_ids": list(supporting)})
        else:
            removed_points.append({"point_id": f"P{index}", "text": fragment, "removal_reason": "unsupported_generation_claim", "attempted_evidence_ids": list(evidence_ids.values())})
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
        output = _REFUSAL_PREFIX + "，无法可靠回答该问题。"
        audit = GroundingAudit(
            audit_version="phase10b3f-grounding-audit-v1", generation_invoked=True,
            generation_empty=not bool(answer.strip()), generation_returned_refusal=answer.strip().startswith(_REFUSAL_PREFIX),
            pre_grounding_answer=captured, pre_grounding_answer_sha256=digest,
            pre_grounding_answer_truncated=truncated, pre_grounding_answer_redacted=redacted,
            replay_eligible=not (truncated or redacted or answer.strip().startswith(_REFUSAL_PREFIX) or not answer.strip()),
            replay_ineligible_reason=capture_reason or ("generation_returned_refusal" if answer.strip().startswith(_REFUSAL_PREFIX) else None),
            input_fragments=input_fragments, point_decisions=tuple(point_decisions),
            removed_answer_points=tuple(removed_points), retained_answer_points=tuple(retained_points),
            grounding_output_answer=output, grounding_output_status="insufficient_evidence",
            grounding_failure_categories=tuple(unsupported_categories),
        )
        return GroundedAnswer(output, (), tuple(points), "insufficient_evidence", tuple(unsupported_categories), audit)
    if len(supported_points) != len(points):
        output = "\n".join(point.content for point in supported_points)
        audit = GroundingAudit(
            audit_version="phase10b3f-grounding-audit-v1", generation_invoked=True,
            generation_empty=False, generation_returned_refusal=False,
            pre_grounding_answer=captured, pre_grounding_answer_sha256=digest,
            pre_grounding_answer_truncated=truncated, pre_grounding_answer_redacted=redacted,
            replay_eligible=not (truncated or redacted), replay_ineligible_reason=capture_reason,
            input_fragments=input_fragments, point_decisions=tuple(point_decisions),
            removed_answer_points=tuple(removed_points), retained_answer_points=tuple(retained_points),
            grounding_output_answer=output, grounding_output_status="partial_answer",
            grounding_failure_categories=tuple(unsupported_categories),
        )
        return GroundedAnswer(
            output,
            tuple(supported_citations),
            tuple(points),
            "partial_answer",
            tuple(unsupported_categories),
            audit,
        )
    audit = GroundingAudit(
        audit_version="phase10b3f-grounding-audit-v1", generation_invoked=True,
        generation_empty=False, generation_returned_refusal=False,
        pre_grounding_answer=captured, pre_grounding_answer_sha256=digest,
        pre_grounding_answer_truncated=truncated, pre_grounding_answer_redacted=redacted,
        replay_eligible=not (truncated or redacted), replay_ineligible_reason=capture_reason,
        input_fragments=input_fragments, point_decisions=tuple(point_decisions),
        removed_answer_points=(), retained_answer_points=tuple(retained_points),
        grounding_output_answer=answer, grounding_output_status="success",
        grounding_failure_categories=(),
    )
    return GroundedAnswer(answer, tuple(supported_citations), tuple(points), "success", (), audit)

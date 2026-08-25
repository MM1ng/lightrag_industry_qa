"""Runtime-only Claim -> Evidence semantic judgement primitives.

This module is deliberately independent from evaluation artifacts and the
online query path.  It only normalizes runtime claim/evidence fields, parses
the batch judge response, and applies the conservative ``supported`` policy.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class SemanticSupport(StrEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    NOT_SUPPORTED = "not_supported"
    UNCERTAIN = "uncertain"


_FORBIDDEN_KEYS = frozenset(
    {
        "supporting_actual_chunk_ids",
        "expected_support_chunk_ids",
        "expected_evidence",
        "expected_answer_point",
        "golden",
        "validation",
        "holdout",
        "oracle",
        "evaluation",
        "manual_supporting_label",
    }
)


@dataclass(frozen=True, slots=True)
class ParsedBatchJudgement:
    valid: bool
    judgements: dict[tuple[str, str], SemanticSupport]
    error: str | None = None


def _reject_evaluation_fields(value: Mapping[str, Any]) -> None:
    for key in value:
        normalized = str(key).casefold()
        if normalized in _FORBIDDEN_KEYS or any(
            marker in normalized for marker in ("golden", "holdout", "validation")
        ):
            raise ValueError("evaluation label is not allowed in runtime judge input")


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _runtime_excerpt(evidence: Mapping[str, Any]) -> str:
    return _text(evidence.get("excerpt") or evidence.get("content_excerpt") or evidence.get("text"))


def build_batch_judge_input(
    *,
    claims: Sequence[Mapping[str, Any]],
    candidate_evidence: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Build an allowlisted batch payload from fields available at runtime.

    The function fails closed when candidate evidence has no text.  Metadata
    alone is not enough for a semantic support decision, and silently judging
    from page/chunk identifiers would create a misleading experiment.
    """

    normalized_claims: list[dict[str, Any]] = []
    for claim in claims:
        _reject_evaluation_fields(claim)
        claim_id = _text(claim.get("claim_id"))
        claim_text = _text(claim.get("text") or claim.get("claim_text"))
        if not claim_id or not claim_text:
            raise ValueError("runtime claim requires claim_id and text")
        normalized_claims.append({"claim_id": claim_id, "claim_text": claim_text})

    normalized_evidence: list[dict[str, Any]] = []
    for evidence in candidate_evidence:
        _reject_evaluation_fields(evidence)
        evidence_id = _text(evidence.get("evidence_id"))
        excerpt = _runtime_excerpt(evidence)
        if not evidence_id:
            raise ValueError("runtime evidence requires evidence_id")
        if not excerpt:
            raise ValueError("runtime evidence text is required for semantic judgement")
        normalized_evidence.append(
            {
                "evidence_id": evidence_id,
                "document_name": _text(evidence.get("document_name")),
                "page": evidence.get("page"),
                "chunk_id": _text(evidence.get("chunk_id")),
                "excerpt": excerpt,
            }
        )
    return {"claims": normalized_claims, "evidence": normalized_evidence}


def build_semantic_judge_prompt(payload: Mapping[str, Any]) -> str:
    """Create the single-call prompt used by the offline replay runner."""

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (
        "判断 Evidence 是否支持 Claim，而不是判断二者是否与 Question 相关。\n"
        "禁止因为关键词重合就判 supported；数值、单位、条件、否定和时间范围必须一致。\n"
        "Claim 包含多个事实而 Evidence 只覆盖部分时判 partially_supported；冲突时判 not_supported。\n"
        "不得使用模型自身知识补全 Evidence 中没有的信息，只能依据给定 Evidence。\n"
        "请只返回 JSON：{\"claims\":[{\"claim_id\":\"...\",\"evidence\":[{\"evidence_id\":\"...\",\"support\":\"supported|partially_supported|not_supported|uncertain\"}]}]}\n"
        f"输入：{body}"
    )


def _uncertain_pairs(
    claim_ids: Sequence[str], evidence_ids: Sequence[str]
) -> dict[tuple[str, str], SemanticSupport]:
    return {
        (str(claim_id), str(evidence_id)): SemanticSupport.UNCERTAIN
        for claim_id in claim_ids
        for evidence_id in evidence_ids
    }


def parse_batch_judgement(
    raw: str,
    *,
    claim_ids: Sequence[str],
    evidence_ids: Sequence[str],
) -> ParsedBatchJudgement:
    """Parse a batch response and fail closed to ``uncertain`` on any error."""

    fallback = _uncertain_pairs(claim_ids, evidence_ids)
    try:
        decoded = json.loads(raw)
        if not isinstance(decoded, Mapping) or not isinstance(decoded.get("claims"), list):
            raise ValueError("judge response must contain a claims list")
        allowed_claims = {str(item) for item in claim_ids}
        allowed_evidence = {str(item) for item in evidence_ids}
        parsed: dict[tuple[str, str], SemanticSupport] = {}
        for claim in decoded["claims"]:
            if not isinstance(claim, Mapping):
                raise ValueError("judge claim entry must be an object")
            claim_id = _text(claim.get("claim_id"))
            if claim_id not in allowed_claims or not isinstance(claim.get("evidence"), list):
                raise ValueError("judge returned an unknown claim or invalid evidence list")
            for item in claim["evidence"]:
                if not isinstance(item, Mapping):
                    raise ValueError("judge evidence entry must be an object")
                evidence_id = _text(item.get("evidence_id"))
                support = _text(item.get("support"))
                if evidence_id not in allowed_evidence or support not in {
                    member.value for member in SemanticSupport
                }:
                    raise ValueError("judge returned an unknown evidence or support label")
                parsed[(claim_id, evidence_id)] = SemanticSupport(support)
        if set(parsed) != set(fallback):
            raise ValueError("judge response does not cover the complete claim/evidence matrix")
        return ParsedBatchJudgement(valid=True, judgements=parsed)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        return ParsedBatchJudgement(valid=False, judgements=fallback, error=str(error))


def select_supported_evidence(
    judgements: Mapping[tuple[str, str], SemanticSupport],
) -> dict[str, tuple[str, ...]]:
    """Apply the first-round conservative policy: only ``supported`` cites."""

    selected: dict[str, list[str]] = {}
    for (claim_id, evidence_id), support in judgements.items():
        if support is SemanticSupport.SUPPORTED:
            selected.setdefault(str(claim_id), []).append(str(evidence_id))
    return {claim_id: tuple(ids) for claim_id, ids in selected.items()}


__all__ = [
    "ParsedBatchJudgement",
    "SemanticSupport",
    "build_batch_judge_input",
    "build_semantic_judge_prompt",
    "parse_batch_judgement",
    "select_supported_evidence",
]

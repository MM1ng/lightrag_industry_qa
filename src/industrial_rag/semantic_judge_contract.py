"""Strict compact output contract for the Phase 12B-3A-R2 offline Judge."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .semantic_judge import SemanticSupport

_SET_NAMES = (
    "supported",
    "partially_supported",
    "not_supported",
    "uncertain",
)
_ALLOWED_CLAIM_KEYS = frozenset(("claim_id", *_SET_NAMES))


@dataclass(frozen=True, slots=True)
class ContractParseResult:
    valid: bool
    judgements: dict[tuple[str, str], SemanticSupport]
    error: str | None = None
    subtypes: tuple[str, ...] = ()
    returned_pair_count: int = 0


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _fallback(claim_ids: Sequence[str], evidence_ids: Sequence[str]) -> dict[tuple[str, str], SemanticSupport]:
    return {
        (str(claim_id), str(evidence_id)): SemanticSupport.UNCERTAIN
        for claim_id in claim_ids
        for evidence_id in evidence_ids
    }


def _invalid(
    subtype: str,
    error: str,
    fallback: dict[tuple[str, str], SemanticSupport],
    returned_pair_count: int = 0,
) -> ContractParseResult:
    return ContractParseResult(
        valid=False,
        judgements=fallback,
        error=error,
        subtypes=(subtype,),
        returned_pair_count=returned_pair_count,
    )


def build_compact_semantic_judge_prompt(payload: Mapping[str, Any]) -> str:
    """Keep R1 semantic instructions and change only response serialization."""

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (
        "判断 Evidence 是否支持 Claim，而不是判断二者是否与 Question 相关。\n"
        "禁止因为关键词重合就判 supported；数值、单位、条件、否定和时间范围必须一致。\n"
        "Claim 包含多个事实而 Evidence 只覆盖部分时判 partially_supported；冲突时判 not_supported。\n"
        "不得使用模型自身知识补全 Evidence 中没有的信息，只能依据给定 Evidence。\n"
        "请只返回 JSON，不要返回解释或 Markdown。格式必须是："
        '{"claims":[{"claim_id":"...","supported":["e1"],'
        '"partially_supported":["e2"],"not_supported":[],"uncertain":[]}]}.\n'
        "每个 claim 必须出现一次；每个 candidate evidence_id 必须恰好出现在四个集合之一。\n"
        f"输入：{body}"
    )


def parse_compact_batch_judgement(
    raw: str,
    *,
    claim_ids: Sequence[str],
    evidence_ids: Sequence[str],
) -> ContractParseResult:
    """Validate a complete compact matrix without silently filling omissions."""

    fallback = _fallback(claim_ids, evidence_ids)
    if not isinstance(raw, str) or not raw.strip():
        return _invalid("empty_response", "judge response is empty", fallback)
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as error:
        return _invalid("invalid_json", str(error), fallback)
    if not isinstance(decoded, Mapping) or set(decoded) != {"claims"} or not isinstance(decoded.get("claims"), list):
        return _invalid("other", "judge response must contain only a claims list", fallback)

    allowed_claims = {str(item) for item in claim_ids}
    allowed_evidence = {str(item) for item in evidence_ids}
    seen_claims: set[str] = set()
    parsed: dict[tuple[str, str], SemanticSupport] = {}
    returned_pair_count = 0

    for claim in decoded["claims"]:
        if not isinstance(claim, Mapping):
            return _invalid("other", "claim entry must be an object", fallback, returned_pair_count)
        unknown_keys = set(claim) - _ALLOWED_CLAIM_KEYS
        if unknown_keys:
            subtype = "invalid_support_enum" if any("support" in str(key) for key in unknown_keys) else "other"
            return _invalid(subtype, "claim entry contains unknown fields", fallback, returned_pair_count)
        claim_id = _text(claim.get("claim_id"))
        if not claim_id or claim_id not in allowed_claims:
            return _invalid("unknown_claim_id", "judge returned an unknown claim_id", fallback, returned_pair_count)
        if claim_id in seen_claims:
            return _invalid("duplicate_claim", "judge returned a duplicate claim_id", fallback, returned_pair_count)
        seen_claims.add(claim_id)
        if any(name not in claim for name in _SET_NAMES):
            return _invalid("missing_evidence", "claim does not contain all four evidence sets", fallback, returned_pair_count)
        if any(not isinstance(claim[name], list) for name in _SET_NAMES):
            return _invalid("other", "evidence sets must be lists", fallback, returned_pair_count)

        local_seen: set[str] = set()
        for set_name in _SET_NAMES:
            support = SemanticSupport(set_name)
            for evidence_id_value in claim[set_name]:
                evidence_id = _text(evidence_id_value)
                if not evidence_id or evidence_id not in allowed_evidence:
                    return _invalid("unknown_evidence_id", "judge returned an unknown evidence_id", fallback, returned_pair_count)
                if evidence_id in local_seen:
                    return _invalid("duplicate_evidence", "evidence_id occurs in multiple sets", fallback, returned_pair_count)
                local_seen.add(evidence_id)
                parsed[(claim_id, evidence_id)] = support
                returned_pair_count += 1
        if local_seen != allowed_evidence:
            return _invalid("missing_evidence", "claim does not cover every candidate evidence_id", fallback, returned_pair_count)

    if seen_claims != allowed_claims:
        return _invalid("missing_claim", "judge response does not contain every claim_id", fallback, returned_pair_count)
    if set(parsed) != set(fallback):
        return _invalid("missing_evidence", "judge response does not cover the complete matrix", fallback, returned_pair_count)
    return ContractParseResult(True, parsed, returned_pair_count=returned_pair_count)


__all__ = [
    "ContractParseResult",
    "build_compact_semantic_judge_prompt",
    "parse_compact_batch_judgement",
]

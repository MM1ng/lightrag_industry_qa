"""Deterministic, bounded policy for one supplemental retrieval attempt.

This module is intentionally side-effect free.  It does not change the normal
LightRAG ranking, query limits, chunking or generation.  A caller may provide a
retriever, but the policy decides whether it is allowed to call it and filters
every returned identity before it can become evidence.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

SUPPLEMENTAL_TOP_K = 5
MAX_FINAL_EVIDENCE = 5

_NEGATIVE_TERMS = ("是否存在", "有没有", "有无", "不存在", "未提及", "没有说明", "not", "no evidence")
_TYPE_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("procedure", ("步骤", "操作", "启动", "安装", "拆卸", "如何")),
    ("safety", ("安全", "禁止", "不得", "危险", "警告")),
    ("troubleshooting", ("故障", "异常", "原因", "排除", "处理")),
    ("maintenance", ("维护", "保养", "周期", "润滑")),
    ("parameter", ("参数", "温度", "压力", "流量", "转速", "间隙", "单位")),
)
_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "procedure": ("precondition", "step", "warning"),
    "safety": ("restriction", "prohibition", "condition", "consequence"),
    "troubleshooting": ("symptom", "cause", "handling"),
    "maintenance": ("object", "period", "condition"),
    "parameter": ("object", "parameter", "value", "unit", "condition"),
}


@dataclass(frozen=True, slots=True)
class SupplementalQuery:
    question: str
    knowledge_base_id: str
    generation_id: str
    coverage_gap: tuple[str, ...]
    top_k: int = SUPPLEMENTAL_TOP_K
    attempt: int = 1


@dataclass(frozen=True, slots=True)
class SupplementalCandidate:
    chunk_id: str
    document_id: str | None
    generation_id: str | None
    knowledge_base_id: str | None
    rank: int | None
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SupplementalRetrievalResult:
    triggered: bool
    reason: str
    supplemental_query: SupplementalQuery | None
    coverage_requirements: tuple[str, ...]
    coverage_gap: tuple[str, ...]
    coverage_before: tuple[str, ...]
    coverage_after: tuple[str, ...]
    retrieved: tuple[Mapping[str, Any], ...] = ()
    accepted: tuple[Mapping[str, Any], ...] = ()
    rejected: tuple[Mapping[str, Any], ...] = ()
    duplicate_chunk_ids: tuple[str, ...] = ()
    wrong_identity_chunk_ids: tuple[str, ...] = ()
    rejection_reasons: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "triggered": self.triggered,
            "reason": self.reason,
            "supplemental_query": (
                {
                    "question": self.supplemental_query.question,
                    "knowledge_base_id": self.supplemental_query.knowledge_base_id,
                    "generation_id": self.supplemental_query.generation_id,
                    "coverage_gap": list(self.supplemental_query.coverage_gap),
                    "top_k": self.supplemental_query.top_k,
                    "attempt": self.supplemental_query.attempt,
                }
                if self.supplemental_query
                else None
            ),
            "coverage_requirements": list(self.coverage_requirements),
            "coverage_gap": list(self.coverage_gap),
            "coverage_before": list(self.coverage_before),
            "coverage_after": list(self.coverage_after),
            "retrieved": [dict(item) for item in self.retrieved],
            "accepted": [dict(item) for item in self.accepted],
            "rejected": [dict(item) for item in self.rejected],
            "duplicate_chunk_ids": list(self.duplicate_chunk_ids),
            "wrong_identity_chunk_ids": list(self.wrong_identity_chunk_ids),
            "rejection_reasons": dict(self.rejection_reasons),
        }


def derive_coverage_requirements(question: str, question_type: str | None = None) -> tuple[str, ...]:
    """Infer generic requirements from the question, never from the Golden Set."""
    q = str(question or "").casefold()
    kind = question_type
    if not kind:
        kind = next((name for name, terms in _TYPE_TERMS if any(t.casefold() in q for t in terms)), "parameter")
    return _REQUIREMENTS.get(kind, ("independent_answer_point",))


def build_supplemental_query(
    question: str,
    *,
    knowledge_base_id: str,
    generation_id: str,
    coverage_gap: Iterable[str],
) -> SupplementalQuery:
    gap = tuple(dict.fromkeys(str(item) for item in coverage_gap if item))
    return SupplementalQuery(str(question), str(knowledge_base_id), str(generation_id), gap)


def _candidate(item: Mapping[str, Any]) -> SupplementalCandidate:
    return SupplementalCandidate(
        chunk_id=str(item.get("chunk_id") or item.get("id") or ""),
        document_id=item.get("document_id"),
        generation_id=item.get("generation_id"),
        knowledge_base_id=item.get("knowledge_base_id"),
        rank=item.get("rank") if isinstance(item.get("rank"), int) else item.get("initial_rank"),
        payload=item,
    )


def run_supplemental_retrieval(
    question: str,
    *,
    knowledge_base_id: str,
    generation_id: str,
    selected: Sequence[Mapping[str, Any]] = (),
    coverage_before: Iterable[str] = (),
    coverage_after_context: Iterable[str] = (),
    question_type: str | None = None,
    status: str = "partial_answer",
    is_negative: bool = False,
    retrieve: Callable[[SupplementalQuery], Iterable[Mapping[str, Any]]] | None = None,
) -> SupplementalRetrievalResult:
    """Plan and, when permitted, execute exactly one bounded retrieval call."""
    requirements = derive_coverage_requirements(question, question_type)
    before = tuple(dict.fromkeys(str(x) for x in coverage_before))
    after_context = tuple(dict.fromkeys(str(x) for x in coverage_after_context))
    missing = tuple(item for item in requirements if item not in before)
    selected_ids = {str(item.get("chunk_id")) for item in selected if item.get("chunk_id")}
    base = dict(
        triggered=False,
        supplemental_query=None,
        coverage_requirements=requirements,
        coverage_gap=missing,
        coverage_before=before,
        coverage_after=tuple(dict.fromkeys((*before, *after_context))),
        retrieved=(), accepted=(), rejected=(), duplicate_chunk_ids=(), wrong_identity_chunk_ids=(), rejection_reasons={},
    )
    if is_negative:
        return SupplementalRetrievalResult(reason="negative_question", **base)
    if status != "partial_answer":
        return SupplementalRetrievalResult(reason="not_partial_answer", **base)
    if not missing:
        return SupplementalRetrievalResult(reason="no_coverage_gap", **base)
    if any(term in str(question).casefold() for term in _NEGATIVE_TERMS):
        return SupplementalRetrievalResult(reason="negative_question", **base)
    if any(item in after_context for item in missing):
        return SupplementalRetrievalResult(reason="parent_adjacent_resolved", **base)
    query = build_supplemental_query(question, knowledge_base_id=knowledge_base_id, generation_id=generation_id, coverage_gap=missing)
    if retrieve is None:
        return SupplementalRetrievalResult(reason="retriever_unavailable", supplemental_query=query, **{k: v for k, v in base.items() if k != "supplemental_query"})
    raw = tuple(dict(item) for item in retrieve(query))
    accepted: list[Mapping[str, Any]] = []
    rejected: list[Mapping[str, Any]] = []
    duplicates: list[str] = []
    wrong: list[str] = []
    reasons: dict[str, str] = {}
    seen = set(selected_ids)
    for item in raw:
        c = _candidate(item)
        if not c.chunk_id or c.chunk_id in seen:
            if c.chunk_id:
                if c.chunk_id in selected_ids:
                    reasons[c.chunk_id] = "already_selected"
                else:
                    duplicates.append(c.chunk_id)
                    reasons[c.chunk_id] = "duplicate_chunk"
            rejected.append(item)
            continue
        # Supplemental evidence must carry explicit identity; an omitted
        # identity is not safe to treat as same-KB/same-generation.
        if c.knowledge_base_id != knowledge_base_id or c.generation_id != generation_id:
            wrong.append(c.chunk_id)
            reasons[c.chunk_id] = "wrong_identity"
            rejected.append(item)
            continue
        if len(accepted) >= MAX_FINAL_EVIDENCE - len(selected):
            reasons[c.chunk_id] = "final_evidence_limit"
            rejected.append(item)
            continue
        seen.add(c.chunk_id)
        accepted.append(item)
    after = tuple(dict.fromkeys((*before, *after_context)))
    # Retrieval payloads are not interpreted as Golden answers; coverage is
    # recorded as before/after context only unless caller performs its own
    # deterministic coverage check.
    return SupplementalRetrievalResult(True, "coverage_gap", query, requirements, missing, before, after, raw, tuple(accepted), tuple(rejected), tuple(duplicates), tuple(wrong), reasons)

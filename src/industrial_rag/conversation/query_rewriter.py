"""Bounded, evidence-neutral conversation query rewriting.

History is used only to resolve the meaning of the current question.  This
module never returns evidence and its output is the only conversation-derived
value allowed to enter retrieval.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

RewriteStatus = Literal["unchanged", "rewritten", "ambiguous", "failed"]
RewriteReason = Literal[
    "none", "pronoun_resolution", "ellipsis_resolution", "constraint_inheritance"
]

MAX_HISTORY_MESSAGES = 6
MAX_MESSAGE_CONTENT_LENGTH = 2000
REWRITE_VERSION = "query-rewrite-v1"

HistoryItem = Mapping[str, Any]
Provider = Callable[[str, list[dict[str, str]]], Any]


@dataclass(frozen=True, slots=True)
class QueryRewriteResult:
    original_query: str
    history_dependent: bool
    status: RewriteStatus
    rewrite_reason: RewriteReason
    standalone_query: str | None
    history_available: bool = False
    history_message_count: int = 0
    history_used: bool = False
    failure_reason: str | None = None

    def to_trace(self) -> dict[str, object]:
        """Return bounded observability metadata without history content."""

        return {
            "original_query": self.original_query,
            "history_available": self.history_available,
            "history_message_count": self.history_message_count,
            "history_used": self.history_used,
            "rewrite_required": self.history_dependent,
            "rewrite_status": self.status,
            "rewrite_reason": self.rewrite_reason,
            "rewritten_query": self.standalone_query if self.status == "rewritten" else None,
            "failure_reason": self.failure_reason,
            "rewrite_version": REWRITE_VERSION,
        }


def build_query_rewrite_prompt(query: str, history: Sequence[HistoryItem]) -> str:
    """Build the provider prompt; history is context, never evidence."""

    messages = "\n".join(
        f"{item['role']}: {item['content']}" for item in history
    ) or "(无可用历史)"
    return f"""你是工业知识问答系统的查询改写模块。

你的任务不是回答问题，而是根据有限的会话历史，将当前用户问题改写为无需依赖聊天历史即可理解的独立问题。

规则：
1. 不得回答用户问题。
2. 不得添加会话中不存在的设备、参数、条件或技术事实。
3. 只允许解决指代、省略和明确的上下文条件继承。
4. 当前问题已经独立完整时保持原文。
5. 如果存在多个可能指代对象，必须返回 ambiguous，不得猜测。
6. 不得从历史 Assistant 回答中提取新的技术事实；历史仅是语义上下文，不是事实证据。
7. 保留设备名称、型号、参数名称、单位和限定条件。
8. 输出必须符合固定 JSON 结构，不得输出答案正文。

有限会话历史：
{messages}

当前问题：
{query}
"""


class QueryRewriter:
    """Rewrite only the small, safe first-version dependency patterns."""

    def __init__(self, provider: Provider | None = None) -> None:
        self._provider = provider

    async def rewrite(
        self, query: str, history: Sequence[HistoryItem] | None = None
    ) -> QueryRewriteResult:
        original = query.strip()
        bounded = _sanitize_history(history or ())
        independent = _is_independent(original)
        base = QueryRewriteResult(
            original_query=original,
            history_dependent=not independent,
            status="unchanged" if independent else "failed",
            rewrite_reason="none",
            standalone_query=original if independent else None,
            history_available=bool(bounded),
            history_message_count=len(bounded),
        )
        if independent and self._provider is None:
            return base
        if self._provider is not None:
            try:
                candidate = self._validate_provider_result(
                    await _maybe_await(self._provider(original, bounded)), original, bounded
                )
            except Exception as error:
                if independent:
                    return base
                return _failed(base, type(error).__name__)
            if candidate is not None:
                return candidate
            if independent:
                return base
            return _failed(base, "invalid_structured_output")
        return self._deterministic(original, bounded, base)

    def _deterministic(
        self,
        query: str,
        history: list[dict[str, str]],
        base: QueryRewriteResult,
    ) -> QueryRewriteResult:
        user_questions = [item["content"] for item in history if item["role"] == "user"]
        if not user_questions:
            return _failed(base, "missing_context")
        latest = user_questions[-1]
        candidates = _extract_subjects(latest)
        if _has_pronoun(query):
            if len(candidates) > 1:
                return _ambiguous(base, "ambiguous_context")
            if not candidates:
                return _failed(base, "missing_context")
            subject = candidates[0]
            pronoun = re.match(r"(它|这个|那个|该设备|此设备|其)", query)
            suffix = query[pronoun.end() :] if pronoun else query
            return _rewritten(base, _combine_subject_and_suffix(subject, suffix), "pronoun_resolution")
        if _is_constraint_ellipsis(query):
            if len(candidates) > 1:
                return _ambiguous(base, "ambiguous_context")
            if not candidates:
                return _failed(base, "missing_context")
            subject = candidates[0]
            if "这种情况下" in query or "该情况下" in query:
                return _failed(base, "ambiguous_constraint")
            if "正常工作压力" in latest:
                return _rewritten(
                    base,
                    f"{subject}在{re.sub(r'呢[？?。！!]?$', '', query).strip()}的正常工作压力是多少？",
                    "constraint_inheritance",
                )
            return _failed(base, "unsupported_constraint")
        if _is_property_ellipsis(query):
            if len(candidates) > 1:
                return _ambiguous(base, "ambiguous_context")
            if not candidates:
                return _failed(base, "missing_context")
            subject = candidates[0]
            property_name = query.rstrip("？?。 ").removesuffix("呢").strip()
            question_suffix = _property_question_suffix(property_name)
            if question_suffix is None:
                return _failed(base, "unsupported_ellipsis")
            return _rewritten(
                base,
                f"{subject}的{property_name}{question_suffix}",
                "ellipsis_resolution",
            )
        return _failed(base, "unsupported_dependency")

    @staticmethod
    def _validate_provider_result(
        payload: Any, original: str, history: list[dict[str, str]]
    ) -> QueryRewriteResult | None:
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, Mapping):
            return None
        status = payload.get("status")
        reason = payload.get("rewrite_reason")
        dependent = payload.get("history_dependent")
        standalone = payload.get("standalone_query")
        if status not in {"unchanged", "rewritten", "ambiguous", "failed"}:
            return None
        if reason not in {"none", "pronoun_resolution", "ellipsis_resolution", "constraint_inheritance"}:
            return None
        if not isinstance(dependent, bool):
            return None
        if status == "rewritten" and (
            not dependent
            or reason == "none"
            or not isinstance(standalone, str)
            or not standalone.strip()
        ):
            return None
        if status == "unchanged" and (dependent or standalone != original):
            return None
        if status == "ambiguous" and standalone is not None:
            return None
        if status == "failed" and standalone is not None:
            return None
        return QueryRewriteResult(
            original_query=original,
            history_dependent=dependent,
            status=status,
            rewrite_reason=reason,
            standalone_query=standalone.strip() if isinstance(standalone, str) else None,
            history_available=bool(history),
            history_message_count=len(history),
            history_used=dependent,
        )


def _sanitize_history(history: Sequence[HistoryItem]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for item in history:
        if not isinstance(item, Mapping) or item.get("role") not in {"user", "assistant"}:
            continue
        content = item.get("content")
        if (
            not isinstance(content, str)
            or not content.strip()
            or len(content) > MAX_MESSAGE_CONTENT_LENGTH
        ):
            continue
        cleaned.append(
            {
                "role": str(item["role"]),
                "content": content.strip(),
            }
        )
    return cleaned[-MAX_HISTORY_MESSAGES:]


def _is_independent(query: str) -> bool:
    return bool(query) and not (_has_pronoun(query) or _is_constraint_ellipsis(query) or _is_property_ellipsis(query))


def _has_pronoun(query: str) -> bool:
    return bool(re.search(r"(?:^|[，,、\s])(它|这个|那个|该设备|此设备|其)(?:[\u4e00-\u9fff\w]|$)", query))


def _is_constraint_ellipsis(query: str) -> bool:
    return bool(re.search(r"(?:高温|低温|低负荷|满负荷|这种情况下|该情况下).*呢[？?。！!]?$", query))


def _is_property_ellipsis(query: str) -> bool:
    return query.rstrip().endswith(("呢？", "呢?", "呢。", "呢"))


def _extract_subjects(question: str) -> list[str]:
    if re.search(r"\b[A-Z]\s*泵和\s*[A-Z]\s*泵", question):
        return ["A 泵", "B 泵"]
    definition = re.search(r"什么是(.+?)[？?。]?$", question)
    if definition:
        return [definition.group(1).strip(" ，,：:")]
    introduction = re.search(r"介绍一下(.+?)[？?。！!]?$", question)
    if introduction:
        return [introduction.group(1).strip(" ，,：:")]
    pair = re.search(r"(.+?)和(.+?)(?:有什么区别|有何不同|的压力有何不同)", question)
    if pair:
        return [pair.group(1).strip(" ，,：:") , pair.group(2).strip(" ，,：:")]
    property_match = re.search(
        r"(.+?)(?:的)?(?:入口|出口|正常工作|工作|额定|润滑|最高|最低)?"
        r"(?:压力|温度|流量|转速|频率|周期|条件|步骤|要求|标准|状态|方式|原因|方法)"
        r"(?:是多少|是什么|如何|怎么|呢)?[？?。！!]?$",
        question,
    )
    if property_match:
        candidate = _clean_subject(property_match.group(1))
        if candidate:
            return [candidate]
    match = re.search(r"(.+?)(?:的)?(?:启动|正常工作|工作|额定|维护|是什么|有什么区别)", question)
    if match:
        candidate = match.group(1).strip(" ，,：:？?。")
        if candidate:
            return [candidate]
    generic = re.search(r"(.+?)(?:如何|怎么|多久|多少|是什么|是否|有何|有什么)", question)
    if generic:
        candidate = _clean_subject(generic.group(1))
        if candidate:
            return [candidate]
    return []


def _clean_subject(value: str) -> str:
    value = value.rstrip("的 ").strip(" ，,：:？?。")
    return re.sub(
        r"的(?:启动|停止|正常工作|工作|额定|维护|入口|出口|润滑)(?:条件|压力|温度|流量|转速|频率|周期)?$",
        "",
        value,
    ).rstrip("的 ")


def _property_question_suffix(property_name: str) -> str | None:
    """Return a safe question ending for an explicit property ellipsis.

    The property must be named in the current turn.  This deliberately does
    not infer a missing property from the previous answer.
    """

    if not property_name or any(char in property_name for char in "，,；;。！？?!"):
        return None
    if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9\-（）() /]+", property_name):
        return None
    if not any(
        marker in property_name
        for marker in (
            "压力",
            "温度",
            "流量",
            "转速",
            "频率",
            "周期",
            "步骤",
            "条件",
            "要求",
            "标准",
            "原因",
            "方法",
            "状态",
            "方式",
        )
    ):
        return None
    if any(marker in property_name for marker in ("步骤", "条件", "要求", "标准", "原因", "方法", "状态", "方式")):
        return "是什么？"
    return "是多少？"


def _combine_subject_and_suffix(subject: str, suffix: str) -> str:
    """Join a resolved subject without duplicating a shared word boundary."""

    if not suffix:
        return subject
    for size in range(min(len(subject), len(suffix)), 0, -1):
        if subject[-size:] == suffix[:size] and all(
            "\u4e00" <= char <= "\u9fff" for char in subject[-size:]
        ):
            suffix = suffix[size:]
            break
    return f"{subject}{suffix}"


def _rewritten(base: QueryRewriteResult, query: str, reason: RewriteReason) -> QueryRewriteResult:
    return QueryRewriteResult(
        original_query=base.original_query,
        history_dependent=True,
        status="rewritten",
        rewrite_reason=reason,
        standalone_query=query,
        history_available=base.history_available,
        history_message_count=base.history_message_count,
        history_used=True,
    )


def _failed(base: QueryRewriteResult, reason: str) -> QueryRewriteResult:
    return QueryRewriteResult(
        original_query=base.original_query,
        history_dependent=base.history_dependent,
        status="failed" if reason != "ambiguous_context" else "ambiguous",
        rewrite_reason="pronoun_resolution" if "ambiguous" in reason else base.rewrite_reason,
        standalone_query=None,
        history_available=base.history_available,
        history_message_count=base.history_message_count,
        history_used=base.history_dependent,
        failure_reason=reason,
    )


def _ambiguous(base: QueryRewriteResult, reason: str) -> QueryRewriteResult:
    return QueryRewriteResult(
        original_query=base.original_query,
        history_dependent=True,
        status="ambiguous",
        rewrite_reason="pronoun_resolution",
        standalone_query=None,
        history_available=base.history_available,
        history_message_count=base.history_message_count,
        history_used=True,
        failure_reason=reason,
    )


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value

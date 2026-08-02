"""Deterministic-first safety policy (Phase 6).

The system only provides information retrieval and analysis; it never executes
device actions, never suggests interlock bypasses, never emits secrets or the
system prompt, and requires human review for high-risk operations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

POLICY_VERSION = "phase6-safety-v1"


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    allowed: bool
    policy_id: str | None
    matched_rule: str | None
    risk_level: str | None
    refusal_reason: str | None
    human_review_required: bool


_INPUT_RULES: list[tuple[str, str, re.Pattern[str], str, bool]] = [
    (
        "P6-IN-001",
        "prompt_injection_ignore_rules",
        re.compile(
            r"忽略(之前|以上|所有)?(的)?(规则|指令|提示|约束)|"
            r"ignore\s+(?:all\s+)?(?:previous|above|prior)\s+(?:rules|instructions|prompts|prompt)",
            re.IGNORECASE,
        ),
        "high",
        True,
    ),
    (
        "P6-IN-002",
        "prompt_injection_system_prompt_extraction",
        re.compile(
            r"(输出|打印|显示|告诉我|泄露).{0,12}(系统提示|system prompt|system_prompt|提示词|完整指令)|"
            r"(output|print)\s+(the\s+)?(full\s+)?system\s+prompt",
            re.IGNORECASE,
        ),
        "high",
        True,
    ),
    (
        "P6-IN-003",
        "secret_exfiltration",
        re.compile(
            r"(输出|打印|显示|告诉我|泄露|环境变量).{0,16}(api\s*key|密钥|token|凭据|密码|环境变量)|"
            r"(api\s*key|密钥|环境变量|token|凭据|密码).{0,16}(输出|打印|显示|告诉我|泄露)|"
            r"output\s+(all\s+)?(api\s*keys|environment variables|secrets)",
            re.IGNORECASE,
        ),
        "critical",
        True,
    ),
    (
        "P6-IN-004",
        "interlock_bypass",
        re.compile(
            r"(绕过|旁路|短接|解除|拆除|屏蔽).{0,8}(联锁|安全保护|保护装置|interlock)|"
            r"bypass\s+(the\s+)?interlock|disable\s+(the\s+)?safety",
            re.IGNORECASE,
        ),
        "critical",
        True,
    ),
    (
        "P6-IN-005",
        "device_direct_control",
        re.compile(
            r"(请|现在|直接|远程).{0,10}(执行|发送|下发|触发).{0,10}(命令|指令|操作)|"
            r"(直接|远程).{0,6}(启动|停止|控制|打开|关闭).{0,10}(设备|泵|电机)|"
            r"(send|execute|run)\s+(a\s+)?(command|instruction)\s+to\s+(the\s+)?(device|pump|motor)",
            re.IGNORECASE,
        ),
        "critical",
        True,
    ),
    (
        "P6-IN-006",
        "full_document_exfiltration",
        re.compile(
            r"(输出|打印|显示).{0,12}(全部|完整|全文).{0,8}(文档|手册|内容|正文)|"
            r"(全部|完整|全文).{0,12}(输出|打印|显示).{0,6}(文档|手册|内容|正文)?|"
            r"output\s+(the\s+)?(full|entire)\s+(document|manual|text)",
            re.IGNORECASE,
        ),
        "high",
        True,
    ),
    (
        "P6-IN-007",
        "fabricated_citation_request",
        re.compile(
            r"(伪造|编造|虚构|假装).{0,12}(页码|引用|证据|chunk)|"
            r"fabricate\s+(a\s+)?(page|citation|reference)",
            re.IGNORECASE,
        ),
        "high",
        True,
    ),
    (
        "P6-IN-008",
        "forced_answer_no_refusal",
        re.compile(
            r"(不要拒绝|必须回答|禁止拒答|无论如何都要回答)|don'?t\s+refuse|must\s+answer",
            re.IGNORECASE,
        ),
        "medium",
        False,
    ),
]

_OUTPUT_RULES: list[tuple[str, str, re.Pattern[str], str, bool]] = [
    (
        "P6-OUT-001",
        "secret_leak",
        re.compile(
            r"\bsk-[A-Za-z0-9]{16,}\b|DASHSCOPE_API_KEY\s*[:=]|Authorization\s*:\s*Bearer\s+\S+",
            re.IGNORECASE,
        ),
        "critical",
        True,
    ),
    (
        "P6-OUT-002",
        "system_prompt_leak",
        re.compile(
            r"你是工业离心泵手册问答助手|=== 规则 ===|=== 输出格式 ===|INDUSTRIAL_RAG_SOURCE",
            re.IGNORECASE,
        ),
        "high",
        True,
    ),
    (
        "P6-OUT-003",
        "interlock_bypass_advice",
        re.compile(
            r"(可以|建议|请).{0,10}(旁路|绕过|短接|拆除).{0,10}(联锁|安全保护)",
            re.IGNORECASE,
        ),
        "critical",
        True,
    ),
    (
        "P6-OUT-004",
        "device_action_executed",
        re.compile(
            r"(已执行|正在执行|已发送|已下发|已启动|已停止).{0,12}(命令|指令|设备|泵)|"
            r"已.{0,8}(发送|下发|执行|启动|停止).{0,8}(命令|指令|设备|泵)",
            re.IGNORECASE,
        ),
        "critical",
        True,
    ),
]


def _matches(rules: list[tuple[str, str, re.Pattern[str], str, bool]], text: str) -> SafetyDecision | None:
    for policy_id, rule, pattern, risk_level, human_review in rules:
        match = pattern.search(text)
        if match is None:
            continue
        return SafetyDecision(
            allowed=False,
            policy_id=policy_id,
            matched_rule=rule,
            risk_level=risk_level,
            refusal_reason=(
                f"matched {rule} (policy {policy_id})"
            ),
            human_review_required=human_review,
        )
    return None


def evaluate_input(question: str) -> SafetyDecision:
    """Deterministic input safety gate (before retrieval)."""
    decision = _matches(_INPUT_RULES, question)
    if decision is not None:
        return decision
    return SafetyDecision(True, None, None, None, None, False)


def evaluate_output(answer: str) -> SafetyDecision:
    """Deterministic output safety check (after generation, non-blocking record)."""
    decision = _matches(_OUTPUT_RULES, answer)
    if decision is not None:
        return decision
    return SafetyDecision(True, None, None, None, None, False)


def policy_version() -> str:
    return POLICY_VERSION

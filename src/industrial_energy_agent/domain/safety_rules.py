"""Deterministic, offline industrial-safety classification and output review."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from industrial_energy_agent.domain.enums import ActionMode, RiskLevel
from industrial_energy_agent.domain.errors import (
    contains_sensitive_or_internal_text,
    is_sensitive_field_name,
)
from industrial_energy_agent.domain.models import WorkOrderDraft


class SafetyDisposition(StrEnum):
    """Allowed response handling after deterministic safety review."""

    ANSWER = "answer"
    RESTRICTED = "restricted"
    BLOCKED = "blocked"


class _SafetyModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        hide_input_in_errors=True,
    )


class SafetyAssessment(_SafetyModel):
    """Stable result of deterministic input classification."""

    action_mode: ActionMode
    risk_level: RiskLevel
    prohibited: bool
    approval_required: bool
    disposition: SafetyDisposition
    classification_failed: bool = False
    reason_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=10)

    @model_validator(mode="after")
    def safety_fields_are_consistent(self) -> SafetyAssessment:
        if self.prohibited:
            if (
                self.disposition is not SafetyDisposition.BLOCKED
                or self.risk_level is not RiskLevel.CRITICAL
                or not self.approval_required
                or self.action_mode is not ActionMode.PROHIBITED_BYPASS
            ):
                raise ValueError("prohibited assessments must be critical and blocked")
        elif self.disposition is SafetyDisposition.BLOCKED:
            raise ValueError("only prohibited assessments may be blocked")
        if self.action_mode is ActionMode.PROHIBITED_BYPASS and not self.prohibited:
            raise ValueError("prohibited bypass mode must be blocked")
        if self.classification_failed and (
            self.risk_level not in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            or not self.approval_required
            or self.disposition not in {SafetyDisposition.RESTRICTED, SafetyDisposition.BLOCKED}
        ):
            raise ValueError("failed classification must fail closed")
        if self.disposition is SafetyDisposition.ANSWER and (
            self.approval_required
            or self.prohibited
            or self.risk_level not in {RiskLevel.LOW, RiskLevel.MEDIUM}
        ):
            raise ValueError("answer assessments cannot require approval")
        if self.disposition is SafetyDisposition.RESTRICTED and (
            not self.approval_required
            or self.risk_level not in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            or self.prohibited
        ):
            raise ValueError("restricted assessments require non-prohibited high risk")
        if self.action_mode in {
            ActionMode.PROCEDURE_REQUEST,
            ActionMode.DRAFT_REQUEST,
            ActionMode.OPERATION_COMMAND,
        } and (
            self.risk_level not in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            or self.disposition is not SafetyDisposition.RESTRICTED
            or not self.approval_required
            or self.prohibited
        ):
            raise ValueError("actionable assessments must be restricted high risk")
        return self


class SafetyOutputReview(_SafetyModel):
    """Public output after rescanning generated answer or draft text."""

    answer: str = Field(min_length=1, max_length=20_000)
    action_mode: ActionMode
    risk_level: RiskLevel
    prohibited: bool
    approval_required: bool
    disposition: SafetyDisposition
    citations_allowed: bool
    draft_allowed: bool
    allowed_for_review: bool = False
    safety_check_failed: bool = False
    sensitive_content: bool = False
    reason_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=10)

    @model_validator(mode="after")
    def output_fields_are_consistent(self) -> SafetyOutputReview:
        if self.prohibited:
            if (
                self.disposition is not SafetyDisposition.BLOCKED
                or self.risk_level is not RiskLevel.CRITICAL
                or not self.approval_required
                or self.draft_allowed
                or self.allowed_for_review
                or self.citations_allowed
                or self.action_mode is not ActionMode.PROHIBITED_BYPASS
            ):
                raise ValueError("prohibited output must be critical, blocked, and not draftable")
        elif self.disposition is SafetyDisposition.BLOCKED:
            raise ValueError("only prohibited output may be blocked")
        if self.action_mode is ActionMode.PROHIBITED_BYPASS and not self.prohibited:
            raise ValueError("prohibited bypass output must be blocked")
        if self.disposition is SafetyDisposition.ANSWER and (
            self.approval_required
            or self.prohibited
            or self.draft_allowed
            or self.allowed_for_review
            or self.risk_level not in {RiskLevel.LOW, RiskLevel.MEDIUM}
        ):
            raise ValueError("answer output cannot require review or allow a draft")
        if self.disposition is SafetyDisposition.RESTRICTED and (
            not self.approval_required
            or self.risk_level not in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            or self.prohibited
        ):
            raise ValueError("restricted output requires non-prohibited high risk")
        if self.safety_check_failed and (
            self.risk_level not in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            or not self.approval_required
            or self.draft_allowed
            or self.allowed_for_review
            or self.citations_allowed
            or self.disposition not in {SafetyDisposition.RESTRICTED, SafetyDisposition.BLOCKED}
        ):
            raise ValueError("failed safety checks must fail closed")
        if self.sensitive_content and (
            not self.approval_required
            or self.risk_level not in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            or self.disposition not in {SafetyDisposition.RESTRICTED, SafetyDisposition.BLOCKED}
            or self.citations_allowed
            or self.draft_allowed
            or self.allowed_for_review
        ):
            raise ValueError("sensitive output must fail closed")
        if self.draft_allowed != self.allowed_for_review:
            raise ValueError("draft review flags must be mutually consistent")
        if self.draft_allowed and (
            self.action_mode is not ActionMode.DRAFT_REQUEST
            or self.prohibited
            or self.safety_check_failed
            or self.sensitive_content
            or self.disposition is not SafetyDisposition.RESTRICTED
            or self.risk_level not in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            or not self.approval_required
        ):
            raise ValueError("draft review requires a review-safe draft request")
        if self.action_mode in {
            ActionMode.PROCEDURE_REQUEST,
            ActionMode.DRAFT_REQUEST,
            ActionMode.OPERATION_COMMAND,
        } and (
            self.risk_level not in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            or self.disposition is not SafetyDisposition.RESTRICTED
            or not self.approval_required
            or self.prohibited
        ):
            raise ValueError("actionable output must be restricted high risk")
        return self


@dataclass(frozen=True)
class _SafetyTextViews:
    normalized: str
    compact: str
    credential_scan: str


_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "\N{IDEOGRAPHIC FULL STOP}": ".",
        "\N{IDEOGRAPHIC COMMA}": ",",
        "\N{FULLWIDTH SEMICOLON}": ";",
        "\N{FULLWIDTH COLON}": ":",
        "\N{FULLWIDTH EXCLAMATION MARK}": "!",
        "\N{FULLWIDTH QUESTION MARK}": "?",
    }
)
_COMPACT_SEPARATOR = re.compile(r"[\s,.;:!?]+")
_DEFAULT_IGNORABLE_RANGES = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)
_CREDENTIAL_CONFUSABLE_TRANSLATION = str.maketrans(
    {
        codepoint: ascii_character
        for ascii_character, codepoints in {
            "A": (0x0391, 0x0410),
            "a": (0x03B1, 0x0430),
            "B": (0x0392, 0x0412),
            "b": (0x03B2, 0x0432),
            "C": (0x03F9, 0x0421),
            "c": (0x03F2, 0x0441),
            "E": (0x0395, 0x0415),
            "e": (0x03B5, 0x0435),
            "H": (0x0397, 0x041D),
            "h": (0x03B7, 0x043D),
            "I": (0x0399, 0x0406, 0x04C0),
            "i": (0x03B9, 0x0456, 0x04CF),
            "J": (0x0408,),
            "j": (0x03F3, 0x0458),
            "K": (0x039A, 0x041A),
            "k": (0x03BA, 0x043A),
            "M": (0x039C, 0x041C),
            "m": (0x03BC, 0x043C),
            "N": (0x039D,),
            "n": (0x03BD,),
            "O": (0x039F, 0x041E),
            "o": (0x03BF, 0x043E),
            "P": (0x03A1, 0x0420),
            "p": (0x03C1, 0x0440),
            "S": (0x0405,),
            "s": (0x0455,),
            "T": (0x03A4, 0x0422),
            "t": (0x03C4, 0x0442),
            "X": (0x03A7, 0x0425),
            "x": (0x03C7, 0x0445),
            "Y": (0x03A5, 0x04AE, 0x0423),
            "y": (0x03C5, 0x04AF, 0x0443),
        }.items()
        for codepoint in codepoints
    }
)


def _is_default_ignorable(character: str) -> bool:
    codepoint = ord(character)
    return unicodedata.category(character) == "Cf" or any(
        start <= codepoint <= end for start, end in _DEFAULT_IGNORABLE_RANGES
    )


def _normalize_safety_text(text: str) -> _SafetyTextViews:
    nfkc = unicodedata.normalize("NFKC", text)
    visible = "".join(character for character in nfkc if not _is_default_ignorable(character))
    punctuation_normalized = visible.translate(_PUNCTUATION_TRANSLATION)
    normalized = re.sub(r"\s+", " ", punctuation_normalized).strip()
    return _SafetyTextViews(
        normalized=normalized,
        compact=_COMPACT_SEPARATOR.sub("", normalized),
        credential_scan=normalized.translate(_CREDENTIAL_CONFUSABLE_TRANSLATION),
    )


_BYPASS_ACTION = (
    r"(?:(?:旁路|解除|屏蔽|短接|绕过)(?:掉|掉了)?|"
    r"取消|停用|禁用|退出|撤销|失效|无效|停掉)"
)
_SAFETY_CONTROL = r"(?:联锁|保护|报警|安全回路)"
_PROHIBITED_PATTERNS = (
    re.compile(rf"{_BYPASS_ACTION}.{{0,16}}{_SAFETY_CONTROL}", re.I),
    re.compile(rf"{_SAFETY_CONTROL}.{{0,16}}{_BYPASS_ACTION}", re.I),
    re.compile(r"(?:强制|伪造).{0,12}(?:(?:DCS|PLC)\s*)?信号", re.I),
    re.compile(r"(?:DCS|PLC).{0,12}(?:强制|伪造).{0,8}信号", re.I),
    re.compile(r"(?:修改|调高|调低|篡改).{0,12}(?:保护)?定值", re.I),
    re.compile(r"(?:保护)?定值.{0,12}(?:修改|调高|调低|篡改)", re.I),
)
_DRAFT = re.compile(
    r"(?:(?:生成|创建|起草|草拟|写|制作).{0,16}(?:工单|草稿|操作票)|"
    r"(?:给我|来|要|提供)一份.{0,16}(?:工单|操作票))",
    re.I,
)
_WHY_EXPLANATION = re.compile(r"^\s*(?:为什么|为何)", re.I)
_INFORMATIONAL = re.compile(
    r"(?:为什么|为何|什么是|是什么|解释|介绍|原理|作用|原因|意义|是否需要)",
    re.I,
)
_PROCEDURE = re.compile(
    r"(?:(?:如何|怎么|流程|步骤|规程|操作指导|方法)|"
    r"(?:列出|给出|提供).{0,16}(?:检查)?清单)",
    re.I,
)
_INDUSTRIAL_OBJECT_PATTERN = (
    r"(?:泵速|转速|流量|温度|液位|频率|控制参数|泵体?|设备|电源|阀门?|DCS|"
    r"PLC|联锁|保护|危险介质|介质|管线|系统|回路|电机|机组|开关|断路器|压力)"
)
_VALVE_OR_SWITCH = r"(?:阀门?|开关|断路器)"
_OBJECT_ACTION_VERB = r"(?:启动|停止|复位|隔离|拆开|拆卸|排放|切换)"
_CONTROL_ACTION_VERB = r"(?:调节|调整|改变|提高|升高|降低|减小|增大|设定|修改)"
_CONTROL_PARAMETER_OBJECT = r"(?:阀门?|压力|泵速|转速|流量|温度|液位|频率|控制参数)"
_ACTION = re.compile(
    rf"(?:停机|断电|送电|切断电源|合闸|分闸|泄压|升压|降压|加压|投运|停运|"
    rf"(?:打开|开启|关闭|关断).{{0,3}}{_VALVE_OR_SWITCH}|"
    rf"{_VALVE_OR_SWITCH}.{{0,3}}(?:打开|开启|关闭|关断)|"
    rf"{_OBJECT_ACTION_VERB}.{{0,3}}{_INDUSTRIAL_OBJECT_PATTERN}|"
    rf"{_INDUSTRIAL_OBJECT_PATTERN}.{{0,3}}{_OBJECT_ACTION_VERB}|"
    rf"{_CONTROL_ACTION_VERB}.{{0,3}}{_CONTROL_PARAMETER_OBJECT}|"
    rf"{_CONTROL_PARAMETER_OBJECT}.{{0,3}}{_CONTROL_ACTION_VERB}|"
    r"操作\s*(?:DCS|PLC))",
    re.I,
)
_TEXT_SEGMENT = re.compile(r"[.!?;,]+")
_ACTION_EXPLANATION = re.compile(
    r"^\s*(?:.*(?:为什么|为何).*|"
    r"(?:请)?(?:解释|说明|介绍|分析).+|"
    r".+(?:有什么作用|作用是什么)|"
    r".+(?:什么是|是什么|是指|指的是|表示|定义|含义|意味着).+|"
    r".+(?:用于|作用(?:是|为)?|目的是|是为了).+|"
    r".+(?:可以|能够)(?:防止|避免|降低|保护).+)\s*$",
    re.I,
)
_ACTION_SEQUENCE = re.compile(
    rf"(?:然后|随后|接着|先.{{0,24}}(?:再|然后|后)|"
    rf"{_ACTION.pattern}.{{0,8}}后.{{0,8}}{_ACTION.pattern}|"
    rf"(?:再|并且?|并)\s*{_ACTION.pattern})",
    re.I,
)
_COMMAND_CUE = re.compile(
    r"(?:请(?!问|\s*(?:解释|说明|介绍|分析|回答))|立即|直接|马上|务必|现在|立刻|"
    r"然后|随后|接着)",
    re.I,
)
_INDUSTRIAL_OBJECT = re.compile(_INDUSTRIAL_OBJECT_PATTERN, re.I)
_KNOWN_INFORMATION = re.compile(
    r"(?:泵|设备|检修|安全|防护|液压|电气|联锁|上锁挂牌|隔离|工作原理)",
    re.I,
)
_INTERNAL_OUTPUT = re.compile(
    r"(?:system\s+prompt(?:\s+is)?\s*:|developer\s+(?:message|prompt)\s*:|"
    r"(?:hidden\s+)?reasoning(?:\s+follows)?(?:\s*:|\b)|private\s+analysis|"
    r"chain[-_ ]of[-_ ]thought(?:\s*:|\b)|系统提示词|内部(?:指令|提示|策略)|思维链)",
    re.I,
)
_INTERNAL_PAIR = re.compile(
    r"\b(?:system|developer|internal|hidden|private)[\s_-]+"
    r"(?:prompt|message|instruction|policy|reasoning|analysis)\b"
    r"(?:\s*(?:is\s*:?|[:=]))?",
    re.I,
)
_GENERIC_SECRET_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:[A-Za-z][A-Za-z0-9]*[\s_.-]+)*"
    r"(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS?)"
    r"(?![A-Za-z0-9])\s*(?::|=|is\s*:?)",
    re.I,
)
_CHINESE_SECRET_ASSIGNMENT = re.compile(r"(?:密钥|口令|密码|令牌)\s*[:=]", re.I)
_FIELD_ASSIGNMENT = re.compile(
    r"(?P<field>\b[A-Za-z][A-Za-z0-9_.-]{0,127})\s*[:=]",
)

_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}
_ACTION_MODE_ORDER = {
    ActionMode.INFORMATIONAL: 0,
    ActionMode.DRAFT_REQUEST: 1,
    ActionMode.PROCEDURE_REQUEST: 2,
    ActionMode.OPERATION_COMMAND: 3,
    ActionMode.PROHIBITED_BYPASS: 4,
}
_DISPOSITION_ORDER = {
    SafetyDisposition.ANSWER: 0,
    SafetyDisposition.RESTRICTED: 1,
    SafetyDisposition.BLOCKED: 2,
}

_RESTRICTED_ANSWER = (
    "该内容包含可执行的高风险工业操作, 已限制显示, 须由授权人员按现场规程人工复核。"
)
_BLOCKED_ANSWER = "该请求涉及绕过工业安全联锁或保护措施, 无法提供相关方法或草稿。"
_FAILED_REVIEW_ANSWER = "安全审查未能可靠完成, 原回答已限制显示并转人工复核。"
_SENSITIVE_OUTPUT_ANSWER = "回答包含不可公开的内部或敏感信息, 原内容已移除并转人工复核。"


def _contains_prohibited(text: _SafetyTextViews) -> bool:
    return any(pattern.search(text.compact) for pattern in _PROHIBITED_PATTERNS)


def _contains_sensitive_output(text: _SafetyTextViews) -> bool:
    return bool(
        _INTERNAL_OUTPUT.search(text.normalized)
        or _INTERNAL_PAIR.search(text.normalized)
        or _GENERIC_SECRET_ASSIGNMENT.search(text.normalized)
        or _CHINESE_SECRET_ASSIGNMENT.search(text.normalized)
        or any(
            is_sensitive_field_name(match.group("field"))
            for match in _FIELD_ASSIGNMENT.finditer(text.normalized)
        )
        or contains_sensitive_or_internal_text(text.normalized)
        or _GENERIC_SECRET_ASSIGNMENT.search(text.credential_scan)
        or any(
            is_sensitive_field_name(match.group("field"))
            for match in _FIELD_ASSIGNMENT.finditer(text.credential_scan)
        )
    )


def _contains_executable_action(text: _SafetyTextViews) -> bool:
    for segment in _TEXT_SEGMENT.split(text.normalized):
        if not segment.strip():
            continue
        has_action = _ACTION.search(segment) is not None
        has_command_cue = _COMMAND_CUE.search(segment) is not None
        is_sequence = _ACTION_SEQUENCE.search(segment) is not None
        is_explanation = _ACTION_EXPLANATION.search(segment) is not None
        if has_action:
            if has_command_cue or is_sequence:
                return True
            if is_explanation:
                continue
            return True
        if has_command_cue and _INDUSTRIAL_OBJECT.search(segment) and not is_explanation:
            return True
    return False


def _higher_risk(first: RiskLevel, second: RiskLevel) -> RiskLevel:
    return first if _RISK_ORDER[first] >= _RISK_ORDER[second] else second


def merge_safety_output_reviews(
    canonical: SafetyOutputReview,
    external: SafetyOutputReview,
) -> SafetyOutputReview:
    """Merge an external review into a canonical review without weakening any field."""

    prohibited = canonical.prohibited or external.prohibited
    sensitive_content = canonical.sensitive_content or external.sensitive_content
    safety_check_failed = canonical.safety_check_failed or external.safety_check_failed
    risk_level = _higher_risk(canonical.risk_level, external.risk_level)
    action_mode = max(
        (canonical.action_mode, external.action_mode),
        key=_ACTION_MODE_ORDER.__getitem__,
    )
    disposition = max(
        (canonical.disposition, external.disposition),
        key=_DISPOSITION_ORDER.__getitem__,
    )

    if prohibited:
        action_mode = ActionMode.PROHIBITED_BYPASS
        risk_level = RiskLevel.CRITICAL
        disposition = SafetyDisposition.BLOCKED
    elif (
        sensitive_content
        or safety_check_failed
        or risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
        or action_mode is not ActionMode.INFORMATIONAL
        or canonical.approval_required
        or external.approval_required
    ):
        risk_level = _higher_risk(risk_level, RiskLevel.HIGH)
        disposition = SafetyDisposition.RESTRICTED

    approval_required = (
        canonical.approval_required
        or external.approval_required
        or disposition is not SafetyDisposition.ANSWER
    )
    draft_allowed = canonical.draft_allowed and external.draft_allowed
    allowed_for_review = canonical.allowed_for_review and external.allowed_for_review
    if (
        action_mode is not ActionMode.DRAFT_REQUEST
        or disposition is not SafetyDisposition.RESTRICTED
        or prohibited
        or sensitive_content
        or safety_check_failed
    ):
        draft_allowed = False
        allowed_for_review = False

    citations_allowed = canonical.citations_allowed and external.citations_allowed
    if prohibited or sensitive_content or safety_check_failed:
        citations_allowed = False

    if prohibited:
        answer = _BLOCKED_ANSWER
    elif sensitive_content:
        answer = _SENSITIVE_OUTPUT_ANSWER
    elif safety_check_failed:
        answer = _FAILED_REVIEW_ANSWER
    elif draft_allowed:
        answer = canonical.answer
    elif disposition is SafetyDisposition.RESTRICTED:
        answer = (
            canonical.answer
            if canonical.disposition is SafetyDisposition.RESTRICTED and not canonical.draft_allowed
            else _RESTRICTED_ANSWER
        )
    else:
        answer = canonical.answer

    reason_codes = tuple(
        dict.fromkeys((*canonical.reason_codes, *external.reason_codes, "only_tighten_merge"))
    )[:10]
    return SafetyOutputReview(
        answer=answer,
        action_mode=action_mode,
        risk_level=risk_level,
        prohibited=prohibited,
        approval_required=approval_required,
        disposition=disposition,
        citations_allowed=citations_allowed,
        draft_allowed=draft_allowed,
        allowed_for_review=allowed_for_review,
        safety_check_failed=safety_check_failed,
        sensitive_content=sensitive_content,
        reason_codes=reason_codes,
    )


def work_order_review_fingerprint(draft: WorkOrderDraft) -> str:
    """Hash every reviewed work-order target field except its approval transition."""

    canonical = json.dumps(
        draft.model_dump(mode="json", exclude={"approval_status"}),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _deterministic_classification(text: str) -> tuple[ActionMode, RiskLevel, bool, bool]:
    safety_text = _normalize_safety_text(text)
    normalized = safety_text.normalized
    if not normalized:
        return ActionMode.INFORMATIONAL, RiskLevel.HIGH, False, True
    if _contains_prohibited(safety_text):
        return ActionMode.PROHIBITED_BYPASS, RiskLevel.CRITICAL, True, False
    executable_action = _contains_executable_action(safety_text)
    if _WHY_EXPLANATION.search(normalized) and not executable_action:
        return ActionMode.INFORMATIONAL, RiskLevel.MEDIUM, False, False
    if _DRAFT.search(normalized):
        return ActionMode.DRAFT_REQUEST, RiskLevel.HIGH, False, False
    if _PROCEDURE.search(normalized) and (
        _ACTION.search(normalized) or _INDUSTRIAL_OBJECT.search(normalized)
    ):
        return ActionMode.PROCEDURE_REQUEST, RiskLevel.HIGH, False, False
    if executable_action:
        return ActionMode.OPERATION_COMMAND, RiskLevel.HIGH, False, False
    if _INFORMATIONAL.search(normalized):
        return ActionMode.INFORMATIONAL, RiskLevel.MEDIUM, False, False
    if _KNOWN_INFORMATION.search(normalized):
        return ActionMode.INFORMATIONAL, RiskLevel.MEDIUM, False, False
    return ActionMode.INFORMATIONAL, RiskLevel.HIGH, False, True


def _parse_model_risk(model_risk: RiskLevel | str | None) -> tuple[RiskLevel | None, bool]:
    if model_risk is None:
        return None, False
    try:
        return RiskLevel(model_risk), False
    except (TypeError, ValueError):
        return None, True


def classify_input(
    text: str,
    *,
    model_risk: RiskLevel | str | None = None,
) -> SafetyAssessment:
    """Classify a request with prohibited patterns taking absolute precedence."""

    try:
        action_mode, risk_level, prohibited, classification_failed = _deterministic_classification(
            text
        )
    except Exception:
        action_mode = ActionMode.INFORMATIONAL
        risk_level = RiskLevel.HIGH
        prohibited = False
        classification_failed = True
    parsed_model_risk, invalid_model_risk = _parse_model_risk(model_risk)
    classification_failed = classification_failed or invalid_model_risk
    if invalid_model_risk:
        risk_level = _higher_risk(risk_level, RiskLevel.HIGH)
    elif parsed_model_risk is not None:
        risk_level = _higher_risk(risk_level, parsed_model_risk)

    if prohibited:
        disposition = SafetyDisposition.BLOCKED
    elif risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
        disposition = SafetyDisposition.RESTRICTED
    else:
        disposition = SafetyDisposition.ANSWER
    reason_codes: list[str] = [action_mode.value]
    if classification_failed:
        reason_codes.append("classification_failed")
    if parsed_model_risk is not None and parsed_model_risk is risk_level:
        reason_codes.append("model_risk_raised")
    return SafetyAssessment(
        action_mode=action_mode,
        risk_level=risk_level,
        prohibited=prohibited,
        approval_required=(prohibited or risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}),
        disposition=disposition,
        classification_failed=classification_failed,
        reason_codes=tuple(reason_codes),
    )


def review_output(
    answer: str,
    *,
    input_assessment: SafetyAssessment,
    model_risk: RiskLevel | str | None = None,
    safety_check_failed: bool = False,
) -> SafetyOutputReview:
    """Rescan generated content and replace unsafe content before it is exposed."""

    safety_text = _normalize_safety_text(answer)
    if safety_check_failed:
        return SafetyOutputReview(
            answer=_FAILED_REVIEW_ANSWER,
            action_mode=input_assessment.action_mode,
            risk_level=_higher_risk(input_assessment.risk_level, RiskLevel.HIGH),
            prohibited=input_assessment.prohibited,
            approval_required=True,
            disposition=(
                SafetyDisposition.BLOCKED
                if input_assessment.prohibited
                else SafetyDisposition.RESTRICTED
            ),
            citations_allowed=False,
            draft_allowed=False,
            safety_check_failed=True,
            reason_codes=("output_review_failed",),
        )

    output_prohibited = _contains_prohibited(safety_text)
    if input_assessment.prohibited or output_prohibited:
        return SafetyOutputReview(
            answer=_BLOCKED_ANSWER,
            action_mode=ActionMode.PROHIBITED_BYPASS,
            risk_level=RiskLevel.CRITICAL,
            prohibited=True,
            approval_required=True,
            disposition=SafetyDisposition.BLOCKED,
            citations_allowed=False,
            draft_allowed=False,
            reason_codes=("prohibited_output",),
        )

    if _contains_sensitive_output(safety_text):
        return SafetyOutputReview(
            answer=_SENSITIVE_OUTPUT_ANSWER,
            action_mode=input_assessment.action_mode,
            risk_level=_higher_risk(input_assessment.risk_level, RiskLevel.HIGH),
            prohibited=False,
            approval_required=True,
            disposition=SafetyDisposition.RESTRICTED,
            citations_allowed=False,
            draft_allowed=False,
            allowed_for_review=False,
            sensitive_content=True,
            reason_codes=("sensitive_output",),
        )

    parsed_model_risk, invalid_model_risk = _parse_model_risk(model_risk)
    output_has_command = _contains_executable_action(safety_text)
    output_risk = RiskLevel.HIGH if output_has_command else RiskLevel.LOW
    if invalid_model_risk:
        output_risk = _higher_risk(output_risk, RiskLevel.HIGH)
    elif parsed_model_risk is not None:
        output_risk = _higher_risk(output_risk, parsed_model_risk)
    final_risk = _higher_risk(input_assessment.risk_level, output_risk)
    requires_review = final_risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}
    if requires_review:
        draft_review_safe = (
            input_assessment.action_mode is ActionMode.DRAFT_REQUEST
            and not input_assessment.classification_failed
            and not invalid_model_risk
            and not output_has_command
        )
        return SafetyOutputReview(
            answer=answer if draft_review_safe else _RESTRICTED_ANSWER,
            action_mode=(
                ActionMode.OPERATION_COMMAND if output_has_command else input_assessment.action_mode
            ),
            risk_level=final_risk,
            prohibited=False,
            approval_required=True,
            disposition=SafetyDisposition.RESTRICTED,
            citations_allowed=False,
            draft_allowed=draft_review_safe,
            allowed_for_review=draft_review_safe,
            reason_codes=(
                "draft_output_review_safe" if draft_review_safe else "output_requires_review",
            ),
        )
    return SafetyOutputReview(
        answer=answer,
        action_mode=input_assessment.action_mode,
        risk_level=final_risk,
        prohibited=False,
        approval_required=False,
        disposition=SafetyDisposition.ANSWER,
        citations_allowed=True,
        draft_allowed=False,
        reason_codes=("informational_output",),
    )


def inspect_work_order_draft(
    draft: WorkOrderDraft,
    *,
    safety_check_failed: bool = False,
) -> SafetyOutputReview:
    """Inspect every persisted draft body field before creating its review."""

    draft_text = "\n".join(
        (
            draft.equipment,
            draft.symptom,
            *draft.candidate_causes,
            *draft.checks,
            *draft.safety_items,
        )
    )
    assessment = SafetyAssessment(
        action_mode=ActionMode.DRAFT_REQUEST,
        risk_level=RiskLevel.HIGH,
        prohibited=False,
        approval_required=True,
        disposition=SafetyDisposition.RESTRICTED,
        reason_codes=("persisted_draft",),
    )
    reviewed = review_output(
        draft_text,
        input_assessment=assessment,
        safety_check_failed=safety_check_failed,
    )
    if (
        reviewed.prohibited
        or reviewed.safety_check_failed
        or reviewed.sensitive_content
        or not reviewed.draft_allowed
        or not reviewed.allowed_for_review
    ):
        return reviewed
    return SafetyOutputReview.model_validate(
        reviewed.model_dump()
        | {
            "action_mode": ActionMode.DRAFT_REQUEST,
            "draft_allowed": True,
            "allowed_for_review": True,
            "reason_codes": ("persisted_draft_review_safe",),
        }
    )


def fail_closed_draft_inspection() -> SafetyOutputReview:
    """Return a fixed safe result when canonical draft inspection fails."""

    assessment = SafetyAssessment(
        action_mode=ActionMode.DRAFT_REQUEST,
        risk_level=RiskLevel.HIGH,
        prohibited=False,
        approval_required=True,
        disposition=SafetyDisposition.RESTRICTED,
        reason_codes=("persisted_draft",),
    )
    return review_output(
        "draft inspection unavailable",
        input_assessment=assessment,
        safety_check_failed=True,
    )

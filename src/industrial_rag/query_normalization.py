"""Deterministic query normalization for controlled Phase 10B experiments."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_WHITESPACE = re.compile(r"\s+")
_MODEL_PATTERN = re.compile(r"\b(2196(?:-[A-Z0-9]+)?)\b", re.IGNORECASE)
_COMPONENTS = (
    "入口管路",
    "出口管路",
    "填料函",
    "异径接头",
    "止回阀",
    "润滑油",
    "泵轴",
    "轴承",
    "叶轮",
    "机封",
)
_PARAMETERS = (
    "温度",
    "压力",
    "流量",
    "转速",
    "扭矩",
    "间隙",
    "频率",
    "润滑油",
)


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    original_query: str
    normalized_query: str
    detected_model: str | None
    detected_component: str | None
    detected_parameter: str | None
    added_aliases: tuple[str, ...]


def _replace_aliases(query: str) -> tuple[str, tuple[str, ...]]:
    aliases: list[str] = []
    replacements = (
        ("最高", "最大", "最高→最大"),
        ("上限", "最大", "上限→最大"),
        ("额定", "规定", "额定→规定"),
        ("摄氏度", "°C", "摄氏度→°C"),
        ("华氏度", "°F", "华氏度→°F"),
        ("℃", "°C", "℃→°C"),
        ("℉", "°F", "℉→°F"),
        ("如何操作", "操作步骤", "如何操作→操作步骤"),
        ("流程", "步骤", "流程→步骤"),
        ("顺序", "步骤", "顺序→步骤"),
    )
    for source, target, label in replacements:
        if source in query:
            query = query.replace(source, target)
            aliases.append(label)
    if "怎么" in query or "多久" in query:
        if "怎么" in query:
            query = query.replace("怎么", "如何")
        if "多久" in query:
            query = query.replace("多久", "如何")
        aliases.append("怎么/多久→如何")
    return query, tuple(dict.fromkeys(aliases))


def _first_term(query: str, terms: tuple[str, ...]) -> str | None:
    matches = [(query.find(term), term) for term in terms if term in query]
    return min(matches)[1] if matches else None


def normalize_query(query: str) -> NormalizationResult:
    original_query = query
    normalized = unicodedata.normalize("NFKC", query)
    normalized = _WHITESPACE.sub(" ", normalized).strip().lower()
    normalized, aliases = _replace_aliases(normalized)
    model_match = _MODEL_PATTERN.search(normalized)
    detected_model = model_match.group(1).upper() if model_match else None
    return NormalizationResult(
        original_query=original_query,
        normalized_query=normalized,
        detected_model=detected_model,
        detected_component=_first_term(normalized, _COMPONENTS),
        detected_parameter=_first_term(normalized, _PARAMETERS),
        added_aliases=aliases,
    )

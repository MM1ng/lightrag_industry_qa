from __future__ import annotations

from industrial_rag.config import Settings
from industrial_rag.query_normalization import normalize_query


def test_normalization_is_deterministic_and_preserves_original_query() -> None:
    query = "  \uff33\uff35\uff2d\uff2d\uff29\uff34\u30002196  泵轴多久转动一次？  "
    result = normalize_query(query)
    assert result.original_query == query
    assert result.normalized_query == "summit 2196 泵轴如何转动一次?"
    assert result.detected_model == "2196"
    assert result.detected_component == "泵轴"
    assert "怎么/多久→如何" in result.added_aliases


def test_normalization_handles_units_parameters_and_temperature_aliases() -> None:
    result = normalize_query("最高温度 40 摄氏度，额定压力 2 MPa")
    assert result.normalized_query == "最大温度 40 °C,规定压力 2 mpa"
    assert result.detected_parameter in {"温度", "压力"}
    assert "最高→最大" in result.added_aliases
    assert "摄氏度→°C" in result.added_aliases


def test_normalization_does_not_call_an_llm() -> None:
    result = normalize_query("如何更换润滑油？")
    assert result.added_aliases == ()
    assert result.normalized_query == "如何更换润滑油?"


def test_normalization_setting_defaults_off_and_is_environment_controlled() -> None:
    base = {"DASHSCOPE_API_KEY": "test-key"}
    assert Settings.from_mapping(base).query_normalization_enabled is False
    enabled = Settings.from_mapping({**base, "QA_QUERY_NORMALIZATION_ENABLED": "true"})
    assert enabled.query_normalization_enabled is True

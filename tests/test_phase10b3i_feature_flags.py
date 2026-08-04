"""Phase 10B-3I flags are fail-closed and trace-safe."""

from __future__ import annotations

from industrial_rag.config import Settings
from industrial_rag.production_config import ProductionQASettings
from industrial_rag.retrieval_trace import (
    FEATURE_FLAG_TRACE_VERSION,
    feature_flag_retrieval_config,
)
from industrial_rag.version import FEATURE_FLAG_CONFIG_VERSION, version_info


def _values() -> dict[str, str]:
    return {
        "DASHSCOPE_API_KEY": "test-only-key",
        "LLM_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    }


def test_phase10b3i_flags_default_false_and_digest_is_stable() -> None:
    settings = Settings.from_mapping(_values())
    assert settings.phase10b3i_feature_flags == {
        "QA_SUPPORT_VALIDATOR_V2_ENABLED": False,
        "QA_STRUCTURED_GENERATION_ENABLED": False,
        "QA_SUPPLEMENTAL_RETRIEVAL_ENABLED": False,
    }
    assert len(settings.phase10b3i_config_sha256) == 64
    assert settings.phase10b3i_config_sha256 == Settings.from_mapping(_values()).phase10b3i_config_sha256


def test_phase10b3i_flags_parse_independently() -> None:
    settings = Settings.from_mapping(
        {
            **_values(),
            "QA_SUPPORT_VALIDATOR_V2_ENABLED": "true",
            "QA_STRUCTURED_GENERATION_ENABLED": "false",
            "QA_SUPPLEMENTAL_RETRIEVAL_ENABLED": "true",
        }
    )
    assert settings.support_validator_v2_enabled is True
    assert settings.structured_generation_enabled is False
    assert settings.supplemental_retrieval_enabled is True
    assert settings.phase10b3i_config_sha256 != Settings.from_mapping(_values()).phase10b3i_config_sha256


def test_trace_feature_flag_fragment_is_sorted_and_secret_free() -> None:
    fragment = feature_flag_retrieval_config(
        {"QA_Z": True, "QA_A": False}
    )
    assert fragment == (("QA_A", False), ("QA_Z", True))
    metadata = feature_flag_retrieval_config(
        {"QA_A": False}, "a" * 64, include_metadata=True
    )
    assert metadata[-2:] == (
        ("feature_flag_config_sha256", "a" * 64),
        ("feature_flag_config_version", "phase10b3i-feature-flags-v1"),
    )
    assert FEATURE_FLAG_TRACE_VERSION == "phase10b3i-feature-flags-v1"


def test_version_exposes_feature_flag_config_version() -> None:
    assert version_info()["feature_flag_config_version"] == FEATURE_FLAG_CONFIG_VERSION


def test_production_summary_accepts_flags_without_changing_frozen_strategy() -> None:
    config = ProductionQASettings.from_mapping(
        {
            "QA_SUPPORT_VALIDATOR_V2_ENABLED": "true",
            "QA_STRUCTURED_GENERATION_ENABLED": "false",
            "QA_SUPPLEMENTAL_RETRIEVAL_ENABLED": "true",
        }
    )
    assert config.support_validator_v2_enabled is True
    assert config.supplemental_retrieval_enabled is True
    assert config.strategy_hash() == ProductionQASettings().strategy_hash()

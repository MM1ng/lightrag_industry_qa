"""Tests for environment-backed runtime configuration."""

from __future__ import annotations

import pytest
from industrial_rag.config import Settings


def _valid_values() -> dict[str, str]:
    return {
        "DASHSCOPE_API_KEY": "test-only-key",
        "LLM_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "LLM_MODEL": "kimi-k2.6",
        "EMBEDDING_MODEL": "text-embedding-v4",
        "EMBEDDING_DIM": "1024",
        "LIGHTRAG_WORKING_DIR": "./lightrag_storage",
    }


def test_service_api_key_is_optional_and_trimmed() -> None:
    settings = Settings.from_mapping({**_valid_values(), "SERVICE_API_KEY": "  local-key  "})
    assert settings.service_api_key == "local-key"
    assert "local-key" not in repr(settings)


def test_service_api_key_is_none_when_absent() -> None:
    settings = Settings.from_mapping(_valid_values())
    assert settings.service_api_key is None


def test_service_api_key_is_none_when_blank() -> None:
    settings = Settings.from_mapping({**_valid_values(), "SERVICE_API_KEY": "   "})
    assert settings.service_api_key is None


def test_default_model_chain_starts_with_kimi_and_uses_free_fallbacks() -> None:
    settings = Settings.from_mapping(_valid_values())

    assert settings.llm_models == (
        "kimi-k2.6",
        "qwen-plus-2025-07-28",
        "qwen3.6-plus",
        "qwen3.6-flash",
        "qwen-plus",
    )


def test_custom_primary_model_is_allowed_and_removed_from_default_fallbacks() -> None:
    settings = Settings.from_mapping({**_valid_values(), "LLM_MODEL": "qwen-plus"})

    assert settings.llm_models == (
        "qwen-plus",
        "kimi-k2.6",
        "qwen-plus-2025-07-28",
        "qwen3.6-plus",
        "qwen3.6-flash",
    )


def test_direct_settings_constructor_applies_the_same_default_model_chain() -> None:
    settings = Settings(api_key="test-only-key", llm_model="qwen-plus")

    assert settings.llm_models[0] == "qwen-plus"
    assert "qwen-plus" not in settings.llm_fallback_models


@pytest.mark.parametrize(
    "fallbacks, message",
    [
        ("first,,second", "空模型名"),
        ("kimi-k2.6,second", "不能重复"),
        ("first,first", "不能重复"),
    ],
)
def test_model_chain_rejects_empty_or_duplicate_entries(fallbacks: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Settings.from_mapping({**_valid_values(), "LLM_FALLBACK_MODELS": fallbacks})

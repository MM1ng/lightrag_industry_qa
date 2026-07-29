"""Tests for environment-backed runtime configuration."""

from __future__ import annotations

from industrial_rag.config import Settings


def _valid_values() -> dict[str, str]:
    return {
        "DASHSCOPE_API_KEY": "test-only-key",
        "LLM_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "LLM_MODEL": "qwen3.7-plus",
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

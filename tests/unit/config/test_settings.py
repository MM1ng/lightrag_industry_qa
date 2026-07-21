from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from industrial_energy_agent.config.settings import BEIJING_SHARED_BASE_URL, Settings


@pytest.fixture(autouse=True)
def clear_relevant_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LLM_API_KEY",
        "DASHSCOPE_API_KEY",
        "LLM_BASE_URL",
        "CHAT_MODEL",
        "EMBEDDING_MODEL",
        "EMBEDDING_DIMENSION",
        "LLM_MAX_CONCURRENCY",
        "LLM_MAX_INPUT_TOKENS",
        "LLM_MAX_OUTPUT_TOKENS",
        "LIGHTRAG_BASE_URL",
        "LIGHTRAG_API_KEY",
        "LIGHTRAG_TIMEOUT_SECONDS",
        "LIGHTRAG_MAX_RETRIES",
        "STREAMLIT_API_BASE_URL",
        "CORS_ALLOWED_ORIGINS",
        "DATABASE_URL",
        "LANGFUSE_HOST",
        "SERVICE_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


def test_settings_falls_back_to_beijing_shared_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only-dashscope")

    settings = Settings(_env_file=None)

    assert str(settings.llm_base_url).rstrip("/") == BEIJING_SHARED_BASE_URL
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "test-only-dashscope"


def test_explicit_business_workspace_url_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_url = "https://workspace-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    monkeypatch.setenv("LLM_BASE_URL", workspace_url)

    settings = Settings(_env_file=None)

    assert str(settings.llm_base_url).rstrip("/") == workspace_url
    assert settings.llm_base_url_source == "explicit"


def test_explicit_llm_key_takes_precedence_over_dashscope_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-only-explicit")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only-fallback")

    settings = Settings(_env_file=None)

    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "test-only-explicit"


@pytest.mark.parametrize(
    "invalid_url",
    [
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
    ],
)
def test_rejects_resource_endpoint_instead_of_base_url(
    monkeypatch: pytest.MonkeyPatch,
    invalid_url: str,
) -> None:
    monkeypatch.setenv("LLM_BASE_URL", invalid_url)

    with pytest.raises(ValidationError, match="OpenAI-compatible base URL"):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "invalid_url",
    [
        "http://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://dashscope.aliyuncs.com/compatible-mode/v1?tenant=secret",
        "https://dashscope.aliyuncs.com/compatible-mode/v1#fragment",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/extra",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "https://workspace.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        "https://workspace.cn-beijing.maas.aliyuncs.com.evil.example/compatible-mode/v1",
        "https://user:never-show@dashscope.aliyuncs.com/compatible-mode/v1",
    ],
)
def test_llm_base_url_accepts_only_exact_beijing_endpoints(
    monkeypatch: pytest.MonkeyPatch,
    invalid_url: str,
) -> None:
    monkeypatch.setenv("LLM_BASE_URL", invalid_url)

    with pytest.raises(ValidationError, match="Beijing OpenAI-compatible base URL") as error:
        Settings(_env_file=None)

    assert "never-show" not in str(error.value)


def test_cors_origins_are_origin_strings_without_trailing_slashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        "http://127.0.0.1:8501/,https://example.com/",
    )

    settings = Settings(_env_file=None)

    assert settings.cors_allowed_origins == (
        "http://127.0.0.1:8501",
        "https://example.com",
    )
    assert all(isinstance(origin, str) for origin in settings.cors_allowed_origins)


def test_cors_removes_explicit_default_ports_but_preserves_custom_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        "http://example.com:80,https://example.com:443,http://example.com:8080",
    )

    settings = Settings(_env_file=None)

    assert settings.cors_allowed_origins == (
        "http://example.com",
        "https://example.com",
        "http://example.com:8080",
    )


@pytest.mark.parametrize(
    "invalid_origin",
    [
        "https://user:never-show@example.com",
        "https://example.com/app",
        "https://example.com?tenant=secret",
        "https://example.com#fragment",
    ],
)
def test_cors_rejects_values_that_are_not_strict_origins(
    monkeypatch: pytest.MonkeyPatch,
    invalid_origin: str,
) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", invalid_origin)

    with pytest.raises(ValidationError, match="CORS_ALLOWED_ORIGINS") as error:
        Settings(_env_file=None)

    assert "never-show" not in str(error.value)


@pytest.mark.parametrize(
    ("environment_name", "invalid_url"),
    [
        ("LIGHTRAG_BASE_URL", "http://user:never-show@127.0.0.1:9621"),
        ("LIGHTRAG_BASE_URL", "http://127.0.0.1:9621?tenant=secret"),
        ("STREAMLIT_API_BASE_URL", "http://127.0.0.1:8000#fragment"),
        ("LANGFUSE_HOST", "https://user:never-show@example.com"),
        ("DATABASE_URL", "postgresql://user:never-show@example.com/app"),
    ],
)
def test_non_secret_urls_reject_embedded_credentials_and_unsafe_components(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
    invalid_url: str,
) -> None:
    monkeypatch.setenv(environment_name, invalid_url)

    with pytest.raises(ValidationError, match="must not contain") as error:
        Settings(_env_file=None)

    assert "never-show" not in str(error.value)


@pytest.mark.parametrize(
    "invalid_database_url",
    [
        "postgresql://example.com/energyops",
        "sqlite:///data/processed/energyops.sqlite3?password=never-show",
        "sqlite:///data/processed/energyops.sqlite3#never-show",
        "sqlite://user:never-show@example.com/energyops.sqlite3",
    ],
)
def test_database_url_is_sqlite_only_and_carries_no_url_secrets(
    monkeypatch: pytest.MonkeyPatch,
    invalid_database_url: str,
) -> None:
    monkeypatch.setenv("DATABASE_URL", invalid_database_url)

    with pytest.raises(ValidationError, match="SQLite URL without credentials") as error:
        Settings(_env_file=None)

    assert "never-show" not in str(error.value)


def test_checked_in_env_example_accepts_empty_optional_langfuse_host() -> None:
    project_root = Path(__file__).resolve().parents[3]

    settings = Settings(_env_file=project_root / ".env.example")

    assert settings.langfuse_host is None


def test_bailian_defaults_are_locked() -> None:
    settings = Settings(_env_file=None)

    assert settings.chat_model == "qwen3.7-plus"
    assert settings.embedding_model == "text-embedding-v4"
    assert settings.embedding_dimension == 1024
    assert settings.llm_max_concurrency == 4
    assert settings.llm_max_input_tokens == 32768
    assert settings.llm_max_output_tokens == 4096


def test_lightrag_rest_defaults_are_bounded() -> None:
    settings = Settings(_env_file=None)

    assert str(settings.lightrag_base_url).rstrip("/") == "http://127.0.0.1:9621"
    assert settings.lightrag_timeout_seconds == 60
    assert settings.lightrag_max_retries == 2


def test_lightrag_service_key_must_differ_from_model_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-only-shared-secret")
    monkeypatch.setenv("LIGHTRAG_API_KEY", "test-only-shared-secret")

    with pytest.raises(ValidationError, match="must differ") as error:
        Settings(_env_file=None)

    assert "test-only-shared-secret" not in str(error.value)


@pytest.mark.parametrize(
    "environment_name",
    ["LLM_MAX_CONCURRENCY", "LLM_MAX_INPUT_TOKENS", "LLM_MAX_OUTPUT_TOKENS"],
)
def test_llm_resource_limits_reject_zero(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
) -> None:
    monkeypatch.setenv(environment_name, "0")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_repr_does_not_reveal_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "test-only-never-render"
    monkeypatch.setenv("LLM_API_KEY", secret)

    settings = Settings(_env_file=None)

    assert secret not in repr(settings)
    assert secret not in str(settings)

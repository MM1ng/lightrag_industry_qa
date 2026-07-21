"""Typed application settings with explicit secret and endpoint boundaries."""

from __future__ import annotations

import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BEIJING_SHARED_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

_OPENAI_COMPATIBLE_PATH = "/compatible-mode/v1"
_BEIJING_WORKSPACE_HOST = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cn-beijing\.maas\.aliyuncs\.com$"
)
_DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:8501",
    "http://localhost:8501",
)


def _normalize_cors_origin(raw_origin: object) -> str:
    origin = str(raw_origin).strip()
    try:
        validated = AnyHttpUrl(origin)
        parsed = urlsplit(origin)
        explicit_port = parsed.port
    except (TypeError, ValueError) as error:
        raise ValueError("CORS_ALLOWED_ORIGINS must contain valid HTTP origins") from error

    if (
        validated.scheme not in {"http", "https"}
        or validated.username is not None
        or validated.password is not None
        or validated.query is not None
        or validated.fragment is not None
        or (validated.path or "") not in {"", "/"}
    ):
        raise ValueError(
            "CORS_ALLOWED_ORIGINS must contain origins without credentials, path, query, or fragment"
        )

    host = validated.host
    if host is None:
        raise ValueError("CORS_ALLOWED_ORIGINS must contain valid HTTP origins")
    is_default_port = (validated.scheme == "http" and explicit_port == 80) or (
        validated.scheme == "https" and explicit_port == 443
    )
    port = f":{explicit_port}" if explicit_port is not None and not is_default_port else ""
    return f"{validated.scheme}://{host}{port}"


class Settings(BaseSettings):
    """Single source of truth for process configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
        hide_input_in_errors=True,
    )

    explicit_llm_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="LLM_API_KEY",
        repr=False,
        exclude=True,
    )
    dashscope_api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    llm_base_url: AnyHttpUrl = Field(default=AnyHttpUrl(BEIJING_SHARED_BASE_URL), repr=False)
    chat_model: str = "qwen3.7-plus"
    embedding_model: str = "text-embedding-v4"
    embedding_dimension: int = Field(default=1024, ge=1)
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    llm_max_concurrency: int = Field(default=4, ge=1, le=32)
    llm_max_input_tokens: int = Field(default=32768, ge=1, le=1_000_000)
    llm_max_output_tokens: int = Field(default=4096, ge=1, le=65_536)

    lightrag_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:9621")
    lightrag_api_key: SecretStr | None = Field(default=None, repr=False)
    lightrag_timeout_seconds: float = Field(default=60.0, gt=0, le=600)

    database_url: str = Field(
        default="sqlite:///data/processed/energyops.sqlite3",
        repr=False,
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    streamlit_api_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8000")
    service_token: SecretStr | None = Field(default=None, repr=False)
    cors_allowed_origins: Annotated[tuple[str, ...], NoDecode] = _DEFAULT_CORS_ORIGINS

    langfuse_public_key: SecretStr | None = Field(default=None, repr=False)
    langfuse_secret_key: SecretStr | None = Field(default=None, repr=False)
    langfuse_host: AnyHttpUrl | None = None

    @field_validator(
        "explicit_llm_api_key",
        "dashscope_api_key",
        "lightrag_api_key",
        "service_token",
        "langfuse_public_key",
        "langfuse_secret_key",
        mode="before",
    )
    @classmethod
    def empty_secret_is_unset(cls, value: object) -> object:
        """Treat empty ``.env`` placeholders as absent credentials."""

        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("langfuse_host", mode="before")
    @classmethod
    def empty_optional_url_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("llm_base_url")
    @classmethod
    def require_beijing_openai_compatible_base_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        host = (value.host or "").casefold()
        valid_host = host == "dashscope.aliyuncs.com" or bool(
            _BEIJING_WORKSPACE_HOST.fullmatch(host)
        )
        if (
            value.scheme != "https"
            or not valid_host
            or value.username is not None
            or value.password is not None
            or value.query is not None
            or value.fragment is not None
            or (value.path or "").rstrip("/") != _OPENAI_COMPATIBLE_PATH
        ):
            raise ValueError(
                "LLM_BASE_URL must be a Beijing OpenAI-compatible base URL with the exact "
                f"{_OPENAI_COMPATIBLE_PATH} path"
            )
        return AnyHttpUrl(str(value).rstrip("/"))

    @field_validator("lightrag_base_url", "streamlit_api_base_url", "langfuse_host")
    @classmethod
    def reject_unsafe_service_base_url(
        cls,
        value: AnyHttpUrl | None,
    ) -> AnyHttpUrl | None:
        if value is not None and (
            value.username is not None
            or value.password is not None
            or value.query is not None
            or value.fragment is not None
        ):
            raise ValueError(
                "Service base URLs must not contain username, password, query, or fragment"
            )
        return value

    @field_validator("database_url")
    @classmethod
    def require_local_sqlite_database_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme.casefold() != "sqlite"
            or bool(parsed.netloc)
            or parsed.username is not None
            or parsed.password is not None
            or bool(parsed.query)
            or bool(parsed.fragment)
        ):
            raise ValueError(
                "DATABASE_URL must be a local SQLite URL without credentials, query, or fragment; "
                "it must not contain connection secrets"
            )
        return value

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> tuple[str, ...]:
        raw_origins: object
        if isinstance(value, str):
            raw_origins = tuple(part.strip() for part in value.split(",") if part.strip())
        else:
            raw_origins = value
        if not isinstance(raw_origins, (list, tuple)) or not raw_origins:
            raise ValueError("CORS_ALLOWED_ORIGINS must contain at least one HTTP origin")
        return tuple(_normalize_cors_origin(origin) for origin in raw_origins)

    @property
    def llm_api_key(self) -> SecretStr | None:
        """Return the explicit key, or the process-only DashScope fallback."""

        return self.explicit_llm_api_key or self.dashscope_api_key

    @property
    def llm_base_url_source(self) -> Literal["explicit", "beijing_shared_fallback"]:
        """Return safe endpoint provenance without exposing the configured URL."""

        if "llm_base_url" in self.model_fields_set:
            return "explicit"
        return "beijing_shared_fallback"

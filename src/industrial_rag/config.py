"""Environment-only configuration and non-destructive index compatibility checks."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from industrial_rag.vector_collections import VectorBackend

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BAILIAN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
SUPPORTED_QUERY_MODES = (
    "mix",
    "hybrid",
    "local",
    "global",
    "naive",
)
INDEX_METADATA_FILENAME = "industrial_rag_index.json"
DEFAULT_LLM_MODELS = (
    "kimi-k2.6",
    "qwen3.6-plus",
    "qwen3.6-flash",
    "qwen-plus",
    "qwen3.5-flash-2026-02-23",
)


class StorageCompatibilityError(RuntimeError):
    """Existing LightRAG data cannot safely be reused by this embedding configuration."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated runtime settings for the single LightRAG knowledge base."""

    api_key: str = field(repr=False)
    service_api_key: str | None = field(default=None, repr=False)
    llm_base_url: str = DEFAULT_BAILIAN_BASE_URL
    llm_model: str = "kimi-k2.6"
    llm_fallback_models: tuple[str, ...] = ()
    model_fallback_enabled: bool = True
    embedding_model: str = "text-embedding-v4"
    embedding_dim: int = 1024
    chunk_token_size: int = 1600
    working_dir: Path = PROJECT_ROOT / "lightrag_storage"
    vector_backend: VectorBackend = VectorBackend.nano
    qdrant_url: str | None = None
    qdrant_api_key: str | None = field(default=None, repr=False)
    qdrant_collection_prefix: str = "ira_qdrant"
    qdrant_generation: str | None = None
    qdrant_kb_id: str | None = None
    # LightRAG per-generation isolation token. Derived per knowledge base by
    # settings_for_knowledge_base(); None keeps the legacy layout (workspace="")
    # used by pre-generation knowledge bases.
    vector_workspace: str | None = None
    mineru_enabled: bool = False
    mineru_api_base_url: str = "https://mineru.net"
    mineru_api_key: str | None = field(default=None, repr=False)
    mineru_api_version: str = "v4"
    mineru_request_timeout: float = 60.0
    mineru_task_timeout: float = 600.0
    mineru_poll_interval: float = 3.0
    mineru_max_retries: int = 3
    mineru_fallback_to_pymupdf: bool = True
    mineru_save_raw_response: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.vector_backend, VectorBackend):
            object.__setattr__(self, "vector_backend", VectorBackend(self.vector_backend))
        if self.vector_backend is VectorBackend.qdrant and not self.qdrant_url:
            raise ValueError("VECTOR_BACKEND=qdrant 时必须配置 QDRANT_URL")
        if not self.llm_model.strip():
            raise ValueError("LLM_MODEL 不能为空")
        if not self.llm_fallback_models:
            object.__setattr__(
                self,
                "llm_fallback_models",
                tuple(model for model in DEFAULT_LLM_MODELS if model != self.llm_model),
            )
        if not all(model.strip() for model in self.llm_fallback_models):
            raise ValueError("LLM_FALLBACK_MODELS 不能包含空模型名")
        if len(set(self.llm_models)) != len(self.llm_models):
            raise ValueError("LLM_MODEL 与 LLM_FALLBACK_MODELS 不能重复")

    @property
    def llm_models(self) -> tuple[str, ...]:
        """Ordered model chain, starting with the currently preferred model."""

        return (self.llm_model, *self.llm_fallback_models)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str | None]) -> Settings:
        api_key = (values.get("DASHSCOPE_API_KEY") or "").strip()
        service_api_key = (values.get("SERVICE_API_KEY") or "").strip() or None
        base_url = (values.get("LLM_BASE_URL") or DEFAULT_BAILIAN_BASE_URL).rstrip("/")
        llm_model = (values.get("LLM_MODEL") or DEFAULT_LLM_MODELS[0]).strip()
        model_fallback_enabled = (
            (values.get("MODEL_FALLBACK_ENABLED") or "true").strip().lower() != "false"
        )
        fallback_value = values.get("LLM_FALLBACK_MODELS")
        if fallback_value is None or not fallback_value.strip():
            llm_fallback_models = tuple(model for model in DEFAULT_LLM_MODELS if model != llm_model)
        else:
            llm_fallback_models = tuple(model.strip() for model in fallback_value.split(","))
        embedding_model = (values.get("EMBEDDING_MODEL") or "text-embedding-v4").strip()
        try:
            embedding_dim = int(values.get("EMBEDDING_DIM") or "1024")
        except ValueError as error:
            raise ValueError("EMBEDDING_DIM 必须是整数") from error
        try:
            chunk_token_size = int(values.get("LIGHTRAG_CHUNK_TOKEN_SIZE") or "1600")
        except ValueError as error:
            raise ValueError("LIGHTRAG_CHUNK_TOKEN_SIZE 必须是整数") from error
        if chunk_token_size < 512:
            raise ValueError("LIGHTRAG_CHUNK_TOKEN_SIZE 不能小于 512")
        raw_working_dir = values.get("LIGHTRAG_WORKING_DIR") or "./lightrag_storage"
        working_dir = Path(raw_working_dir)
        if not working_dir.is_absolute():
            working_dir = PROJECT_ROOT / working_dir
        raw_vector_backend = (values.get("VECTOR_BACKEND") or VectorBackend.nano.value).strip().lower()
        try:
            vector_backend = VectorBackend(raw_vector_backend)
        except ValueError as error:
            raise ValueError("VECTOR_BACKEND 必须为 nano 或 qdrant") from error
        qdrant_url = (values.get("QDRANT_URL") or "").strip().rstrip("/") or None
        qdrant_api_key = (values.get("QDRANT_API_KEY") or "").strip() or None
        qdrant_collection_prefix = (
            values.get("QDRANT_COLLECTION_PREFIX") or "ira_qdrant"
        ).strip()
        qdrant_generation = (values.get("QDRANT_GENERATION") or "").strip() or None
        qdrant_kb_id = (values.get("QDRANT_KB_ID") or "").strip() or None

        if not api_key:
            raise ValueError("必须通过环境变量 DASHSCOPE_API_KEY 提供百炼密钥")
        if base_url != DEFAULT_BAILIAN_BASE_URL:
            raise ValueError("LLM_BASE_URL 必须使用阿里云百炼北京 OpenAI 兼容端点")
        if embedding_model != "text-embedding-v4":
            raise ValueError("EMBEDDING_MODEL 必须为 text-embedding-v4")
        if embedding_dim != 1024:
            raise ValueError("EMBEDDING_DIM 必须为 1024")

        # MinerU config — defaults to disabled
        mineru_enabled = (values.get("MINERU_ENABLED") or "").strip().lower() == "true"
        mineru_api_base_url = (values.get("MINERU_API_BASE_URL") or "https://mineru.net").rstrip("/")
        mineru_api_key = (values.get("MINERU_API_KEY") or "").strip() or None
        mineru_api_version = (values.get("MINERU_API_VERSION") or "v4").strip()
        try:
            mineru_request_timeout = float(values.get("MINERU_REQUEST_TIMEOUT_SECONDS") or "60")
        except ValueError:
            mineru_request_timeout = 60.0
        try:
            mineru_task_timeout = float(values.get("MINERU_TASK_TIMEOUT_SECONDS") or "600")
        except ValueError:
            mineru_task_timeout = 600.0
        try:
            mineru_poll_interval = float(values.get("MINERU_POLL_INTERVAL_SECONDS") or "3")
        except ValueError:
            mineru_poll_interval = 3.0
        try:
            mineru_max_retries = int(values.get("MINERU_MAX_RETRIES") or "3")
        except ValueError:
            mineru_max_retries = 3
        mineru_fallback_to_pymupdf = (values.get("MINERU_FALLBACK_TO_PYMUPDF") or "true").strip().lower() != "false"
        mineru_save_raw_response = (values.get("MINERU_SAVE_RAW_RESPONSE") or "true").strip().lower() != "false"

        return cls(
            api_key=api_key,
            service_api_key=service_api_key,
            llm_base_url=base_url,
            llm_model=llm_model,
            llm_fallback_models=llm_fallback_models,
            model_fallback_enabled=model_fallback_enabled,
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
            chunk_token_size=chunk_token_size,
            working_dir=working_dir.resolve(),
            vector_backend=vector_backend,
            qdrant_url=qdrant_url,
            qdrant_api_key=qdrant_api_key,
            qdrant_collection_prefix=qdrant_collection_prefix,
            qdrant_generation=qdrant_generation,
            qdrant_kb_id=qdrant_kb_id,
            mineru_enabled=mineru_enabled,
            mineru_api_base_url=mineru_api_base_url,
            mineru_api_key=mineru_api_key,
            mineru_api_version=mineru_api_version,
            mineru_request_timeout=mineru_request_timeout,
            mineru_task_timeout=mineru_task_timeout,
            mineru_poll_interval=mineru_poll_interval,
            mineru_max_retries=mineru_max_retries,
            mineru_fallback_to_pymupdf=mineru_fallback_to_pymupdf,
            mineru_save_raw_response=mineru_save_raw_response,
        )

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        return cls.from_mapping(os.environ)


def check_storage_compatibility(
    storage_dir: Path, embedding_model: str, embedding_dim: int
) -> None:
    """Reject unknown or dimension-mismatched storage without deleting user data."""

    if not storage_dir.exists():
        return
    marker = storage_dir / INDEX_METADATA_FILENAME
    if not marker.exists():
        existing = [path for path in storage_dir.iterdir() if path.name != ".gitkeep"]
        if existing:
            raise StorageCompatibilityError(
                "现有 lightrag_storage 缺少本项目的维度标记；请先备份并手动重建该目录。"
            )
        return
    try:
        metadata = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StorageCompatibilityError(
            "索引维度标记不可读；请先备份并手动重建 lightrag_storage。"
        ) from error
    if (
        metadata.get("embedding_model") != embedding_model
        or metadata.get("embedding_dim") != embedding_dim
    ):
        raise StorageCompatibilityError(
            "现有索引的 Embedding 模型或维度不一致；请先备份并手动重建 lightrag_storage。"
        )


def write_storage_metadata(storage_dir: Path, embedding_model: str, embedding_dim: int) -> None:
    storage_dir.mkdir(parents=True, exist_ok=True)
    marker = storage_dir / INDEX_METADATA_FILENAME
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {"embedding_model": embedding_model, "embedding_dim": embedding_dim},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(marker)

"""Environment-only configuration and non-destructive index compatibility checks."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BAILIAN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
SUPPORTED_QUERY_MODES = ("mix", "local", "global", "naive")
INDEX_METADATA_FILENAME = "industrial_rag_index.json"


class StorageCompatibilityError(RuntimeError):
    """Existing LightRAG data cannot safely be reused by this embedding configuration."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated runtime settings for the single LightRAG knowledge base."""

    api_key: str = field(repr=False)
    llm_base_url: str = DEFAULT_BAILIAN_BASE_URL
    llm_model: str = "qwen3.7-plus"
    embedding_model: str = "text-embedding-v4"
    embedding_dim: int = 1024
    working_dir: Path = PROJECT_ROOT / "lightrag_storage"

    @classmethod
    def from_mapping(cls, values: Mapping[str, str | None]) -> Settings:
        api_key = (values.get("DASHSCOPE_API_KEY") or "").strip()
        base_url = (values.get("LLM_BASE_URL") or DEFAULT_BAILIAN_BASE_URL).rstrip("/")
        llm_model = (values.get("LLM_MODEL") or "qwen3.7-plus").strip()
        embedding_model = (values.get("EMBEDDING_MODEL") or "text-embedding-v4").strip()
        try:
            embedding_dim = int(values.get("EMBEDDING_DIM") or "1024")
        except ValueError as error:
            raise ValueError("EMBEDDING_DIM 必须是整数") from error
        raw_working_dir = values.get("LIGHTRAG_WORKING_DIR") or "./lightrag_storage"
        working_dir = Path(raw_working_dir)
        if not working_dir.is_absolute():
            working_dir = PROJECT_ROOT / working_dir

        if not api_key:
            raise ValueError("必须通过环境变量 DASHSCOPE_API_KEY 提供百炼密钥")
        if base_url != DEFAULT_BAILIAN_BASE_URL:
            raise ValueError("LLM_BASE_URL 必须使用阿里云百炼北京 OpenAI 兼容端点")
        if llm_model != "qwen3.7-plus":
            raise ValueError("LLM_MODEL 必须为 qwen3.7-plus")
        if embedding_model != "text-embedding-v4":
            raise ValueError("EMBEDDING_MODEL 必须为 text-embedding-v4")
        if embedding_dim != 1024:
            raise ValueError("EMBEDDING_DIM 必须为 1024")
        return cls(
            api_key=api_key,
            llm_base_url=base_url,
            llm_model=llm_model,
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
            working_dir=working_dir.resolve(),
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

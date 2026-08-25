from __future__ import annotations

from types import SimpleNamespace

import pytest
from industrial_rag.config import Settings
from industrial_rag.kb_runtime_settings import settings_for_knowledge_base
from industrial_rag.vector_collections import VectorBackend


def _kb(**values: object) -> SimpleNamespace:
    defaults = {
        "id": "a" * 32,
        "workspace_path": "C:/tmp/kb/lightrag",
        "embedding_model": "text-embedding-v4",
        "embedding_dimension": 1024,
        "vector_backend": "nano",
        "active_vector_generation": None,
        "active_vector_generation_id": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def _base() -> Settings:
    return Settings.from_mapping({"DASHSCOPE_API_KEY": "test-key"})


def test_runtime_settings_preserve_legacy_nano_workspace() -> None:
    settings = settings_for_knowledge_base(_base(), _kb())

    assert settings.vector_backend is VectorBackend.nano
    assert settings.working_dir.name == "lightrag"
    assert settings.qdrant_generation is None


def test_runtime_settings_select_qdrant_generation_from_active_generation_record() -> None:
    base = Settings.from_mapping(
        {
            "DASHSCOPE_API_KEY": "test-key",
            "QDRANT_URL": "http://qdrant.test:6333",
        }
    )
    generation = SimpleNamespace(generation="g20260731abc", workspace_path="C:/tmp/qdrant/workspace")
    settings = settings_for_knowledge_base(
        base,
        _kb(vector_backend="qdrant", active_vector_generation=generation),
    )

    assert settings.vector_backend is VectorBackend.qdrant
    assert settings.qdrant_kb_id == "a" * 32
    assert settings.qdrant_generation == "g20260731abc"
    assert settings.working_dir.name == "workspace"


def test_runtime_settings_reject_qdrant_knowledge_base_without_configured_url() -> None:
    with pytest.raises(RuntimeError, match="QDRANT_URL"):
        settings_for_knowledge_base(
            _base(),
            _kb(vector_backend="qdrant", active_vector_generation=SimpleNamespace(generation="g20260731abc", workspace_path="C:/tmp/qdrant/workspace")),
        )

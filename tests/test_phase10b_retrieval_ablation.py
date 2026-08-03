from __future__ import annotations

import pytest
from industrial_rag.config import Settings
from industrial_rag.phase10b_experiment_manifest import (
    SUPPORTED_ABLATION_MODES,
    AblationConfig,
)


def test_ablation_config_allows_only_supported_lightrag_modes() -> None:
    config = AblationConfig(experiment_id="mix", query_mode="mix", top_k=12, chunk_top_k=20)
    assert config.query_mode in SUPPORTED_ABLATION_MODES
    assert config.changed_variables == ()
    with pytest.raises(ValueError, match="query_mode"):
        AblationConfig(experiment_id="keyword", query_mode="keyword", top_k=12, chunk_top_k=20)


def test_ablation_config_requires_single_explicit_variable_change() -> None:
    config = AblationConfig(experiment_id="topk8", query_mode="mix", top_k=8, chunk_top_k=20)
    assert config.changed_variables == ("top_k",)
    with pytest.raises(ValueError, match="one variable"):
        AblationConfig(experiment_id="invalid", query_mode="hybrid", top_k=8, chunk_top_k=20)


def test_phase10b_query_settings_are_environment_controlled_and_default_to_baseline() -> None:
    base = {"DASHSCOPE_API_KEY": "test-key"}
    defaults = Settings.from_mapping(base)
    assert defaults.phase10b_query_mode == "mix"
    assert defaults.phase10b_top_k == 12
    assert defaults.phase10b_chunk_top_k == 20
    configured = Settings.from_mapping(
        {
            **base,
            "PHASE10B_QUERY_MODE": "hybrid",
            "PHASE10B_TOP_K": "8",
            "PHASE10B_CHUNK_TOP_K": "16",
        }
    )
    assert configured.phase10b_query_mode == "hybrid"
    assert configured.phase10b_top_k == 8
    assert configured.phase10b_chunk_top_k == 16

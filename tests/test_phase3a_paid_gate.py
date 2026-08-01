"""Phase 3A-D: paid-run gate, frozen artifacts, and exact-match cache tests."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.experiments.parser_backend.fixed_model_gate import (
    config_hashes,
    load_frozen_config,
)
from evaluation.experiments.parser_backend.fixed_model_llm import FixedModelLLM
from evaluation.experiments.parser_backend.paid_run_gate import (
    FIXED_MODEL,
    FROZEN_MANIFEST_PATH,
    PAID_RUN_ENV,
    check_paid_run_gate,
    scrub_secrets,
    verify_frozen_artifacts,
)


def _env(**overrides) -> dict[str, str]:
    env = {
        "LLM_MODEL": FIXED_MODEL,
        "MODEL_FALLBACK_ENABLED": "false",
        "QDRANT_COLLECTION_PREFIX": "ira_p3ar_test1234",
        PAID_RUN_ENV: "1",
    }
    env.update(overrides)
    return env


# ---------------------------------------------------------------------------
# parser_pipeline single-variable declaration
# ---------------------------------------------------------------------------


def test_frozen_config_declares_parser_pipeline_as_only_variable() -> None:
    cfg = load_frozen_config()
    assert cfg["only_independent_variable"] == "parser_pipeline"
    assert cfg["p0_parser_pipeline"] == "pymupdf_standard_adapter"
    assert cfg["p1_parser_pipeline"] == "mineru_online_clean_adapter"
    assert cfg["p0_parser_pipeline"] != cfg["p1_parser_pipeline"]


def test_changing_downstream_config_changes_hash() -> None:
    cfg = load_frozen_config()
    base = config_hashes("pymupdf_standard_adapter")
    modified = json.loads(json.dumps(cfg))
    modified["top_k"] = 5
    changed = config_hashes("pymupdf_standard_adapter", cfg=modified)
    assert base["retrieval_config_hash"] != changed["retrieval_config_hash"]


# ---------------------------------------------------------------------------
# Frozen artifacts
# ---------------------------------------------------------------------------


def test_frozen_manifest_exists_and_is_immutable() -> None:
    assert FROZEN_MANIFEST_PATH.is_file()
    manifest = json.loads(FROZEN_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["entries"]
    assert all(entry["immutable"] is True for entry in manifest["entries"])
    roles = {entry["role"] for entry in manifest["entries"]}
    assert {"p0_children", "p1_clean_children", "mineru_raw_zip", "frozen_config", "prompt_bundle"} <= roles


def test_frozen_artifacts_verify_ok_on_disk() -> None:
    result = verify_frozen_artifacts()
    assert result["ok"] is True
    assert result["checked"] == 25


def test_frozen_artifacts_detect_tamper(tmp_path: Path) -> None:
    manifest = json.loads(FROZEN_MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = dict(manifest["entries"][0])
    entry["sha256"] = "0" * 64
    tampered = {"entries": [entry]}
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    result = verify_frozen_artifacts(path)
    assert result["ok"] is False
    assert result["mismatches"][0]["issue"] == "sha256_mismatch"


# ---------------------------------------------------------------------------
# Paid-run gate
# ---------------------------------------------------------------------------


def test_gate_refuses_without_paid_run_env() -> None:
    env = _env()
    env.pop(PAID_RUN_ENV)
    result = check_paid_run_gate(env)
    assert result["allowed"] is False
    assert result["checks"]["paid_run_env"] is False


def test_gate_refuses_wrong_model() -> None:
    result = check_paid_run_gate(_env(LLM_MODEL="qwen-plus"))
    assert result["allowed"] is False
    assert result["checks"]["llm_model_fixed"] is False


def test_gate_refuses_fallback_enabled() -> None:
    result = check_paid_run_gate(_env(MODEL_FALLBACK_ENABLED="true"))
    assert result["allowed"] is False
    assert result["checks"]["fallback_disabled"] is False


def test_gate_refuses_changed_frozen_artifacts(tmp_path: Path, monkeypatch) -> None:
    # Point the gate at a tampered manifest copy.
    manifest = json.loads(FROZEN_MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = dict(manifest["entries"][0])
    entry["sha256"] = "0" * 64
    tampered = {"entries": [entry]}
    tmp = tmp_path / "tampered.json"
    tmp.write_text(json.dumps(tampered), encoding="utf-8")
    monkeypatch.setattr("evaluation.experiments.parser_backend.paid_run_gate.FROZEN_MANIFEST_PATH", tmp)
    result = check_paid_run_gate(_env())
    assert result["allowed"] is False
    assert result["checks"]["frozen_artifacts_unchanged"] is False


def test_gate_requires_random_test_prefix() -> None:
    result = check_paid_run_gate(_env(QDRANT_COLLECTION_PREFIX="ira_prod_prefix"))
    assert result["checks"]["random_prefix_ready"] is False


# ---------------------------------------------------------------------------
# Secret scrubbing and exact-match cache
# ---------------------------------------------------------------------------


def test_scrub_secrets_removes_api_key_and_signed_url() -> None:
    text = "key=sk-test1234567890 url=https://cdn/x?X-OSS-AccessKeyId=abc&sig=xyz"
    cleaned = scrub_secrets(text, api_key="sk-test1234567890")
    assert "sk-test1234567890" not in cleaned
    assert "X-OSS" not in cleaned
    assert "[REDACTED_API_KEY]" in cleaned


def test_cache_hit_only_for_exact_match(tmp_path: Path) -> None:
    cache = tmp_path / "cache.jsonl"
    llm = FixedModelLLM(
        model=FIXED_MODEL,
        api_key="test-key",
        base_url="https://example.invalid",
        cache_path=cache,
        config_hash="cfg",
    )
    key = llm._cache_key("sys", "prompt")
    cache.write_text(
        json.dumps(
            {
                "key": key,
                "content": "cached-answer",
                "input_tokens": 10,
                "output_tokens": 2,
                "total_tokens": 12,
                "model": FIXED_MODEL,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    llm2 = FixedModelLLM(
        model=FIXED_MODEL,
        api_key="test-key",
        base_url="https://example.invalid",
        cache_path=cache,
        config_hash="cfg",
    )
    import asyncio

    answer = asyncio.run(llm2("prompt", system_prompt="sys"))
    assert answer == "cached-answer"
    assert llm2.cache_hits == 1
    assert llm2.calls[0]["status"] == "cache_hit"
    assert llm2.calls[0]["cache_hit"] is True
    # Cache hits replay the original usage for cost accounting.
    assert llm2.calls[0]["total_tokens"] == 12
    assert llm2.calls[0]["cached_total_tokens"] == 12


def test_cache_key_differs_across_prompt_and_model() -> None:
    llm = FixedModelLLM(model=FIXED_MODEL, api_key="k", base_url="https://x")
    key_a = llm._cache_key("sys", "q1")
    key_b = llm._cache_key("sys", "q2")
    key_c = llm._cache_key("other", "q1")
    assert key_a != key_b != key_c

"""Frozen-config consistency gate for the Phase 3A-R fixed-model experiment."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT
from .common import sha256_file

EXPERIMENT_ROOT = Path(__file__).resolve().parent
FROZEN_CONFIG_PATH = EXPERIMENT_ROOT / "fixed_model" / "config.json"
GOLDEN_SET_PATH = PROJECT_ROOT / "data" / "evaluation" / "industrial_pump_golden_set_50.jsonl"
GOLDEN_SHA256 = "fc52600fcce019d7f3cab04e0d0306ce336c468873ba2aef44391cc863e37aaf"


def load_frozen_config() -> dict[str, Any]:
    return json.loads(FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def config_hashes(parser_pipeline: str, *, cfg: dict[str, Any] | None = None) -> dict[str, str]:
    """Compute the eight consistency hashes for one parser pipeline group."""
    cfg = cfg or load_frozen_config()
    if cfg.get("only_independent_variable") != "parser_pipeline":
        raise RuntimeError("frozen config must declare parser_pipeline as the only variable")
    chunker = cfg["chunker"]
    chunk_config = {
        "strategy": chunker["strategy"],
        "version": "0.1.0",
        "parent_target_tokens": chunker["parent_target_tokens"],
        "parent_max_tokens": chunker["parent_max_tokens"],
        "child_target_tokens": chunker["child_target_tokens"],
        "child_min_tokens": chunker["child_min_tokens"],
        "child_max_tokens": chunker["child_max_tokens"],
        "child_overlap_tokens": chunker["child_overlap_tokens"],
        "merge_small_children": chunker["merge_small_children"],
        "chunk_token_size": cfg["chunk_token_size"],
        "tokenizer": "tiktoken-cl100k_base",
    }
    embedding_config = {
        "model": cfg["embedding_model"],
        "dimension": cfg["embedding_dimension"],
        "max_token_size": 8192,
        "send_dimensions": True,
        "supports_asymmetric": True,
        "batch_num": cfg["qdrant"]["embedding_batch_num"],
    }
    index_llm_config = {
        "model": cfg["index_llm_model"],
        "fallback_enabled": cfg["model_fallback_enabled"],
        "enable_thinking": cfg["enable_thinking"],
        "max_gleaning": cfg["lightrag"]["entity_extract_max_gleaning"],
        "max_records": cfg["lightrag"]["entity_extract_max_records"],
        "max_entities": cfg["lightrag"]["entity_extract_max_entities"],
        "max_parallel_insert": cfg["lightrag"]["max_parallel_insert"],
    }
    query_llm_config = {
        "model": cfg["query_llm_model"],
        "fallback_enabled": cfg["model_fallback_enabled"],
        "enable_thinking": cfg["enable_thinking"],
    }
    from industrial_rag.lightrag_service import (
        INSUFFICIENT_EVIDENCE_MESSAGE,
        _SELECTED_CONTEXT_LABEL,
        _SYSTEM_PROMPT_BASE,
    )

    prompt_bundle = {
        "system_prompt_base": _SYSTEM_PROMPT_BASE,
        "selected_context_label": _SELECTED_CONTEXT_LABEL,
        "insufficient_evidence_message": INSUFFICIENT_EVIDENCE_MESSAGE,
        "evidence_limit": cfg["evidence_limit"],
        "evidence_policy": "select_evidence",
    }
    retrieval_config = {
        "mode": cfg["query_mode"],
        "top_k": cfg["top_k"],
        "chunk_top_k": cfg["chunk_top_k"],
        "enable_rerank": cfg["enable_rerank"],
    }
    from industrial_rag.vector_collections import QDRANT_VECTOR_NAMESPACES

    qdrant_schema = {
        "distance": cfg["qdrant_distance"],
        "dimension": cfg["qdrant"]["dimension"],
        "namespaces": sorted(QDRANT_VECTOR_NAMESPACES),
        "embedding_batch_num": cfg["qdrant"]["embedding_batch_num"],
        "server_version": cfg["qdrant"]["server_version"],
        "collection_naming": "resolver:prefix_kb_{kb}_g{gen}_{namespace}",
    }
    golden_hash = sha256_file(GOLDEN_SET_PATH)
    return {
        "chunk_config_hash": _sha256_text(json.dumps(chunk_config, sort_keys=True)),
        "embedding_config_hash": _sha256_text(json.dumps(embedding_config, sort_keys=True)),
        "index_llm_config_hash": _sha256_text(json.dumps(index_llm_config, sort_keys=True)),
        "query_llm_config_hash": _sha256_text(json.dumps(query_llm_config, sort_keys=True)),
        "prompt_bundle_hash": _sha256_text(json.dumps(prompt_bundle, sort_keys=True)),
        "retrieval_config_hash": _sha256_text(json.dumps(retrieval_config, sort_keys=True)),
        "qdrant_schema_hash": _sha256_text(json.dumps(qdrant_schema, sort_keys=True)),
        "golden_set_hash": golden_hash,
    }


def assert_consistency() -> dict[str, dict[str, str]]:
    """Assert P0/P1 differ only in parser_pipeline; return the hash tables."""
    cfg = load_frozen_config()
    p0 = cfg.get("p0_parser_pipeline")
    p1 = cfg.get("p1_parser_pipeline")
    if not p0 or not p1 or p0 == p1:
        raise RuntimeError("frozen config must define distinct P0/P1 parser pipelines")
    hashes_p0 = config_hashes(p0)
    hashes_p1 = config_hashes(p1)
    mismatches = {
        key: (hashes_p0[key], hashes_p1[key])
        for key in hashes_p0
        if hashes_p0[key] != hashes_p1[key]
    }
    if mismatches:
        raise RuntimeError(f"P0/P1 config hash mismatch: {mismatches}")
    if hashes_p0["golden_set_hash"] != GOLDEN_SHA256:
        raise RuntimeError("golden set hash does not match the frozen value")
    return {"p0": hashes_p0, "p1": hashes_p1}


PROMPT_BUNDLE_PATH = EXPERIMENT_ROOT / "fixed_model" / "prompt_bundle.json"


def write_prompt_bundle() -> dict[str, str]:
    """Freeze the exact prompt bundle used by both pipelines."""
    from industrial_rag.lightrag_service import (
        INSUFFICIENT_EVIDENCE_MESSAGE,
        _CHUNK_BOUNDARY,
        _SELECTED_CONTEXT_LABEL,
        _SYSTEM_PROMPT_BASE,
    )

    bundle = {
        "system_prompt_base": _SYSTEM_PROMPT_BASE,
        "selected_context_label": _SELECTED_CONTEXT_LABEL,
        "insufficient_evidence_message": INSUFFICIENT_EVIDENCE_MESSAGE,
        "chunk_boundary": _CHUNK_BOUNDARY,
        "evidence_policy": "select_evidence",
        "evidence_limit": load_frozen_config()["evidence_limit"],
    }
    PROMPT_BUNDLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROMPT_BUNDLE_PATH.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return bundle


def main() -> int:
    result = assert_consistency()
    print("P0/P1 config consistency gate PASSED")
    for key, value in result["p0"].items():
        print(f"  {key}: {value[:16]}...")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

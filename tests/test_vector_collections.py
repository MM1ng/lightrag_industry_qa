from __future__ import annotations

import pytest
from industrial_rag.config import Settings
from industrial_rag.vector_collections import (
    QDRANT_VECTOR_NAMESPACES,
    CollectionNameResolver,
    VectorBackend,
)


def test_settings_default_to_legacy_nano_backend() -> None:
    settings = Settings.from_mapping({"DASHSCOPE_API_KEY": "test-key"})

    assert settings.vector_backend is VectorBackend.nano
    assert settings.qdrant_url is None


def test_settings_accept_qdrant_without_leaking_api_key() -> None:
    settings = Settings.from_mapping(
        {
            "DASHSCOPE_API_KEY": "test-key",
            "VECTOR_BACKEND": "qdrant",
            "QDRANT_URL": "http://127.0.0.1:6333/",
            "QDRANT_API_KEY": "qdrant-secret",
            "QDRANT_COLLECTION_PREFIX": "ira_p3test",
        }
    )

    assert settings.vector_backend is VectorBackend.qdrant
    assert settings.qdrant_url == "http://127.0.0.1:6333"
    assert "qdrant-secret" not in repr(settings)


@pytest.mark.parametrize("kb_id", ["not-a-kb", "ABCDEF", "a" * 65, "../escape"])
def test_collection_resolver_rejects_invalid_knowledge_base_ids(kb_id: str) -> None:
    resolver = CollectionNameResolver(prefix="ira_p3test")

    with pytest.raises(ValueError, match="knowledge base"):
        resolver.names_for(kb_id=kb_id, generation="g20260731abc")


def test_collection_resolver_creates_exact_physical_collections_from_internal_id() -> None:
    resolver = CollectionNameResolver(prefix="ira_p3test")

    names = resolver.names_for(
        kb_id="a" * 32,
        generation="g20260731abc",
    )

    assert set(names) == set(QDRANT_VECTOR_NAMESPACES)
    assert names == {
        "chunks": "ira_p3test_kb_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_g20260731abc_chunks",
        "entities": "ira_p3test_kb_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_g20260731abc_entities",
        "relationships": "ira_p3test_kb_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_g20260731abc_relationships",
    }
    assert "用户知识库名称" not in " ".join(names.values())


def test_collection_resolver_rejects_unsafe_prefix_or_generation() -> None:
    with pytest.raises(ValueError, match="prefix"):
        CollectionNameResolver(prefix="unsafe-prefix!")

    resolver = CollectionNameResolver(prefix="ira_p3test")
    with pytest.raises(ValueError, match="generation"):
        resolver.names_for(kb_id="a" * 32, generation="../../other")


# ---------------------------------------------------------------------------
# Phase 3V boundary tests: uniqueness, stability, and length limits
# ---------------------------------------------------------------------------


def test_collection_names_are_isolated_across_knowledge_bases() -> None:
    resolver = CollectionNameResolver(prefix="ira_p3test")

    kb_a = resolver.names_for(kb_id="a" * 32, generation="g20260731abc")
    kb_b = resolver.names_for(kb_id="1" * 32, generation="g20260731abc")

    assert set(kb_a) == set(kb_b) == set(QDRANT_VECTOR_NAMESPACES)
    for namespace in QDRANT_VECTOR_NAMESPACES:
        assert kb_a[namespace] != kb_b[namespace]


def test_collection_names_are_isolated_across_generations() -> None:
    resolver = CollectionNameResolver(prefix="ira_p3test")
    kb_id = "2" * 32

    gen_1 = resolver.names_for(kb_id=kb_id, generation="g20260731aaa")
    gen_2 = resolver.names_for(kb_id=kb_id, generation="g20260731bbb")

    for namespace in QDRANT_VECTOR_NAMESPACES:
        assert gen_1[namespace] != gen_2[namespace]


def test_collection_names_are_distinct_across_namespaces() -> None:
    resolver = CollectionNameResolver(prefix="ira_p3test")

    names = resolver.names_for(kb_id="3" * 32, generation="g20260731abc")

    assert len(set(names.values())) == len(QDRANT_VECTOR_NAMESPACES) == 3


def test_collection_names_are_stable_for_same_input() -> None:
    resolver = CollectionNameResolver(prefix="ira_p3test")

    first = resolver.names_for(kb_id="4" * 32, generation="g20260731abc")
    second = resolver.names_for(kb_id="4" * 32, generation="g20260731abc")

    assert first == second


def test_collection_names_fit_qdrant_255_character_limit() -> None:
    resolver = CollectionNameResolver(prefix="i" * 48)  # longest allowed prefix
    generation = "g" + "a" * 63  # longest allowed generation

    names = resolver.names_for(kb_id="5" * 32, generation=generation)

    for collection_name in names.values():
        assert len(collection_name) <= 255


def test_different_prefixes_produce_different_collection_names() -> None:
    test_resolver = CollectionNameResolver(prefix="ira_p3test")
    prod_resolver = CollectionNameResolver(prefix="ira_qdrant")
    kb_id = "6" * 32
    generation = "g20260731abc"

    test_names = test_resolver.names_for(kb_id=kb_id, generation=generation)
    prod_names = prod_resolver.names_for(kb_id=kb_id, generation=generation)

    for namespace in QDRANT_VECTOR_NAMESPACES:
        assert test_names[namespace] != prod_names[namespace]
        assert test_names[namespace].startswith("ira_p3test_")
        assert prod_names[namespace].startswith("ira_qdrant_")


@pytest.mark.parametrize("prefix", ["0" + "a" * 47, "A" + "a" * 47, "a" * 49, "a-b"])
def test_collection_resolver_rejects_out_of_contract_prefixes(prefix: str) -> None:
    with pytest.raises(ValueError, match="prefix"):
        CollectionNameResolver(prefix=prefix)


def test_collection_resolver_accepts_minimal_and_maximal_prefix() -> None:
    CollectionNameResolver(prefix="a")
    CollectionNameResolver(prefix="a" * 48)


@pytest.mark.parametrize(
    "generation",
    ["g" + "a" * 7, "g" + "a" * 64, "G" + "a" * 24, "g_20260731", "g-20260731", "g 20260731"],
)
def test_collection_resolver_rejects_out_of_contract_generations(generation: str) -> None:
    resolver = CollectionNameResolver(prefix="ira_p3test")

    with pytest.raises(ValueError, match="generation"):
        resolver.names_for(kb_id="7" * 32, generation=generation)


def test_collection_resolver_accepts_minimal_and_maximal_generation() -> None:
    resolver = CollectionNameResolver(prefix="ira_p3test")
    kb_id = "8" * 32

    minimal = resolver.names_for(kb_id=kb_id, generation="g" + "a" * 8)
    maximal = resolver.names_for(kb_id=kb_id, generation="g" + "a" * 63)

    assert len(minimal["chunks"]) < len(maximal["chunks"])

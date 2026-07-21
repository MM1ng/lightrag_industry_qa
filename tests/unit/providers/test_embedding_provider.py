from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from industrial_energy_agent.providers.base import ProviderResponseError
from industrial_energy_agent.providers.fake import FakeEmbeddingProvider
from industrial_energy_agent.providers.openai_compatible import OpenAIEmbeddingProvider


class _FakeEmbeddings:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.reverse_response = False
        self.vector_dimensions = 1024
        self.response_mutator: Callable[[list[SimpleNamespace]], list[SimpleNamespace]] | None = (
            None
        )
        self.failures: list[Exception] = []
        self.call_count = 0

    def create(self, **request: Any) -> SimpleNamespace:
        self.call_count += 1
        self.requests.append(request)
        if self.failures:
            raise self.failures.pop(0)
        data = [
            SimpleNamespace(
                index=index,
                embedding=[float(text) if str(text).isdigit() else float(index)]
                * self.vector_dimensions,
            )
            for index, text in enumerate(request["input"])
        ]
        if self.reverse_response:
            data.reverse()
        if self.response_mutator is not None:
            data = self.response_mutator(data)
        return SimpleNamespace(model="text-embedding-v4", data=data)


class _FakeClient:
    def __init__(self) -> None:
        self.embeddings = _FakeEmbeddings()


def test_embedding_uses_confirmed_openai_compatible_parameters() -> None:
    client = _FakeClient()
    provider = OpenAIEmbeddingProvider(
        client,
        model="text-embedding-v4",
        dimensions=1024,
    )

    vectors = provider.embed(["泵轴承"])

    assert len(vectors) == 1
    assert len(vectors[0]) == 1024
    assert client.embeddings.requests[0]["dimensions"] == 1024
    assert client.embeddings.requests[0]["encoding_format"] == "float"
    assert "dimension" not in client.embeddings.requests[0]


def test_embedding_batches_ten_items_and_restores_index_order() -> None:
    client = _FakeClient()
    client.embeddings.reverse_response = True
    provider = OpenAIEmbeddingProvider(
        client,
        model="text-embedding-v4",
        dimensions=1024,
    )

    vectors = provider.embed([str(index) for index in range(12)])

    assert [len(request["input"]) for request in client.embeddings.requests] == [10, 2]
    assert [vector[0] for vector in vectors] == [float(index) for index in range(12)]


def test_embedding_rejects_an_unexpected_vector_dimension() -> None:
    client = _FakeClient()
    client.embeddings.vector_dimensions = 1023
    provider = OpenAIEmbeddingProvider(
        client,
        model="text-embedding-v4",
        dimensions=1024,
    )

    with pytest.raises(ProviderResponseError, match="dimension"):
        provider.embed(["泵轴承"])


def test_embedding_rejects_missing_response_items() -> None:
    client = _FakeClient()
    client.embeddings.response_mutator = lambda data: data[:-1]
    provider = OpenAIEmbeddingProvider(
        client,
        model="text-embedding-v4",
        dimensions=1024,
    )

    with pytest.raises(ProviderResponseError, match="count or indices"):
        provider.embed(["泵轴承", "机械密封"])


def test_embedding_rejects_non_finite_values() -> None:
    client = _FakeClient()

    def insert_nan(data: list[SimpleNamespace]) -> list[SimpleNamespace]:
        data[0].embedding[0] = float("nan")
        return data

    client.embeddings.response_mutator = insert_nan
    provider = OpenAIEmbeddingProvider(
        client,
        model="text-embedding-v4",
        dimensions=1024,
    )

    with pytest.raises(ProviderResponseError, match="non-finite"):
        provider.embed(["泵轴承"])


def test_embedding_retries_a_transport_error_then_succeeds() -> None:
    client = _FakeClient()
    client.embeddings.failures = [
        openai.APIConnectionError(
            request=httpx.Request("POST", "https://example.invalid/embeddings")
        )
    ]
    provider = OpenAIEmbeddingProvider(
        client,
        model="text-embedding-v4",
        dimensions=1024,
        max_retries=1,
        retry_base_delay_seconds=0,
    )

    vectors = provider.embed(["泵轴承"])

    assert len(vectors) == 1
    assert client.embeddings.call_count == 2


def test_fake_embedding_records_only_input_count() -> None:
    secret_text = "Authorization: Bearer test-secret"
    provider = FakeEmbeddingProvider(vectors=[[0.0] * 1024], dimensions=1024)

    vectors = provider.embed([secret_text])

    assert len(vectors[0]) == 1024
    assert provider.call_summaries[0].input_count == 1
    summary = provider.call_summaries[0].model_dump_json()
    assert secret_text not in summary
    assert "test-secret" not in summary


def test_fake_embedding_matches_production_finite_value_contract() -> None:
    provider = FakeEmbeddingProvider(
        vectors=[[float("inf")] + [0.0] * 1023],
        dimensions=1024,
    )

    with pytest.raises(ProviderResponseError, match="non-finite"):
        provider.embed(["泵轴承"])

from __future__ import annotations

import pytest
from evaluation.phase10.conversation_e2e_contracts import JudgeConfig
from evaluation.phase10.conversation_e2e_semantic import (
    build_openai_compatible_metrics,
    score_semantic_rows,
)


class FakeMetric:
    def __init__(self, name: str, value: float = 0.75, error: Exception | None = None) -> None:
        self.name = name
        self.value = value
        self.error = error
        self.samples: list[dict] = []

    async def single_turn_ascore(self, sample):
        self.samples.append(sample.to_dict())
        if self.error:
            raise self.error
        return self.value


def _config() -> JudgeConfig:
    return JudgeConfig("0.3.9", "Faithfulness", "ResponseRelevancy", "fake", "judge", "fake", "embed", 0.0, None, 60, 2, 1)


def _rows() -> list[dict]:
    return [{
        "case_id": "c1",
        "standalone_query": "真实问题",
        "baseline": {"answer": "基线回答", "provider_contexts": ["context-b"], "provider_context_ids": ["chunk-b"], "provider_context_hash": "hash-b"},
        "candidate": {"answer": "候选回答", "provider_contexts": ["context-c"], "provider_context_ids": ["chunk-c"], "provider_context_hash": "hash-c"},
    }]


@pytest.mark.asyncio
async def test_semantic_metrics_use_actual_provider_context_and_same_evaluator_question() -> None:
    faithfulness = FakeMetric("faithfulness", 0.8)
    relevancy = FakeMetric("answer_relevancy", 0.6)

    result = await score_semantic_rows(_rows(), _config(), faithfulness=faithfulness, relevancy=relevancy)

    assert faithfulness.samples[0]["retrieved_contexts"] == ["context-b"]
    assert faithfulness.samples[1]["retrieved_contexts"] == ["context-c"]
    assert [sample["user_input"] for sample in relevancy.samples] == ["真实问题", "真实问题"]
    assert result[0]["baseline"]["faithfulness"] == 0.8
    assert result[0]["candidate"]["response_relevancy"] == 0.6


@pytest.mark.asyncio
async def test_judge_error_keeps_case_and_records_error() -> None:
    error = FakeMetric("faithfulness", error=RuntimeError("judge offline"))
    relevancy = FakeMetric("answer_relevancy", 0.6)

    result = await score_semantic_rows(_rows(), _config(), faithfulness=error, relevancy=relevancy)

    assert len(result) == 1
    assert result[0]["baseline"]["faithfulness"] is None
    assert "judge offline" in result[0]["baseline"]["judge_error"]


def test_openai_compatible_metric_builder_freezes_model_temperature_and_base_url() -> None:
    calls: dict[str, object] = {}

    class ChatModel:
        def __init__(self, **kwargs):
            calls["chat"] = kwargs

    class EmbeddingModel:
        def __init__(self, **kwargs):
            calls["embedding"] = kwargs

    def wrapper(value):
        calls["wrapped_llm"] = value
        return "llm"

    def embedding_wrapper(value):
        calls["wrapped_embedding"] = value
        return "embedding"

    faithfulness, relevancy = build_openai_compatible_metrics(
        _config(),
        base_url="https://example.invalid/v1",
        api_key="key",
        chat_model_factory=ChatModel,
        embedding_model_factory=EmbeddingModel,
        llm_wrapper_factory=wrapper,
        embedding_wrapper_factory=embedding_wrapper,
    )

    assert calls["chat"] == {"model": "judge", "api_key": "key", "base_url": "https://example.invalid/v1", "temperature": 0.0, "timeout": 60, "max_retries": 2}
    assert calls["embedding"] == {"model": "embed", "api_key": "key", "base_url": "https://example.invalid/v1", "timeout": 60, "max_retries": 2}
    assert faithfulness.llm == "llm"
    assert relevancy.embeddings == "embedding"

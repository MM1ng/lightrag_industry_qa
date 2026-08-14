from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from evaluation.phase10.conversation_e2e_contracts import JudgeConfig
from evaluation.phase10.conversation_e2e_semantic import (
    build_default_metrics,
    build_openai_compatible_metrics,
    run_semantic_preflight,
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


@pytest.mark.asyncio
async def test_faithfulness_formal_scoring_survives_relevancy_failure() -> None:
    faithfulness = FakeMetric("faithfulness", 0.8)
    relevancy = FakeMetric("answer_relevancy", error=ProviderError(500, "relevancy-500"))

    result = await score_semantic_rows(
        _rows(),
        _config(),
        faithfulness=faithfulness,
        relevancy=relevancy,
        enabled_metrics=("faithfulness",),
    )

    assert result[0]["baseline"]["faithfulness"] == 0.8
    assert result[0]["candidate"]["faithfulness"] == 0.8
    assert result[0]["baseline"]["response_relevancy_status"] == "not_run"
    assert result[0]["candidate"]["response_relevancy_status"] == "not_run"


@pytest.mark.asyncio
async def test_failed_relevancy_does_not_clear_successful_faithfulness_score() -> None:
    result = await score_semantic_rows(
        _rows(),
        _config(),
        faithfulness=FakeMetric("faithfulness", 0.8),
        relevancy=FakeMetric("answer_relevancy", error=ProviderError(500, "relevancy-500")),
        enabled_metrics=("faithfulness", "response_relevancy"),
    )

    assert result[0]["baseline"]["faithfulness"] == 0.8
    assert result[0]["baseline"]["response_relevancy"] is None
    assert result[0]["baseline"]["response_relevancy_status"] == "blocked"
    assert result[0]["baseline"]["judge_errors"][0]["request_id"] == "relevancy-500"


@pytest.mark.asyncio
async def test_formal_metric_timeout_is_retained_as_arm_error() -> None:
    class SlowMetric:
        async def single_turn_ascore(self, _sample):
            await asyncio.sleep(0.05)
            return 0.8

    result = await score_semantic_rows(
        _rows(),
        JudgeConfig("0.3.9", "Faithfulness", "ResponseRelevancy", "fake", "judge", "fake", "embed", 0.0, None, 0, 2, 1),
        faithfulness=SlowMetric(),
        relevancy=FakeMetric("relevancy"),
        enabled_metrics=("faithfulness",),
    )

    assert result[0]["baseline"]["faithfulness"] is None
    assert result[0]["baseline"]["faithfulness_status"] == "blocked"
    assert result[0]["baseline"]["judge_errors"][0]["error_type"] == "TimeoutError"


@pytest.mark.asyncio
async def test_formal_metric_emits_checkpoint_callback_for_each_case() -> None:
    checkpoints: list[dict] = []
    await score_semantic_rows(
        _rows(),
        _config(),
        faithfulness=FakeMetric("faithfulness"),
        relevancy=FakeMetric("relevancy"),
        enabled_metrics=("faithfulness",),
        on_row=checkpoints.append,
    )

    assert [row["case_id"] for row in checkpoints] == ["c1"]


def test_response_relevancy_diagnostic_can_use_strictness_one_without_changing_formal_default() -> None:
    formal = build_default_metrics(llm="llm", embeddings="embedding", max_retries=2)
    diagnostic = build_default_metrics(llm="llm", embeddings="embedding", max_retries=2, response_relevancy_strictness=1)

    assert formal[1].strictness == 3
    assert diagnostic[1].strictness == 1


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


class ProviderError(RuntimeError):
    def __init__(self, status_code: int, request_id: str) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.request_id = request_id


class FakeCreate:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(_request_id="ready-request")


@pytest.mark.asyncio
async def test_semantic_preflight_attributes_chat_5xx_and_retries_only_the_failing_layer() -> None:
    chat = FakeCreate(error=ProviderError(500, "chat-500"))
    embedding = FakeCreate()
    client = SimpleNamespace(chat=SimpleNamespace(completions=chat), embeddings=embedding)

    result = await run_semantic_preflight(
        config=_config(),
        client=client,
        faithfulness=FakeMetric("faithfulness"),
        relevancy=FakeMetric("relevancy"),
    )

    assert result["status"] == "BLOCKED"
    assert result["chat"]["reason_code"] == "chat_provider_error"
    assert len(result["chat"]["attempts"]) == 2
    assert {attempt["request_id"] for attempt in result["chat"]["attempts"]} == {"chat-500"}
    assert result["embedding"]["status"] == "READY"
    assert result["faithfulness"]["status"] == "READY"
    assert result["response_relevancy"]["status"] == "READY"


@pytest.mark.asyncio
async def test_semantic_preflight_keeps_faithfulness_and_relevancy_errors_separate() -> None:
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCreate()),
        embeddings=FakeCreate(),
    )

    result = await run_semantic_preflight(
        config=_config(),
        client=client,
        faithfulness=FakeMetric("faithfulness", error=ProviderError(500, "faithfulness-500")),
        relevancy=FakeMetric("relevancy", error=ProviderError(500, "relevancy-500")),
    )

    assert result["status"] == "BLOCKED"
    assert result["faithfulness"]["reason_code"] == "faithfulness_metric_error"
    assert result["response_relevancy"]["reason_code"] == "response_relevancy_metric_error"
    assert len(result["faithfulness"]["attempts"]) == 2
    assert len(result["response_relevancy"]["attempts"]) == 2

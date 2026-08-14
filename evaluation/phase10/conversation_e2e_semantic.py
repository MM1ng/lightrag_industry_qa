"""Ragas 0.3.9 semantic metric boundary with immutable judge configuration."""

from __future__ import annotations

from typing import Any

from ragas.dataset_schema import SingleTurnSample
from ragas.metrics import Faithfulness, ResponseRelevancy

from .conversation_e2e_contracts import JudgeConfig


def build_default_metrics(*, llm: Any = None, embeddings: Any = None, max_retries: int = 2) -> tuple[Any, Any]:
    return (
        Faithfulness(llm=llm, max_retries=max_retries),
        ResponseRelevancy(llm=llm, embeddings=embeddings, strictness=3),
    )


def build_openai_compatible_metrics(
    config: JudgeConfig,
    *,
    base_url: str,
    api_key: str,
    chat_model_factory: Any = None,
    embedding_model_factory: Any = None,
    llm_wrapper_factory: Any = None,
    embedding_wrapper_factory: Any = None,
) -> tuple[Any, Any]:
    """Build fixed Ragas 0.3.9 metrics for a DashScope OpenAI-compatible API."""

    if chat_model_factory is None:
        from langchain_openai import ChatOpenAI

        chat_model_factory = ChatOpenAI
    if embedding_model_factory is None:
        from langchain_openai import OpenAIEmbeddings

        embedding_model_factory = OpenAIEmbeddings
    if llm_wrapper_factory is None:
        from ragas.llms.base import LangchainLLMWrapper

        llm_wrapper_factory = LangchainLLMWrapper
    if embedding_wrapper_factory is None:
        from ragas.embeddings.base import LangchainEmbeddingsWrapper

        embedding_wrapper_factory = LangchainEmbeddingsWrapper
    chat_model = chat_model_factory(
        model=config.judge_model,
        api_key=api_key,
        base_url=base_url,
        temperature=config.temperature,
        timeout=config.timeout_seconds,
        max_retries=config.retry,
    )
    embedding_model = embedding_model_factory(
        model=config.embedding_model,
        api_key=api_key,
        base_url=base_url,
        timeout=config.timeout_seconds,
        max_retries=config.retry,
    )
    llm = llm_wrapper_factory(chat_model)
    embeddings = embedding_wrapper_factory(embedding_model)
    return build_default_metrics(llm=llm, embeddings=embeddings, max_retries=config.retry)


async def semantic_smoke_test(*, faithfulness: Any, relevancy: Any, config: JudgeConfig) -> dict[str, Any]:
    sample = SingleTurnSample(
        user_input="泵的启动步骤是什么？",
        response="按照手册执行启动步骤。",
        retrieved_contexts=["手册规定了启动步骤。"],
    )
    try:
        faithfulness_score = await faithfulness.single_turn_ascore(sample)
        relevancy_score = await relevancy.single_turn_ascore(sample)
    except Exception as error:
        return {"status": "BLOCKED", "judge_error": f"{type(error).__name__}: {error}", "judge_config": config.to_dict()}
    return {
        "status": "READY",
        "faithfulness": float(faithfulness_score),
        "response_relevancy": float(relevancy_score),
        "judge_config": config.to_dict(),
    }


async def _score_metric(metric: Any, sample: SingleTurnSample) -> tuple[float | None, str | None]:
    try:
        return float(await metric.single_turn_ascore(sample)), None
    except Exception as error:
        return None, f"{type(error).__name__}: {error}"


async def score_semantic_rows(
    rows: list[dict[str, Any]],
    config: JudgeConfig,
    *,
    faithfulness: Any | None = None,
    relevancy: Any | None = None,
) -> list[dict[str, Any]]:
    if faithfulness is None or relevancy is None:
        faithfulness, relevancy = build_default_metrics()
    output: list[dict[str, Any]] = []
    for row in rows:
        scored: dict[str, Any] = {"case_id": row["case_id"], "judge_config": config.to_dict()}
        for arm_name in ("baseline", "candidate"):
            arm = row[arm_name]
            contexts = list(arm.get("provider_contexts", ()))
            if not contexts:
                error = "actual provider context text unavailable; IDs/hashes are not valid semantic contexts"
                scored[arm_name] = {"faithfulness": None, "response_relevancy": None, "judge_error": error}
                continue
            sample = SingleTurnSample(
                user_input=str(row["standalone_query"]),
                response=str(arm.get("answer", "")),
                retrieved_contexts=contexts,
            )
            faithfulness_score, faithfulness_error = await _score_metric(faithfulness, sample)
            relevancy_score, relevancy_error = await _score_metric(relevancy, sample)
            errors = "; ".join(error for error in (faithfulness_error, relevancy_error) if error) or None
            scored[arm_name] = {
                "faithfulness": faithfulness_score,
                "response_relevancy": relevancy_score,
                "judge_error": errors,
                "provider_context_ids": list(arm.get("provider_context_ids", ())),
                "provider_context_hash": arm.get("provider_context_hash"),
                "evaluation_user_input": row["standalone_query"],
            }
        output.append(scored)
    return output

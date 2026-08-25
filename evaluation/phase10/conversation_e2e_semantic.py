"""Ragas 0.3.9 semantic metric boundary with immutable judge configuration."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from ragas.dataset_schema import SingleTurnSample
from ragas.metrics import Faithfulness, ResponseRelevancy

from .conversation_e2e_contracts import JudgeConfig


def build_default_metrics(
    *,
    llm: Any = None,
    embeddings: Any = None,
    max_retries: int = 2,
    response_relevancy_strictness: int = 3,
) -> tuple[Any, Any]:
    return (
        Faithfulness(llm=llm, max_retries=max_retries),
        ResponseRelevancy(llm=llm, embeddings=embeddings, strictness=response_relevancy_strictness),
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
    response_relevancy_strictness: int = 3,
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
    return build_default_metrics(
        llm=llm,
        embeddings=embeddings,
        max_retries=config.retry,
        response_relevancy_strictness=response_relevancy_strictness,
    )


def _provider_error_details(error: Exception, attempt: int) -> dict[str, Any]:
    status = getattr(error, "status_code", None)
    return {
        "attempt": attempt,
        "error_type": type(error).__name__,
        "http_status": status,
        "request_id": getattr(error, "request_id", None),
        "timestamp": datetime.now(UTC).isoformat(),
        "message": str(error),
    }


async def _run_provider_preflight(reason_code: str, operation: Any, retry: int) -> dict[str, Any]:
    """Call one direct provider boundary with a bounded 5xx-only retry policy."""

    attempts: list[dict[str, Any]] = []
    for attempt in range(1, max(1, retry) + 1):
        try:
            response = await operation()
        except Exception as error:
            detail = _provider_error_details(error, attempt)
            attempts.append(detail)
            if not (isinstance(detail["http_status"], int) and 500 <= detail["http_status"] < 600 and attempt < max(1, retry)):
                return {
                    "status": "BLOCKED",
                    "reason_code": reason_code,
                    "reason": f"{detail['error_type']}: {detail['message']}",
                    "attempts": attempts,
                }
        else:
            return {
                "status": "READY",
                "request_id": getattr(response, "_request_id", None),
                "attempts": [*attempts, {
                    "attempt": attempt,
                    "http_status": None,
                    "request_id": getattr(response, "_request_id", None),
                    "timestamp": datetime.now(UTC).isoformat(),
                }],
            }
    return {
        "status": "BLOCKED",
        "reason_code": reason_code,
        "reason": "provider retry budget exhausted",
        "attempts": attempts,
    }


async def _run_metric_preflight(
    reason_code: str,
    metric: Any,
    sample: SingleTurnSample,
    retry: int,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Score one metric with the same finite 5xx retry budget as direct calls."""

    attempts: list[dict[str, Any]] = []
    for attempt in range(1, max(1, retry) + 1):
        try:
            value = await asyncio.wait_for(metric.single_turn_ascore(sample), timeout=timeout_seconds)
        except Exception as error:
            detail = _provider_error_details(error, attempt)
            attempts.append(detail)
            if isinstance(detail["http_status"], int) and 500 <= detail["http_status"] < 600 and attempt < max(1, retry):
                continue
            return {
                "status": "BLOCKED",
                "reason_code": reason_code,
                "reason": f"{detail['error_type']}: {detail['message']}",
                "attempts": attempts,
            }
        else:
            return {"status": "READY", "value": float(value), "attempts": attempts}
    return {
        "status": "BLOCKED",
        "reason_code": reason_code,
        "reason": "provider retry budget exhausted",
        "attempts": attempts,
    }


async def run_semantic_preflight(
    *,
    config: JudgeConfig,
    client: Any,
    faithfulness: Any,
    relevancy: Any,
    enabled_metrics: tuple[str, ...] = ("faithfulness", "response_relevancy"),
) -> dict[str, Any]:
    """Diagnose direct providers and each Ragas metric independently."""

    chat = await _run_provider_preflight(
        "chat_provider_error",
        lambda: client.chat.completions.create(
            model=config.judge_model,
            messages=[{"role": "user", "content": "Respond with OK."}],
            temperature=config.temperature,
        ),
        config.retry,
    )
    embedding = await _run_provider_preflight(
        "embedding_provider_error",
        lambda: client.embeddings.create(model=config.embedding_model, input="semantic preflight"),
        config.retry,
    )
    sample = SingleTurnSample(
        user_input="泵的启动步骤是什么？",
        response="按照手册执行启动步骤。",
        retrieved_contexts=["手册规定了启动步骤。"],
    )
    faithfulness_result = (
        await _run_metric_preflight("faithfulness_metric_error", faithfulness, sample, config.retry, config.timeout_seconds)
        if "faithfulness" in enabled_metrics
        else {"status": "NOT_RUN", "reason_code": "metric_not_requested", "attempts": []}
    )
    relevancy_result = (
        await _run_metric_preflight("response_relevancy_metric_error", relevancy, sample, config.retry, config.timeout_seconds)
        if "response_relevancy" in enabled_metrics
        else {"status": "NOT_RUN", "reason_code": "metric_not_requested", "attempts": []}
    )
    components = {
        "chat": chat,
        "embedding": embedding,
        "faithfulness": faithfulness_result,
        "response_relevancy": relevancy_result,
    }
    blocked = [
        result
        for name, result in components.items()
        if name in {"chat", "embedding", *enabled_metrics} and result["status"] == "BLOCKED"
    ]
    return {
        "status": "BLOCKED" if blocked else "READY",
        "components": components,
        **components,
        "judge_config": config.to_dict(),
    }


async def run_metric_smoke(metric: Any, *, metric_name: str, config: JudgeConfig) -> dict[str, Any]:
    """Run one metric smoke sample for diagnostics without producing formal scores."""

    sample = SingleTurnSample(
        user_input="泵的启动步骤是什么？",
        response="按照手册执行启动步骤。",
        retrieved_contexts=["手册规定了启动步骤。"],
    )
    return await _run_metric_preflight(f"{metric_name}_metric_error", metric, sample, config.retry, config.timeout_seconds)


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
    enabled_metrics: tuple[str, ...] = ("faithfulness", "response_relevancy"),
    on_row: Any | None = None,
) -> list[dict[str, Any]]:
    if faithfulness is None or relevancy is None:
        faithfulness, relevancy = build_default_metrics()
    output: list[dict[str, Any]] = []
    for row in rows:
        scored: dict[str, Any] = {"case_id": row["case_id"], "judge_config": config.to_dict()}
        for arm_name in ("baseline", "candidate"):
            arm = row[arm_name]
            contexts = list(arm.get("provider_contexts", ()))
            arm_result: dict[str, Any] = {
                "faithfulness": None,
                "response_relevancy": None,
                "faithfulness_status": "not_run" if "faithfulness" not in enabled_metrics else "blocked",
                "response_relevancy_status": "not_run" if "response_relevancy" not in enabled_metrics else "blocked",
                "judge_error": None,
                "judge_errors": [],
                "provider_context_ids": list(arm.get("provider_context_ids", ())),
                "provider_context_hash": arm.get("provider_context_hash"),
                "evaluation_user_input": row["standalone_query"],
            }
            if not contexts:
                error = {
                    "error_type": "MissingProviderContextError",
                    "http_status": None,
                    "request_id": None,
                    "attempt": 0,
                    "message": "actual provider context text unavailable; IDs/hashes are not valid semantic contexts",
                }
                for metric_name in enabled_metrics:
                    arm_result[f"{metric_name}_status"] = "blocked"
                    arm_result["judge_errors"].append({"metric": metric_name, **error})
            else:
                sample = SingleTurnSample(
                    user_input=str(row["standalone_query"]),
                    response=str(arm.get("answer", "")),
                    retrieved_contexts=contexts,
                )
                for metric_name, metric in (("faithfulness", faithfulness), ("response_relevancy", relevancy)):
                    if metric_name not in enabled_metrics:
                        continue
                    result = await _run_metric_preflight(
                        f"{metric_name}_metric_error",
                        metric,
                        sample,
                        config.retry,
                        config.timeout_seconds,
                    )
                    if result["status"] == "READY":
                        arm_result[metric_name] = result["value"]
                        arm_result[f"{metric_name}_status"] = "available"
                    else:
                        arm_result[f"{metric_name}_status"] = "blocked"
                        arm_result["judge_errors"].extend(
                            {"metric": metric_name, **attempt}
                            for attempt in result.get("attempts", [])
                        )
            arm_result["judge_error"] = "; ".join(
                f"{item['error_type']}: {item['message']}" for item in arm_result["judge_errors"]
            ) or None
            scored[arm_name] = arm_result
        output.append(scored)
        if on_row is not None:
            callback_result = on_row(scored)
            if hasattr(callback_result, "__await__"):
                await callback_result
    return output

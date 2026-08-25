"""Fair, development-only A/B adapter around the real LightRAGService."""

from __future__ import annotations

import time
from dataclasses import asdict, is_dataclass
from typing import Any

from industrial_rag.conversation.query_rewriter import QueryRewriter
from industrial_rag.query_normalization import normalize_query

from .conversation_e2e_contracts import provider_context_payload, resolved_evaluation_user_input


def _failure_layer(error: BaseException) -> str:
    text = str(error).casefold()
    if "retriev" in text or "qdrant" in text:
        return "Retrieval Error"
    return "Answer Generation Error"


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _trace_payload(result: Any) -> dict[str, Any]:
    trace = getattr(result, "retrieval_trace", None)
    payload = trace.to_payload() if trace is not None and hasattr(trace, "to_payload") else {}
    context = provider_context_payload(result)
    return {
        "retrieved_chunk_ids": list(getattr(result, "retrieval_chunk_ids", ()) or ()),
        "retrieved_ranks": {str(index): chunk_id for index, chunk_id in enumerate(getattr(result, "retrieval_chunk_ids", ()) or (), 1)},
        "selected_evidence_ids": list(payload.get("selected_chunk_ids", ()) or ()),
        "provider_evidence_ids": context["provider_evidence_ids"],
        "provider_context_ids": context["provider_context_order"],
        "provider_context_hash": context["provider_context_sha256"],
        "provider_contexts": list(getattr(trace, "provider_contexts", ()) or ()) if trace is not None else [],
        "trace": payload,
    }


async def _run_arm(service: Any, runtime_query: str, options: dict[str, object]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = await service.query(runtime_query, **options)
        trace = _trace_payload(result)
        return {
            "runtime_query": runtime_query,
            "answer": str(getattr(result, "answer", "")),
            "answer_status": getattr(result, "answer_status", "error"),
            "citations": _json_safe(getattr(result, "citations", ())),
            "answer_points": _json_safe(getattr(result, "answer_points", ())),
            "grounding_removed_points": list(trace["trace"].get("grounding_removed_answer_points", ()) or ()),
            "grounding_failure_categories": list(getattr(result, "grounding_failure_categories", ()) or ()),
            "latency_ms": (time.perf_counter() - started) * 1000,
            "failure_layer": None,
            "metric_error": None,
            **trace,
        }
    except Exception as error:
        return {
            "runtime_query": runtime_query,
            "answer": "",
            "answer_status": "error",
            "citations": [],
            "answer_points": [],
            "grounding_removed_points": [],
            "grounding_failure_categories": [],
            "latency_ms": (time.perf_counter() - started) * 1000,
            "failure_layer": _failure_layer(error),
            "metric_error": f"{type(error).__name__}: {error}",
            "retrieved_chunk_ids": [],
            "retrieved_ranks": {},
            "selected_evidence_ids": [],
            "provider_evidence_ids": [],
            "provider_context_ids": [],
            "provider_context_hash": None,
            "trace": {},
        }


async def run_case(
    service: Any,
    case: dict[str, Any],
    *,
    mode: str,
    top_k: int,
    chunk_top_k: int,
    rewriter: QueryRewriter | None = None,
) -> dict[str, Any]:
    options = {"mode": mode, "top_k": top_k, "chunk_top_k": chunk_top_k}
    baseline = await _run_arm(service, str(case["dependent_query"]), options)
    rewrite = None
    candidate_query = str(case["dependent_query"])
    rewriter = rewriter or QueryRewriter()
    try:
        rewrite = await rewriter.rewrite(case["dependent_query"], case.get("history", []))
        if rewrite.status != "rewritten" or not rewrite.standalone_query:
            raise ValueError(f"rewrite status is {rewrite.status}")
        candidate_query = normalize_query(rewrite.standalone_query).normalized_query
        expected = normalize_query(case.get("expected_standalone_query", "")).normalized_query
        if candidate_query != expected:
            raise ValueError("rewrite output does not match frozen standalone query")
    except Exception as error:
        rewrite = None
        candidate = {
            "runtime_query": candidate_query,
            "answer": "",
            "answer_status": "error",
            "citations": [],
            "answer_points": [],
            "grounding_removed_points": [],
            "grounding_failure_categories": [],
            "latency_ms": 0.0,
            "failure_layer": "Answer Generation Error",
            "metric_error": f"{type(error).__name__}: {error}",
            "retrieved_chunk_ids": [],
            "retrieved_ranks": {},
            "selected_evidence_ids": [],
            "provider_evidence_ids": [],
            "provider_context_ids": [],
            "provider_context_hash": None,
            "trace": {},
        }
    else:
        candidate = await _run_arm(service, candidate_query, options)
    rewrite_payload = {
        "status": rewrite.status if rewrite is not None else "failed",
        "reason": rewrite.rewrite_reason if rewrite is not None else candidate["metric_error"],
        "history_message_count": rewrite.history_message_count if rewrite is not None else len(case.get("history", [])),
        "history_used": rewrite.history_used if rewrite is not None else False,
    }
    for arm in (baseline, candidate):
        arm["evaluation_user_input"] = resolved_evaluation_user_input(case)
    candidate["rewrite_status"] = rewrite_payload["status"]
    candidate["rewrite_reason"] = rewrite_payload["reason"]
    return {
        "case_id": str(case["case_id"]),
        "history": case.get("history", []),
        "dependent_query": case["dependent_query"],
        "standalone_query": resolved_evaluation_user_input(case),
        "gold_chunk_ids": list(case.get("gold_chunk_ids", ())),
        "rewrite": rewrite_payload,
        "baseline": baseline,
        "candidate": candidate,
    }

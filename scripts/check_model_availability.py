"""Minimal provider preflight without persisting provider responses or secrets."""

from __future__ import annotations

import argparse
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx


def _probe(base_url: str, api_key: str, model: str, *, embedding: bool = False) -> dict[str, object]:
    started = time.perf_counter()
    path = "/embeddings" if embedding else "/chat/completions"
    payload = {"model": model, "input": ["health"]} if embedding else {"model": model, "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 1}
    try:
        response = httpx.post(
            base_url.rstrip("/") + path,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=45,
        )
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        error = body.get("error") if isinstance(body, dict) else None
        return {
            "model_name": model,
            "http_status": response.status_code,
            "provider_error_code": error.get("code") if isinstance(error, dict) else None,
            "completion_available": response.is_success and bool(body.get("data") or body.get("choices")),
            "latency_ms": round((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:
        return {"model_name": model, "http_status": None, "provider_error_code": type(exc).__name__, "completion_available": False, "latency_ms": round((time.perf_counter() - started) * 1000)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base_url = os.environ.get("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    models = [item.strip() for item in os.environ.get("LLM_PREFLIGHT_MODELS", "kimi-k2.6,qwen3.6-plus,qwen3.6-flash,qwen-plus-2025-07-28").split(",") if item.strip()]
    results = [_probe(base_url, api_key, model) for model in models]
    results.append(_probe(base_url, api_key, os.environ.get("EMBEDDING_MODEL", "text-embedding-v4"), embedding=True))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(__import__("json").dumps({"checked_at": datetime.now(UTC).isoformat(), "base_url_configured": bool(base_url), "results": results, "fixed_model": next((item["model_name"] for item in results if item["completion_available"] and item["model_name"] != "text-embedding-v4"), None), "external_dependency_blocked": not any(item["completion_available"] and item["model_name"] != "text-embedding-v4" for item in results), "secret_values_written": False}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

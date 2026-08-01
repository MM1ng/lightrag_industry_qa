"""Fixed-model LLM function with per-call usage recording (Phase 3A-R)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI


class ModelMismatchError(RuntimeError):
    """Raised when the API reports a model different from the fixed model."""


class FixedModelLLM:
    """Call one exact DashScope model; never fall back; record every call."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        enable_thinking: bool = False,
        max_retries: int = 2,
        timeout: float = 180.0,
        cache_path: Path | None = None,
        config_hash: str = "",
    ) -> None:
        self.model = model
        self.enable_thinking = enable_thinking
        self.max_retries = max_retries
        self.calls: list[dict[str, Any]] = []
        self.cache_path = cache_path
        self.config_hash = config_hash
        self.cache_hits = 0
        self.cache_misses = 0
        self._cache: dict[str, dict[str, Any]] = {}
        if cache_path is not None and cache_path.is_file():
            for line in cache_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._cache[entry["key"]] = entry
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout

    def _cache_key(self, system_prompt: str | None, prompt: str) -> str:
        payload = "\x00".join(
            [
                self.model,
                _hash(system_prompt or ""),
                _hash(prompt),
                self.config_hash,
                "lightrag-1.5.4",
                "phase3a_fixed_model_parser_comparison",
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def __call__(
        self,
        prompt: str,
        system_prompt: str | None = None,
        history_messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> str:
        kwargs.pop("model", None)
        kwargs.pop("hashing_kv", None)
        kwargs.pop("keyword_extraction", None)
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(history_messages or [])
        messages.append({"role": "user", "content": prompt})
        started = time.monotonic()
        retry_count = 0
        error_code: str | None = None
        status = "ok"
        input_tokens = output_tokens = total_tokens = 0
        actual_model = self.model
        content = ""
        cache_key = self._cache_key(system_prompt, prompt)
        cached = self._cache.get(cache_key)
        if cached is not None:
            self.cache_hits += 1
            self.calls.append(
                {
                    "requested_model": self.model,
                    "actual_model": self.model,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cached_input_tokens": cached.get("input_tokens", 0),
                    "cached_output_tokens": cached.get("output_tokens", 0),
                    "cached_total_tokens": cached.get("total_tokens", 0),
                    "latency": 0.0,
                    "retry_count": 0,
                    "status": "cache_hit",
                    "error_code": None,
                    "cache_hit": True,
                    "system_prompt_hash": _hash(system_prompt or ""),
                    "prompt_hash": _hash(prompt),
                }
            )
            return str(cached["content"])
        self.cache_misses += 1
        while True:
            client = AsyncOpenAI(
                base_url=self._base_url, api_key=self._api_key, timeout=self._timeout
            )
            try:
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    extra_body={"enable_thinking": self.enable_thinking},
                    **kwargs,
                )
                actual_model = response.model or self.model
                if actual_model != self.model:
                    raise ModelMismatchError(
                        f"requested {self.model} but API used {actual_model}"
                    )
                usage = response.usage
                if usage is not None:
                    input_tokens = usage.prompt_tokens or 0
                    output_tokens = usage.completion_tokens or 0
                    total_tokens = usage.total_tokens or 0
                content = response.choices[0].message.content or ""
                break
            except ModelMismatchError:
                status = "model_mismatch"
                error_code = "MODEL_MISMATCH"
                raise
            except Exception as error:
                retry_count += 1
                error_code = type(error).__name__
                if retry_count > self.max_retries:
                    status = "error"
                    raise
                await asyncio.sleep(min(2 ** retry_count, 8))
            finally:
                await client.close()
        self.calls.append(
            {
                "requested_model": self.model,
                "actual_model": actual_model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "latency": round(time.monotonic() - started, 3),
                "retry_count": retry_count,
                "status": status,
                "error_code": error_code,
                "cache_hit": False,
                "system_prompt_hash": _hash(system_prompt or ""),
                "prompt_hash": _hash(prompt),
            }
        )
        if self.cache_path is not None and status == "ok":
            entry = {
                "key": cache_key,
                "content": content,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "model": self.model,
            }
            self._cache[cache_key] = entry
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self.cache_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return content

    def summary(self) -> dict[str, Any]:
        return {
            "call_count": len(self.calls),
            "input_tokens": sum(c["input_tokens"] for c in self.calls),
            "output_tokens": sum(c["output_tokens"] for c in self.calls),
            "total_tokens": sum(c["total_tokens"] for c in self.calls),
            "retry_count": sum(c["retry_count"] for c in self.calls),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "model_mismatches": sum(1 for c in self.calls if c["status"] == "model_mismatch"),
            "errors": sum(1 for c in self.calls if c["status"] == "error"),
        }


def _hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

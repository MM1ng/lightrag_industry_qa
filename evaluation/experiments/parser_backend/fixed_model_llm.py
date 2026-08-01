"""Fixed-model LLM function with per-call usage recording (Phase 3A-R)."""

from __future__ import annotations

import asyncio
import os
import time
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
    ) -> None:
        self.model = model
        self.enable_thinking = enable_thinking
        self.max_retries = max_retries
        self.calls: list[dict[str, Any]] = []
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout

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
                "system_prompt_hash": _hash(system_prompt or ""),
                "prompt_hash": _hash(prompt),
            }
        )
        return content

    def summary(self) -> dict[str, Any]:
        return {
            "call_count": len(self.calls),
            "input_tokens": sum(c["input_tokens"] for c in self.calls),
            "output_tokens": sum(c["output_tokens"] for c in self.calls),
            "total_tokens": sum(c["total_tokens"] for c in self.calls),
            "retry_count": sum(c["retry_count"] for c in self.calls),
            "model_mismatches": sum(1 for c in self.calls if c["status"] == "model_mismatch"),
            "errors": sum(1 for c in self.calls if c["status"] == "error"),
        }


def _hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

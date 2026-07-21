"""Explicit BaiLian OpenAI-compatible compatibility smoke checks."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, ConfigDict

from industrial_energy_agent.config.settings import Settings
from industrial_energy_agent.providers.base import ProviderResponseError
from industrial_energy_agent.providers.openai_compatible import (
    OpenAIChatProvider,
    OpenAIEmbeddingProvider,
)

ClientFactory = Callable[[Settings], Any]
LOCKED_CHAT_MODEL = "qwen3.7-plus"
LOCKED_EMBEDDING_MODEL = "text-embedding-v4"
LOCKED_EMBEDDING_DIMENSION = 1024


class SmokeContractError(RuntimeError):
    """Raised when the explicit smoke no longer tests the locked MVP contract."""


class _SmokeJsonResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]


def _build_client(settings: Settings) -> OpenAI:
    api_key = settings.llm_api_key
    if api_key is None:
        raise RuntimeError("BaiLian API key is not configured")
    return OpenAI(
        api_key=api_key.get_secret_value(),
        base_url=str(settings.llm_base_url).rstrip("/"),
        timeout=settings.llm_timeout_seconds,
        max_retries=0,
    )


def _validate_locked_contract(settings: Settings) -> None:
    if (
        settings.chat_model != LOCKED_CHAT_MODEL
        or settings.embedding_model != LOCKED_EMBEDDING_MODEL
        or settings.embedding_dimension != LOCKED_EMBEDDING_DIMENSION
    ):
        raise SmokeContractError("Configured models or dimensions differ from the MVP contract")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat", action="store_true")
    parser.add_argument("--json-mode", action="store_true")
    parser.add_argument("--function-call", action="store_true")
    parser.add_argument("--embedding", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: ClientFactory | None = None,
) -> int:
    args = _parser().parse_args(argv)
    if not any((args.chat, args.json_mode, args.function_call, args.embedding)):
        print("Select at least one smoke check flag.", file=sys.stderr)
        return 2

    try:
        # ``_env_file`` is a runtime BaseSettings keyword not present in the generated signature.
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
    except Exception as error:
        print(f"FAIL BaiLian smoke error_type={type(error).__name__}", file=sys.stderr)
        return 1
    if settings.llm_api_key is None:
        print("BaiLian API key is not configured.", file=sys.stderr)
        return 2
    factory = client_factory or _build_client
    try:
        _validate_locked_contract(settings)
        client = factory(settings)
        chat_provider = OpenAIChatProvider(
            client,
            model=settings.chat_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
        embedding_provider = OpenAIEmbeddingProvider(
            client,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimension,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

        if args.chat:
            content = chat_provider.complete("Reply with the single word OK.")
            if not content.strip():
                raise ProviderResponseError("Text smoke response is empty")
            print(f"PASS chat model={settings.chat_model}")

        if args.json_mode:
            chat_provider.complete_json(
                'Return JSON exactly as {"status":"ok"}.',
                _SmokeJsonResponse,
            )
            print(f"PASS json-mode model={settings.chat_model}")

        if args.function_call:
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "report_readiness",
                        "description": "Report this harmless compatibility check as ready.",
                        "parameters": {
                            "type": "object",
                            "properties": {"status": {"type": "string", "enum": ["ready"]}},
                            "required": ["status"],
                            "additionalProperties": False,
                        },
                    },
                }
            ]
            decision = chat_provider.complete_with_tools(
                "Call report_readiness with status ready. Do not answer in plain text.",
                tools,
            )
            if (
                len(decision.tool_calls) != 1
                or decision.tool_calls[0].name != "report_readiness"
                or decision.tool_calls[0].arguments != {"status": "ready"}
            ):
                raise ProviderResponseError("Function-call smoke response selected no valid tool")
            print(
                f"PASS function-call model={settings.chat_model} tool={decision.tool_calls[0].name}"
            )

        if args.embedding:
            vectors = embedding_provider.embed(["离心泵轴承状态检查"])
            if len(vectors) != 1:
                raise ProviderResponseError("Embedding smoke response count is invalid")
            print(f"PASS embedding model={settings.embedding_model} dimensions={len(vectors[0])}")
    except Exception as error:
        print(f"FAIL BaiLian smoke error_type={type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

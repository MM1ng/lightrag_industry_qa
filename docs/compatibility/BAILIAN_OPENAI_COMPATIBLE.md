# BaiLian OpenAI-compatible contract

Verified against Alibaba Cloud Model Studio documentation on 2026-07-21. This document
records only the contract used by the EnergyOps Copilot MVP.

## Locked MVP configuration

| Capability | Value |
|---|---|
| Region | Beijing |
| Chat model | `qwen3.7-plus` |
| Embedding model | `text-embedding-v4` |
| Embedding dimensions | `1024` |
| SDK boundary | OpenAI-compatible synchronous API |

The shared Beijing-compatible base URL is:

```text
https://dashscope.aliyuncs.com/compatible-mode/v1
```

For production traffic in a BaiLian business workspace, use its Beijing workspace URL:

```text
https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
```

`LLM_BASE_URL` selects the workspace URL without changing application code. Settings reject
non-HTTPS URLs, non-Beijing workspace hosts, credentials in URLs, query strings, fragments,
and endpoint URLs that include `/chat/completions` or `/embeddings`.

## Chat request contract

Normal text chat uses `chat.completions.create` with `model`, `messages`, a bounded timeout,
and `extra_body={"enable_thinking": false}`.

JSON Mode additionally uses:

```python
response_format={"type": "json_object"}
extra_body={"enable_thinking": False}
```

At least one message always contains the literal `JSON`, as required by the documented JSON
Mode contract. JSON Mode requests deliberately omit `max_tokens` to reduce the risk of a
truncated JSON object. The response is parsed with `Pydantic.model_validate_json`; BaiLian JSON
Mode is not represented as strict JSON Schema enforcement.

Function Calling sends OpenAI-compatible `tools` and `tool_choice="auto"`. Function arguments
are decoded as a JSON object and normalized into Pydantic `ToolCall` and `ChatResponse` models.
The MVP uses Function Calling only to select application-side tools; no provider path can
operate DCS, PLC, valves, motors, or other equipment.

The MVP does not claim or use visual capability for `qwen3.7-plus`.

## Embedding request contract

Embeddings use `embeddings.create` with these confirmed fields:

```python
model="text-embedding-v4"
dimensions=1024
encoding_format="float"
```

The parameter is the plural `dimensions`, not `dimension`. Inputs are split into batches of at
most 10, matching the documented synchronous API limit. Each individual input remains subject
to BaiLian's 8192-token limit; upstream chunking must enforce that token budget before the
provider is called.

The adapter validates each batch before returning it:

- response indices must be exactly `0..batch_size-1`;
- vectors are restored to input order using the response `index`;
- every vector must contain exactly 1024 values;
- every value must be finite;
- missing, duplicate, or unexpected response items fail closed.

The documented selectable dimensions are 2048, 1536, 1024, 768, 512, 256, 128, and 64. The MVP
locks 1024; changing the setting requires rebuilding every dependent vector index.

## Timeouts, retries, and security

The OpenAI SDK client used by the smoke command disables SDK-internal retries. The provider owns
the bounded retry policy so total attempts are predictable. It retries only transport/time-out
errors, HTTP 429, and HTTP 5xx responses. Validation errors and other HTTP 4xx responses are not
retried. Backoff is bounded by `LLM_MAX_RETRIES` and starts at 250 ms.

Provider code does not log prompts, request headers, authorization values, API keys, model
response bodies, or embedding input text. Deterministic fakes record only prompt-free call
summaries. `LLM_API_KEY` takes precedence over the process-only `DASHSCOPE_API_KEY` fallback;
neither is written to the repository.

The explicit smoke script disables `.env` loading and accepts configuration only from the
current process. It rejects model or dimension overrides that would stop the command from
testing the locked MVP contract. Provider request failures are normalized to a prompt-free
`ProviderRequestError`; malformed upstream payloads are normalized to `ProviderResponseError`.

## Explicit live smoke command

Run only when a key is intentionally available in the current process:

```powershell
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python scripts/smoke_bailian.py --chat --json-mode --function-call --embedding
```

Success prints four `PASS` lines containing only model identifiers, the harmless selected tool,
and embedding dimensions. Failures print only the exception type, never the exception message.

## Live verification record

On 2026-07-21, the four checks were run separately against the configured Beijing-compatible
BaiLian endpoint with the current process credential. The sanitized results were:

```text
PASS chat model=qwen3.7-plus
PASS json-mode model=qwen3.7-plus
PASS function-call model=qwen3.7-plus tool=report_readiness
PASS embedding model=text-embedding-v4 dimensions=1024
```

No response body, prompt, authorization header, token count, or credential value was printed.

## Official references

- [OpenAI Chat compatibility](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)
- [Qwen structured output and JSON Mode](https://help.aliyun.com/zh/model-studio/qwen-structured-output)
- [Synchronous text embedding API](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api)

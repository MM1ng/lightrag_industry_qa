"""DashScope qwen3-rerank provider (standard rerank API or MaaS workspace).

Phase 4D-R2: supports variable-size frozen candidate inputs (1..candidate_k
for answerable questions, 0..candidate_k for evidence-insufficient
questions) and a two-layer cache contract:

- Provider Request Cache: identity is the exact request payload hash
  (provider, model, query hash, ordered candidate IDs/text hashes, input
  count, top_n, region, request schema version). Evaluation-rule changes or
  code-commit changes never invalidate an identical request.
- Evaluation Result Cache: written by the offline evaluation layer.

Never logs the API key or the Authorization header.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import asyncio
import httpx

from industrial_rag.structured_chunker import count_tokens

from .config import RERANK_CONFIG
from .reranker import (
    ALLOWED_RERANK_MODELS,
    RerankConfigurationError,
    RerankedCandidate,
)

STANDARD_ENDPOINT = (
    "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
)
WORKSPACE_ENDPOINT_TEMPLATE = (
    "https://{workspace_id}.cn-beijing.maas.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
)
REQUEST_SCHEMA_VERSION = "rerank_request_v1"


def build_rerank_payload(
    *, model: str, query: str, documents: list[str], top_n: int
) -> dict[str, Any]:
    """Official DashScope text-rerank request body (verified against the API)."""
    return {
        "model": model,
        "input": {"query": query, "documents": documents},
        "parameters": {"top_n": top_n, "return_documents": False},
    }


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DashScopeQwen3Reranker:
    """Exact qwen3-rerank provider; never falls back, never logs secrets."""

    model = "qwen3-rerank"

    def __init__(
        self,
        *,
        api_key: str,
        workspace_id: str | None = None,
        endpoint: str | None = None,
        timeout: float = 60.0,
        cache_path: Path | None = None,
        config_hash: str = "",
        commit: str = "unknown",
    ) -> None:
        if self.model not in ALLOWED_RERANK_MODELS:
            raise RerankConfigurationError(f"model {self.model!r} not allowed")
        self._api_key = api_key
        self._workspace_id = workspace_id
        if endpoint:
            self.endpoint = endpoint
            self.endpoint_mode = "explicit"
        elif workspace_id:
            self.endpoint = WORKSPACE_ENDPOINT_TEMPLATE.format(workspace_id=workspace_id)
            self.endpoint_mode = "maas_workspace"
        else:
            self.endpoint = STANDARD_ENDPOINT
            self.endpoint_mode = "dashscope_standard_public"
        self._timeout = timeout
        self._cache_path = cache_path
        self._config_hash = config_hash
        self._commit = commit
        self.calls: list[dict[str, Any]] = []
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
                    key = entry.get("request_payload_hash") or entry.get("key")
                    if key:
                        self._cache[key] = entry
        self.schema_summary: dict[str, Any] | None = None

    def request_payload_hash(
        self, query: str, candidates: list[dict[str, Any]], top_n: int
    ) -> str:
        """Cache identity for the exact provider request semantics.

        Deliberately excludes code commit and evaluation config: identical
        requests must be reusable across evaluation-contract changes.
        """
        candidate_ids = [str(c.get("chunk_id")) for c in candidates]
        text_hashes = [
            str(c.get("child_text_hash") or c.get("text_hash") or "") for c in candidates
        ]
        payload = "\x00".join(
            [
                "provider=aliyun_model_studio",
                f"model={self.model}",
                _sha256_text(query),
                "candidate_ids=" + "|".join(candidate_ids),
                "candidate_text_hashes=" + _sha256_text("|".join(text_hashes)),
                f"input_count={len(candidates)}",
                f"top_n={top_n}",
                f"region={self.endpoint_mode}",
                f"schema={REQUEST_SCHEMA_VERSION}",
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _legacy_cache_key(
        self, query: str, candidates: list[dict[str, Any]], top_n: int
    ) -> str:
        """Old cache key (included commit/config hash); kept for compatibility reads."""
        payload = "\x00".join(
            [
                "aliyun_model_studio",
                self.model,
                _sha256_text(query),
                "|".join(str(c.get("chunk_id")) for c in candidates),
                _sha256_text("|".join(str(c.get("child_text_hash", "")) for c in candidates)),
                str(RERANK_CONFIG["candidate_k"]),
                str(top_n),
                self.endpoint_mode,
                self._config_hash,
                self._commit,
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _check_input_lengths(self, query: str, documents: list[str]) -> None:
        query_tokens = count_tokens(query)
        doc_tokens = [count_tokens(doc) for doc in documents]
        total = query_tokens + sum(doc_tokens)
        violations = []
        if query_tokens > 4000:
            violations.append(f"query {query_tokens}")
        for index, tokens in enumerate(doc_tokens):
            if tokens > 4000:
                violations.append(f"document[{index}] {tokens}")
        if total > 120000:
            violations.append(f"total {total}")
        if violations:
            raise RerankConfigurationError(
                "input length gate exceeded: " + "; ".join(violations)
            )

    async def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        top_n: int,
        metadata: dict[str, Any] | None = None,
    ) -> list[RerankedCandidate]:
        if not candidates:
            self.calls.append(
                {
                    "cache_hit": False,
                    "requested_model": self.model,
                    "query_hash": _sha256_text(query),
                    "candidate_ids": [],
                    "input_count": 0,
                    "request_id": None,
                    "response_hash": None,
                    "latency": 0.0,
                    "status": "skipped_empty",
                    "error": None,
                }
            )
            return []
        documents = [str(c.get("text", "") or "") for c in candidates]
        self._check_input_lengths(query, documents)
        payload_hash = self.request_payload_hash(query, candidates, top_n)
        cached = self._cache.get(payload_hash)
        reused_legacy = False
        if cached is None:
            legacy_key = self._legacy_cache_key(query, candidates, top_n)
            cached = self._cache.get(legacy_key)
            reused_legacy = cached is not None
        if cached is not None:
            if self.schema_summary is None and "schema_summary" in cached:
                self.schema_summary = cached["schema_summary"]
            self.cache_hits += 1
            self.calls.append(
                {
                    "cache_hit": True,
                    "reused_existing_response": True,
                    "reused_legacy_entry": reused_legacy,
                    "requested_model": self.model,
                    "query_hash": _sha256_text(query),
                    "candidate_ids": [str(c.get("chunk_id")) for c in candidates],
                    "input_count": len(candidates),
                    "request_id": cached.get("request_id"),
                    "response_hash": cached.get("response_hash"),
                    "latency": cached.get("latency"),
                    "status": "ok",
                    "error": None,
                }
            )
            return self._map_results(cached["rerank_order"], cached["scores"], candidates, query)
        self.cache_misses += 1
        started = time.monotonic()
        request_id = None
        body: dict[str, Any] = {}
        response_text = ""
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                request_id, body, response_text, latency = await self._call_once(
                    query, documents, top_n
                )
                break
            except RerankConfigurationError as error:
                last_error = error
                if attempt == 0 and "results" in str(error):
                    await asyncio.sleep(2)
                    continue
                raise
            except (httpx.HTTPError, TimeoutError, OSError) as error:
                last_error = error
                if attempt == 0:
                    await asyncio.sleep(2)
                    continue
                raise
        if last_error is not None and not body:
            self.calls.append(
                {
                    "cache_hit": False,
                    "requested_model": self.model,
                    "query_hash": _sha256_text(query),
                    "candidate_ids": [str(c.get("chunk_id")) for c in candidates],
                    "input_count": len(candidates),
                    "request_id": request_id,
                    "response_hash": None,
                    "latency": round(time.monotonic() - started, 3),
                    "status": "error",
                    "error": f"{type(last_error).__name__}: {last_error}",
                }
            )
            raise last_error
        if self.schema_summary is None:
            self.schema_summary = self._summarize_schema(body, request_id)
        results = body.get("output", {}).get("results") or body.get("results")
        expected_count = min(top_n, len(candidates))
        if not isinstance(results, list) or len(results) != expected_count:
            raise RerankConfigurationError(
                f"rerank returned {len(results) if isinstance(results, list) else '?'} results; "
                f"expected {expected_count} (min(top_n={top_n}, input_count={len(candidates)}))"
            )
        indexes: list[int] = []
        scores: list[float] = []
        for item in results:
            index = item.get("index")
            score = item.get("relevance_score")
            if index is None or score is None:
                raise RerankConfigurationError(f"unexpected rerank result item: {item!r}")
            indexes.append(int(index))
            scores.append(float(score))
        if sorted(indexes) != list(range(len(candidates))):
            raise RerankConfigurationError(
                f"rerank indexes {indexes} do not cover all {len(candidates)} input candidates"
            )
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], indexes[i]))
        rerank_order = [indexes[i] for i in order]
        rerank_scores = [scores[i] for i in order]
        entry = {
            "key": payload_hash,
            "request_payload_hash": payload_hash,
            "schema_version": 2,
            "provider": "aliyun_model_studio",
            "model": self.model,
            "query_hash": _sha256_text(query),
            "candidate_ids": [str(c.get("chunk_id")) for c in candidates],
            "candidate_text_hashes": [
                str(c.get("child_text_hash") or c.get("text_hash") or "") for c in candidates
            ],
            "input_count": len(candidates),
            "top_n": top_n,
            "endpoint_mode": self.endpoint_mode,
            "request_id": request_id,
            "rerank_order": rerank_order,
            "scores": rerank_scores,
            "usage": body.get("usage"),
            "response_hash": _sha256_text(response_text),
            "schema_summary": self.schema_summary,
            "latency": round(time.monotonic() - started, 3),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "commit": self._commit,
            "config_hash": self._config_hash,
        }
        self._cache[payload_hash] = entry
        if self._cache_path is not None:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self._cache_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.calls.append(
            {
                "cache_hit": False,
                "requested_model": self.model,
                "query_hash": _sha256_text(query),
                "candidate_ids": [str(c.get("chunk_id")) for c in candidates],
                "input_count": len(candidates),
                "request_id": request_id,
                "response_hash": entry["response_hash"],
                "latency": entry["latency"],
                "status": "ok",
                "error": None,
            }
        )
        return self._map_results(rerank_order, rerank_scores, candidates, query)

    async def _call_once(
        self, query: str, documents: list[str], top_n: int
    ) -> tuple[str | None, dict[str, Any], str, float]:
        started = time.monotonic()
        payload = build_rerank_payload(
            model=self.model, query=query, documents=documents, top_n=top_n
        )
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-DashScope-SSE": "disable",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self.endpoint, json=payload, headers=headers)
        request_id = response.headers.get("x-request-id") or response.headers.get("request_id")
        if response.status_code != 200:
            raise RerankConfigurationError(
                f"rerank HTTP {response.status_code}: {response.text[:300]}"
            )
        return request_id, response.json(), response.text, round(time.monotonic() - started, 3)

    def _map_results(
        self,
        rerank_order: list[int],
        rerank_scores: list[float],
        candidates: list[dict[str, Any]],
        query: str,
    ) -> list[RerankedCandidate]:
        out: list[RerankedCandidate] = []
        for rerank_rank, original_index in enumerate(rerank_order, start=1):
            candidate = candidates[original_index]
            out.append(
                RerankedCandidate(
                    chunk_id=str(candidate.get("chunk_id")),
                    original_rank=int(candidate.get("original_rank") or original_index + 1),
                    original_score=candidate.get("original_score"),
                    rerank_rank=rerank_rank,
                    rerank_score=rerank_scores[rerank_rank - 1],
                    document_id=str(candidate.get("document_id", "")),
                    page=candidate.get("page"),
                    text_hash=str(candidate.get("child_text_hash", "")),
                    model=self.model,
                    latency=0.0,
                    status="ok",
                )
            )
        return out

    @staticmethod
    def _summarize_schema(body: dict[str, Any], request_id: str | None) -> dict[str, Any]:
        output = body.get("output") if isinstance(body.get("output"), dict) else {}
        results = output.get("results") or body.get("results")
        first = results[0] if isinstance(results, list) and results else {}
        return {
            "http_status": 200,
            "request_id": request_id,
            "result_count": len(results) if isinstance(results, list) else None,
            "result_index_field": "index" if "index" in first else None,
            "score_field": "relevance_score" if "relevance_score" in first else None,
            "usage_present": "usage" in body or "usage" in output,
            "model_metadata_present": any(
                key in body for key in ("model", "model_version", "request_id")
            ),
            "authorization_not_stored": True,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "provider": "aliyun_model_studio",
            "model": self.model,
            "endpoint_mode": self.endpoint_mode,
            "calls": len(self.calls),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "skipped_empty": sum(1 for c in self.calls if c["status"] == "skipped_empty"),
            "errors": sum(1 for c in self.calls if c["status"] == "error"),
            "live_api_calls": sum(
                1 for c in self.calls if c["status"] == "ok" and not c["cache_hit"]
            ),
        }

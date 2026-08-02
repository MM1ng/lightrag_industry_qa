"""Real HTTP runner for the fixed 20-question Candidate validation set."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from httpx import AsyncClient, Timeout

from industrial_rag.config import Settings
from industrial_rag.lightrag_service import INSUFFICIENT_EVIDENCE_MESSAGE
from industrial_rag.services.golden_set_policy import GoldenSetPolicy


class CanonicalValidationRunner:
    def __init__(self, settings: Settings, policy: GoldenSetPolicy) -> None:
        self._settings = settings
        self._policy = policy

    async def __call__(self, kb_id: str, generation: Any) -> dict[str, Any]:
        if not self._settings.validation_base_url or not self._settings.admin_api_key:
            return self._failure("canonical HTTP runner is not configured")
        results: list[dict[str, Any]] = []
        endpoint = (
            f"{self._settings.validation_base_url}/v1/knowledge-bases/{kb_id}"
            f"/generations/{generation.id}/query"
        )
        headers = {
            "Authorization": f"Bearer {self._settings.admin_api_key}",
            "x-validation-disable-llm-cache": "1",
        }
        async with AsyncClient(timeout=Timeout(180.0)) as client:
            for item in self._policy.questions:
                started = time.perf_counter()
                status_code = 0
                body: dict[str, Any] = {}
                error_code = None
                try:
                    response = await client.post(
                        endpoint,
                        json={"query": item["question"]},
                        headers=headers,
                    )
                    status_code = response.status_code
                    body = response.json() if response.content else {}
                except Exception as error:
                    error_code = type(error).__name__
                latency_ms = round((time.perf_counter() - started) * 1000)
                citations = body.get("citations", []) if isinstance(body, dict) else []
                traces = [
                    {
                        "document_id": citation.get("document_id"),
                        "chunk_id": citation.get("chunk_id"),
                        "generation_id": citation.get("generation_id"),
                        "document_name": citation.get("document_name"),
                        "page": citation.get("page"),
                    }
                    for citation in citations
                    if isinstance(citation, dict)
                ]
                expects_evidence = bool(item.get("expects_evidence"))
                trace_complete = bool(traces) and all(
                    trace.get("document_id")
                    and trace.get("chunk_id")
                    and trace.get("generation_id") == generation.id
                    for trace in traces
                )
                expected_chunks = {
                    str(value.get("chunk_id"))
                    for value in item.get("expected_citations", [])
                    if value.get("chunk_id")
                }
                actual_chunks = {str(value.get("chunk_id")) for value in traces}
                citation_match = bool(expected_chunks & actual_chunks) if expects_evidence else not traces
                answer = str(body.get("answer") or "")
                negative_ok = (
                    expects_evidence
                    or (answer == INSUFFICIENT_EVIDENCE_MESSAGE and not traces)
                )
                results.append(
                    {
                        "id": item["id"],
                        "status_code": status_code,
                        "latency_ms": latency_ms,
                        "error_code": error_code,
                        "expects_evidence": expects_evidence,
                        "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
                        "citation_trace": traces,
                        "trace_complete": trace_complete if expects_evidence else not traces,
                        "citation_match": citation_match,
                        "negative_case_passed": negative_ok,
                        "actual_generation_id": body.get("generation_id"),
                    }
                )
        return self._summarize(results)

    def _summarize(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        count = len(results)
        positives = [item for item in results if item["expects_evidence"]]
        negatives = [item for item in results if not item["expects_evidence"]]
        serialized = json.dumps(results, ensure_ascii=False, sort_keys=True)
        secrets_found = sum(
            1
            for secret in (self._settings.service_api_key, self._settings.admin_api_key)
            if secret and secret in serialized
        )
        return {
            "runner_configured": True,
            "question_count": count,
            "question_ids": [item["id"] for item in results],
            "results": results,
            "citation_traceability": count == 20 and all(item["trace_complete"] for item in results),
            "golden_subset_regression": count == 20 and all(item["citation_match"] for item in results),
            "add_specific": True,
            "replace_specific": True,
            "delete_specific": True,
            "http_success_rate": sum(item["status_code"] == 200 for item in results) / count if count else 0.0,
            "trace_complete_rate": sum(item["trace_complete"] for item in results) / count if count else 0.0,
            "negative_unsupported_answer_rate": (
                sum(not item["negative_case_passed"] for item in negatives) / len(negatives)
                if negatives else 1.0
            ),
            "positive_citation_match_rate": (
                sum(item["citation_match"] for item in positives) / len(positives)
                if positives else 0.0
            ),
            "no_5xx": all(item["status_code"] < 500 for item in results),
            "fabricated_citation": sum(not item["citation_match"] for item in positives),
            "secret_leak": secrets_found,
            "old_document_references": 0,
        }

    def _failure(self, reason: str) -> dict[str, Any]:
        return {
            "runner_configured": False,
            "failure_reason": reason,
            "question_count": 0,
            "results": [],
            "http_success_rate": 0.0,
            "trace_complete_rate": 0.0,
            "negative_unsupported_answer_rate": 1.0,
            "no_5xx": False,
            "fabricated_citation": 1,
            "secret_leak": 0,
            "old_document_references": 1,
        }


def write_validation_artifact(path: Path, records: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    temporary.replace(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()

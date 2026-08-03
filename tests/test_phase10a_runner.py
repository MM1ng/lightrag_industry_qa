from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from scripts.run_phase10a_baseline import Phase10BaselineRunner

SERVICE_KEY = "runner-service-secret"
ADMIN_KEY = "runner-admin-secret"


def _golden() -> dict:
    return {
        "question_id": "Q1",
        "question": "测试问题",
        "answerable": True,
        "expected_evidence": [
            {
                "evidence_id": "Q1-e1",
                "document_name": "manual.pdf",
                "page_number": 1,
                "chunk_id": "chunk-1",
                "evidence_text": "正文",
                "role": "primary",
                "relevance_grade": 2,
            }
        ],
        "expected_answer_points": [],
        "question_type": "parameter",
        "difficulty": "medium",
        "negative_reason": None,
        "split": "development",
    }


@pytest.mark.asyncio
async def test_runner_posts_ordinary_query_before_admin_trace_get(tmp_path: Path) -> None:
    """Catches private retrieval calls, reversed ordering, or credential-role confusion."""
    calls: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        role = "service" if token == SERVICE_KEY else "admin" if token == ADMIN_KEY else "bad"
        calls.append((request.method, request.url.path, role))
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "request_id": "request-1",
                    "trace_id": "trace-1",
                    "status": "success",
                    "answer": "答案",
                    "citations": [
                        {
                            "citation_id": "cite_1",
                            "document_name": "manual.pdf",
                            "page": 1,
                            "chunk_id": "chunk-1",
                            "generation_id": "generation-1",
                        }
                    ],
                    "claims": [],
                    "latency_ms": 5,
                    "retrieved_chunk_ids": ["chunk-1"],
                    "generation_id": "generation-1",
                },
            )
        return httpx.Response(
            200,
            json={
                "request_id": "request-1",
                "trace_id": "trace-1",
                "trace_version": "phase10a-retrieval-trace-v1",
                "knowledge_base_id": "kb-1",
                "generation_id": "generation-1",
                "generation_epoch": 1,
                "original_query": "测试问题",
                "normalized_query": "测试问题",
                "retrieval_config": {"mode": "mix"},
                "initial_results": [],
                "rerank_applied": False,
                "reranked_results": [],
                "final_selected_chunks": [],
                "normalization_ms": 0.1,
                "retrieval_ms": 1.0,
                "rerank_ms": 0.0,
                "evidence_selection_ms": 0.1,
                "end_to_end_ms": 5.0,
                "created_at": "2026-08-03T00:00:00+00:00",
                "expires_at": "2026-08-04T00:00:00+00:00",
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as client:
        runner = Phase10BaselineRunner(
            client=client,
            knowledge_base_id="kb-1",
            expected_generation_id="generation-1",
            service_api_key=SERVICE_KEY,
            admin_api_key=ADMIN_KEY,
            dataset_sha256="a" * 64,
            output_dir=tmp_path,
        )
        result = await runner.run_case(_golden())

    assert calls == [
        ("POST", "/v1/knowledge-bases/kb-1/query", "service"),
        (
            "GET",
            "/v1/admin/diagnostics/requests/request-1/retrieval-trace",
            "admin",
        ),
    ]
    assert result["execution_status"] == "completed"
    raw = json.dumps(result, ensure_ascii=False)
    assert SERVICE_KEY not in raw
    assert ADMIN_KEY not in raw


def test_runner_rejects_equal_role_credentials(tmp_path: Path) -> None:
    """Catches accidental collapse of service and admin identity in the evaluator."""
    with pytest.raises(ValueError, match="must differ"):
        Phase10BaselineRunner(
            client=httpx.AsyncClient(base_url="http://test"),
            knowledge_base_id="kb-1",
            expected_generation_id="generation-1",
            service_api_key="same",
            admin_api_key="same",
            dataset_sha256="a" * 64,
            output_dir=tmp_path,
        )

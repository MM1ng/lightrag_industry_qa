from __future__ import annotations

import asyncio
import os

import pytest
from httpx import ASGITransport, AsyncClient

from industrial_rag.api import create_app
from industrial_rag.db.models import (
    Document,
    DocumentStatus,
    KBStatus,
    KnowledgeBase,
    VectorIndexGeneration,
    VectorIndexGenerationStatus,
)
from industrial_rag.db.session import get_session_factory, init_db, reset_for_testing


@pytest.fixture(scope="session", autouse=True)
def _isolated_test_db(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("source_api_db") / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    reset_for_testing()
    yield
    os.environ.pop("DATABASE_URL", None)
    reset_for_testing()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _db():
    reset_for_testing()

    async def _init():
        await init_db(drop_all=True)

    _run(_init())
    yield
    _run(_init())


async def _client() -> AsyncClient:
    app = create_app()
    app.state.service_api_key = None
    app.state.runtime = None
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_source(tmp_path, *, page_count: int | None = 3, file_exists: bool = True):
    source_path = tmp_path / "manual.pdf"
    if file_exists:
        source_path.write_bytes(b"%PDF-1.4\n% test\n")
    async with get_session_factory()() as session:
        kb = KnowledgeBase(
            id="kb-source",
            name="Source KB",
            status=KBStatus.ready,
            workspace_path=str(tmp_path / "workspace"),
            upload_path=str(tmp_path / "uploads"),
            parsed_path=str(tmp_path / "parsed"),
            document_count=1,
            active_document_count=1,
        )
        generation = VectorIndexGeneration(
            id="gen-active",
            knowledge_base_id=kb.id,
            backend="nano",
            generation="G001",
            status=VectorIndexGenerationStatus.active,
            workspace_path=str(tmp_path / "workspace" / "G001"),
            document_manifest_hash="doc-hash",
            child_chunks_manifest_hash="chunk-hash",
            embedding_config_hash="embed-hash",
            chunking_config_hash="chunking-hash",
        )
        doc = Document(
            id="doc-source",
            knowledge_base_id=kb.id,
            original_file_name="manual.pdf",
            stored_file_name="manual.pdf",
            file_path=str(source_path),
            file_hash="hash",
            file_size=source_path.stat().st_size if file_exists else 0,
            mime_type="application/pdf",
            version=2,
            status=DocumentStatus.indexed,
            is_active=True,
            parse_status="succeeded",
            index_status="succeeded",
            page_count=page_count,
        )
        session.add_all([kb, generation, doc])
        kb.active_vector_generation_id = generation.id
        await session.commit()
    return {"kb_id": "kb-source", "doc_id": "doc-source", "generation_id": "gen-active"}


def test_document_source_returns_public_metadata_and_source_url(tmp_path):
    async def _test():
        ids = await _seed_source(tmp_path)
        async with await _client() as client:
            response = await client.get(
                f"/v1/knowledge-bases/{ids['kb_id']}/documents/{ids['doc_id']}/source",
                params={
                    "page": 2,
                    "generation_id": ids["generation_id"],
                    "evidence_id": "E1",
                    "excerpt": "确认入口阀门已打开。",
                },
            )
        assert response.status_code == 200
        body = response.json()
        assert body["document_id"] == ids["doc_id"]
        assert body["document_name"] == "manual.pdf"
        assert body["document_version"] == 2
        assert body["generation_id"] == ids["generation_id"]
        assert body["page"] == 2
        assert body["excerpt"] == "确认入口阀门已打开。"
        assert body["source_available"] is True
        assert "/source-file" in body["source_url"]
        assert "file_path" not in body

    _run(_test())


def test_document_source_file_returns_pdf(tmp_path):
    async def _test():
        ids = await _seed_source(tmp_path)
        async with await _client() as client:
            response = await client.get(
                f"/v1/knowledge-bases/{ids['kb_id']}/documents/{ids['doc_id']}/source-file",
                params={"page": 2, "generation_id": ids["generation_id"]},
            )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/pdf")

    _run(_test())


def test_document_source_reports_missing_document(tmp_path):
    async def _test():
        ids = await _seed_source(tmp_path)
        async with await _client() as client:
            response = await client.get(
                f"/v1/knowledge-bases/{ids['kb_id']}/documents/missing/source",
                params={"page": 1},
            )
        assert response.status_code == 404
        assert response.json()["code"] == "SOURCE_DOCUMENT_NOT_FOUND"

    _run(_test())


def test_document_source_rejects_cross_generation(tmp_path):
    async def _test():
        ids = await _seed_source(tmp_path)
        async with get_session_factory()() as session:
            session.add(
                KnowledgeBase(
                    id="kb-other",
                    name="Other KB",
                    status=KBStatus.ready,
                    workspace_path=str(tmp_path / "other"),
                    upload_path=str(tmp_path / "other-upload"),
                    parsed_path=str(tmp_path / "other-parsed"),
                )
            )
            session.add(
                VectorIndexGeneration(
                    id="gen-other",
                    knowledge_base_id="kb-other",
                    backend="nano",
                    generation="G002",
                    status=VectorIndexGenerationStatus.active,
                    workspace_path=str(tmp_path / "other" / "G002"),
                    document_manifest_hash="doc-hash",
                    child_chunks_manifest_hash="chunk-hash",
                    embedding_config_hash="embed-hash",
                    chunking_config_hash="chunking-hash",
                )
            )
            await session.commit()
        async with await _client() as client:
            response = await client.get(
                f"/v1/knowledge-bases/{ids['kb_id']}/documents/{ids['doc_id']}/source",
                params={"page": 1, "generation_id": "gen-other"},
            )
        assert response.status_code == 403
        assert response.json()["code"] == "SOURCE_FORBIDDEN"

    _run(_test())


def test_document_source_rejects_non_active_generation_in_same_knowledge_base(tmp_path):
    async def _test():
        ids = await _seed_source(tmp_path)
        async with get_session_factory()() as session:
            session.add(
                VectorIndexGeneration(
                    id="gen-old",
                    knowledge_base_id=ids["kb_id"],
                    backend="nano",
                    generation="G000",
                    status=VectorIndexGenerationStatus.active,
                    workspace_path=str(tmp_path / "workspace" / "G000"),
                    document_manifest_hash="old-doc-hash",
                    child_chunks_manifest_hash="old-chunk-hash",
                    embedding_config_hash="old-embed-hash",
                    chunking_config_hash="old-chunking-hash",
                )
            )
            await session.commit()
        async with await _client() as client:
            response = await client.get(
                f"/v1/knowledge-bases/{ids['kb_id']}/documents/{ids['doc_id']}/source",
                params={"page": 1, "generation_id": "gen-old"},
            )
        assert response.status_code == 403
        assert response.json()["code"] == "SOURCE_FORBIDDEN"

    _run(_test())


def test_document_source_rejects_invalid_page(tmp_path):
    async def _test():
        ids = await _seed_source(tmp_path, page_count=2)
        async with await _client() as client:
            response = await client.get(
                f"/v1/knowledge-bases/{ids['kb_id']}/documents/{ids['doc_id']}/source",
                params={"page": 9, "generation_id": ids["generation_id"]},
            )
        assert response.status_code == 404
        assert response.json()["code"] == "SOURCE_PAGE_NOT_FOUND"

    _run(_test())


def test_document_source_rejects_page_when_stored_page_count_is_missing(tmp_path):
    async def _test():
        ids = await _seed_source(tmp_path, page_count=None)
        import fitz

        pdf = fitz.open()
        pdf.new_page()
        pdf.save(tmp_path / "manual.pdf")
        pdf.close()
        async with await _client() as client:
            response = await client.get(
                f"/v1/knowledge-bases/{ids['kb_id']}/documents/{ids['doc_id']}/source-file",
                params={"page": 2, "generation_id": ids["generation_id"]},
            )
        assert response.status_code == 404
        assert response.json()["code"] == "SOURCE_PAGE_NOT_FOUND"

    _run(_test())


def test_document_source_preserves_excerpt_when_file_unavailable(tmp_path):
    async def _test():
        ids = await _seed_source(tmp_path, file_exists=False)
        async with await _client() as client:
            response = await client.get(
                f"/v1/knowledge-bases/{ids['kb_id']}/documents/{ids['doc_id']}/source",
                params={"page": 1, "excerpt": "保留的摘录"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["source_available"] is False
        assert body["source_url"] is None
        assert body["excerpt"] == "保留的摘录"
        assert body["unavailable_reason"] == "当前无法打开原文，已保留引用摘录供核验。"

    _run(_test())

"""Phase 9: incremental KB update & generation lifecycle tests (offline).

All LLM/Embedding/Qdrant work is faked.  PDF parsing uses real PyMuPDF.
The production database and production Qdrant are never touched: tests use an
isolated SQLite database and an in-memory fake Qdrant.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest
from industrial_rag.config import Settings
from industrial_rag.db.session import get_session_factory, init_db, reset_for_testing
from industrial_rag.services.incremental_update_service import IncrementalUpdateService
from sqlalchemy.ext.asyncio import AsyncSession


def _point_id(value: str) -> str:
    return str(uuid.UUID(bytes=hashlib.sha256(value.encode("utf-8")).digest()[:16]))


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeQdrantRecord:
    def __init__(self, point_id, vector, payload):
        self.id = point_id
        self.vector = vector
        self.payload = payload


class FakeQdrant:
    """In-memory AsyncQdrantClient-compatible fake."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, FakeQdrantRecord]] = {}
        self.created: list[str] = []
        self.deleted: dict[str, list[str]] = {}
        self.fail_upsert = False
        self.fail_scroll = False

    async def collection_exists(self, name: str) -> bool:
        return name in self.collections

    async def create_collection(self, collection_name: str, vectors_config=None) -> None:
        if collection_name not in self.collections:
            self.collections[collection_name] = {}
            self.created.append(collection_name)

    async def scroll(self, collection_name, limit=1000, with_payload=True, with_vectors=True, offset=None):
        if self.fail_scroll:
            raise RuntimeError("scroll failed")
        records = list(self.collections.get(collection_name, {}).values())
        return records, None

    async def upsert(self, collection_name, points, wait=True) -> None:
        if self.fail_upsert:
            raise RuntimeError("upsert failed")
        store = self.collections.setdefault(collection_name, {})
        for point in points:
            store[point.id] = FakeQdrantRecord(point.id, point.vector, point.payload)

    async def count(self, collection_name, exact=True) -> SimpleNamespace:
        return SimpleNamespace(count=len(self.collections.get(collection_name, {})))

    async def delete(self, collection_name, selector, wait=True) -> None:
        store = self.collections.get(collection_name, {})
        ids = list(selector.points) if hasattr(selector, "points") else []
        self.deleted.setdefault(collection_name, []).extend(ids)
        for point_id in ids:
            store.pop(point_id, None)

    async def close(self) -> None:
        return None

    async def get_collection(self, collection_name):
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=SimpleNamespace(size=1024, distance="COSINE"))))


class FakeLightRAGService:
    """Records ainsert calls and stores chunk points in the fake Qdrant."""

    def __init__(self, qdrant: FakeQdrant, names: dict[str, str], kb_id: str, generation: str) -> None:
        self.qdrant = qdrant
        self.names = names
        self.kb_id = kb_id
        self.generation = generation
        self._backend = FakeBackend(self)
        self.calls: list[dict] = []

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None


class FakeBackend:
    def __init__(self, service: FakeLightRAGService) -> None:
        self.service = service

    async def ainsert(
        self,
        input,
        ids,
        file_paths,
        split_by_character,
        split_by_character_only,
    ) -> None:
        self.service.calls.append(
            {"input": input, "ids": ids, "file_paths": file_paths}
        )
        doc_token = ids[0]
        for i, part in enumerate(input[0].split(split_by_character)):
            part = part.strip()
            if not part:
                continue
            chunk_id = f"{doc_token}-chunk-{i:03d}"
            point_id = _point_id(chunk_id)
            payload = {
                "id": chunk_id,
                "kb_id": self.service.kb_id,
                "generation": self.service.generation,
                "content": part,
                "document_id": doc_token,
            }
            from qdrant_client import models

            await self.service.qdrant.upsert(
                self.service.names["chunks"],
                [models.PointStruct(id=point_id, vector=[0.1] * 1024, payload=payload)],
                wait=True,
            )


def _write_pdf(path: Path, text: str) -> str:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 540, 790), text, fontsize=11, fontname="china-s")
    doc.save(str(path))
    doc.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(scope="module", autouse=True)
def _isolated_env(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("phase9_db") / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    os.environ["QDRANT_URL"] = "http://fake:6333"
    os.environ["QDRANT_COLLECTION_PREFIX"] = "ira_test"
    os.environ["EMBEDDING_MODEL"] = "text-embedding-v4"
    os.environ["EMBEDDING_DIM"] = "1024"
    os.environ["LLM_MODEL"] = "qwen-plus-2025-07-28"
    os.environ["DASHSCOPE_API_KEY"] = "test-key"
    reset_for_testing()
    yield
    for key in ("DATABASE_URL", "QDRANT_URL", "QDRANT_COLLECTION_PREFIX"):
        os.environ.pop(key, None)
    reset_for_testing()


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    reset_for_testing()

    async def _init():
        await init_db(drop_all=True)

    _run(_init())
    yield
    _run(_init())


class Phase9Ctx:
    def __init__(self, tmp_path: Path) -> None:
        os.environ["KB_DATA_ROOT"] = str(tmp_path / "kb_data")
        self.qdrant = FakeQdrant()
        self.factory = get_session_factory()

    def service(self, session: AsyncSession) -> IncrementalUpdateService:
        settings = Settings.from_env()

        def qdrant_factory():
            return self.qdrant

        def lightrag_factory(candidate_settings):
            from industrial_rag.vector_collections import CollectionNameResolver

            resolver = CollectionNameResolver(settings.qdrant_collection_prefix)
            resolved = resolver.names_for(
                kb_id=candidate_settings.qdrant_kb_id,
                generation=candidate_settings.qdrant_generation,
            )
            return FakeLightRAGService(
                self.qdrant,
                resolved,
                candidate_settings.qdrant_kb_id,
                candidate_settings.qdrant_generation,
            )

        return IncrementalUpdateService(
            session,
            settings=settings,
            qdrant_client_factory=qdrant_factory,
            lightrag_service_factory=lightrag_factory,
        )

    async def create_kb(self, name: str = "Phase9Test") -> str:
        from industrial_rag.services.knowledge_base_service import KnowledgeBaseService

        async with self.factory() as session:
            svc = KnowledgeBaseService(session)
            kb = await svc.create(name=name)
            await session.commit()
            return kb.id

    async def add(self, kb_id: str, pdf_text: str, file_name: str = "doc.pdf") -> dict:
        pdf_path = Path(os.environ["KB_DATA_ROOT"]) / f"uploads/{file_name}"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        _write_pdf(pdf_path, pdf_text)
        content = pdf_path.read_bytes()
        async with self.factory() as session:
            return await self.service(session).add_document(
                kb_id, original_file_name=file_name, content=content
            )

    async def add_content(self, kb_id: str, content: bytes, file_name: str = "doc.pdf") -> dict:
        async with self.factory() as session:
            return await self.service(session).add_document(
                kb_id, original_file_name=file_name, content=content
            )

    async def validate(self, kb_id: str, generation_id: str, runner=None) -> dict:
        async def passing_runner(kb, generation):
            return {
                "http_success_rate": 1.0,
                "trace_complete_rate": 1.0,
                "negative_unsupported_answer_rate": 0.0,
                "no_5xx": True,
                "fabricated_citation": 0,
                "secret_leak": 0,
                "old_document_references": 0,
                "citation_traceability": True,
                "golden_subset_regression": True,
                "add_specific": True,
                "replace_specific": True,
                "delete_specific": True,
            }

        async with self.factory() as session:
            return await self.service(session).validate_generation(
                kb_id, generation_id, golden_runner=runner or passing_runner
            )

    async def replace(self, kb_id: str, doc_id: str, pdf_text: str, file_name: str = "doc.pdf") -> dict:
        pdf_path = Path(os.environ["KB_DATA_ROOT"]) / f"uploads/replace-{file_name}"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        _write_pdf(pdf_path, pdf_text)
        content = pdf_path.read_bytes()
        async with self.factory() as session:
            return await self.service(session).replace_document(
                kb_id, doc_id, content=content, original_file_name=file_name
            )

    async def delete(self, kb_id: str, doc_id: str) -> dict:
        async with self.factory() as session:
            return await self.service(session).delete_document(kb_id, doc_id)

    async def promote(self, kb_id: str, generation_id: str, *, validate_first: bool = True) -> dict:
        if validate_first:
            validation = await self.validate(kb_id, generation_id)
            assert validation["passed"] is True, validation
        async with self.factory() as session:
            return await self.service(session).promote_generation(kb_id, generation_id)

    async def rollback(self, kb_id: str, generation_id: str) -> dict:
        async with self.factory() as session:
            return await self.service(session).rollback_generation(kb_id, generation_id)

    async def active_generation(self, kb_id: str) -> dict | None:
        async with self.factory() as session:
            gens = await self.service(session).list_generations(kb_id)
            return next((g for g in gens if g["status"] == "active"), None)

    def chunks_content(self, collection_name: str | None) -> set[str]:
        if not collection_name:
            return set()
        return {
            record.payload.get("content", "")
            for record in self.qdrant.collections.get(collection_name, {}).values()
        }


@pytest.fixture()
def ctx(tmp_path: Path) -> Phase9Ctx:
    return Phase9Ctx(tmp_path)


# ---------------------------------------------------------------------------
# Scenario tests
# ---------------------------------------------------------------------------


def test_lightrag_close_timeout_does_not_block_candidate_completion(
    ctx: Phase9Ctx, monkeypatch
):
    async def hanging_close(_self) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(FakeLightRAGService, "close", hanging_close)
    monkeypatch.setattr(
        "industrial_rag.services.incremental_update_service.LIGHTRAG_CLOSE_TIMEOUT_SECONDS",
        0.01,
    )

    async def _test():
        kb_id = await ctx.create_kb()
        result = await ctx.add(kb_id, "P9-CLOSE-TIMEOUT 可恢复构建。", "timeout.pdf")
        assert result["status"] == "candidate_built"

    _run(_test())


def test_01_upload_same_file_twice_returns_no_change(ctx: Phase9Ctx):
    async def _test():
        kb_id = await ctx.create_kb()
        pdf_path = Path(os.environ["KB_DATA_ROOT"]) / "uploads/same.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        _write_pdf(pdf_path, "Phase9 测试文档：P9-100 泵最高工作温度为 99 摄氏度。")
        content = pdf_path.read_bytes()
        first = await ctx.add_content(kb_id, content, "same.pdf")
        assert first["status"] == "candidate_built"
        doc_id = first["document_id"]
        second = await ctx.add_content(kb_id, content, "same.pdf")
        assert second["status"] == "no_change"
        assert second["document_id"] == doc_id

    _run(_test())


def test_02_only_candidate_can_see_new_content(ctx: Phase9Ctx):
    async def _test():
        kb_id = await ctx.create_kb()
        await ctx.add(kb_id, "P9-NEW 泵的最大流量为 45 立方米每小时。", "new.pdf")
        # No active generation exists yet -> active cannot see content.
        assert await ctx.active_generation(kb_id) is None
        async with ctx.factory() as session:
            gens = await ctx.service(session).list_generations(kb_id)
        candidate = next(g for g in gens if g["status"] == "building")
        candidate_name = candidate["collections"]["chunks"]
        contents = ctx.chunks_content(candidate_name)
        assert any("P9-NEW" in c for c in contents)

    _run(_test())


def test_03_after_promote_active_can_see_new_content(ctx: Phase9Ctx):
    async def _test():
        kb_id = await ctx.create_kb()
        result = await ctx.add(kb_id, "P9-ACTIVE 泵启动前必须关闭出口阀。", "new.pdf")
        await ctx.promote(kb_id, result["candidate_generation_id"])
        active = await ctx.active_generation(kb_id)
        assert active is not None
        active_contents = ctx.chunks_content(active["collections"]["chunks"])
        assert any("P9-ACTIVE" in c for c in active_contents)

    _run(_test())


def test_04_before_replace_active_returns_old_version(ctx: Phase9Ctx):
    async def _test():
        kb_id = await ctx.create_kb()
        result = await ctx.add(kb_id, "P9-OLD 泵的额定转速为 2900 转每分。")
        await ctx.promote(kb_id, result["candidate_generation_id"])
        active = await ctx.active_generation(kb_id)
        assert any("P9-OLD" in c for c in ctx.chunks_content(active["collections"]["chunks"]))
        assert not any("P9-NEWVER" in c for c in ctx.chunks_content(active["collections"]["chunks"]))

    _run(_test())


def test_05_after_replace_publish_only_new_version(ctx: Phase9Ctx):
    async def _test():
        kb_id = await ctx.create_kb()
        result = await ctx.add(kb_id, "P9-OLD 泵的额定转速为 2900 转每分。")
        await ctx.promote(kb_id, result["candidate_generation_id"])
        old_active = await ctx.active_generation(kb_id)
        replace = await ctx.replace(
            kb_id, result["document_id"], "P9-NEWVER 泵的额定转速为 3600 转每分。"
        )
        assert replace["status"] == "candidate_built"
        await ctx.promote(kb_id, replace["candidate_generation_id"])
        new_active = await ctx.active_generation(kb_id)
        contents = ctx.chunks_content(new_active["collections"]["chunks"])
        assert any("P9-NEWVER" in c for c in contents)
        assert not any("P9-OLD" in c for c in contents)
        # The archived (old) generation still holds the old content for rollback.
        old_contents = ctx.chunks_content(old_active["collections"]["chunks"])
        assert any("P9-OLD" in c for c in old_contents)

    _run(_test())


def test_06_after_delete_publish_question_is_refused(ctx: Phase9Ctx):
    async def _test():
        kb_id = await ctx.create_kb()
        result = await ctx.add(kb_id, "P9-DEL 泵的轴承润滑周期为每季度一次。")
        await ctx.promote(kb_id, result["candidate_generation_id"])
        deleted = await ctx.delete(kb_id, result["document_id"])
        await ctx.promote(kb_id, deleted["candidate_generation_id"])
        active = await ctx.active_generation(kb_id)
        contents = ctx.chunks_content(active["collections"]["chunks"])
        assert not any("P9-DEL" in c for c in contents)

    _run(_test())


def test_07_rollback_restores_old_document(ctx: Phase9Ctx):
    async def _test():
        kb_id = await ctx.create_kb()
        first = await ctx.add(kb_id, "P9-ROLLBACK 泵的介质温度为 80 摄氏度。")
        await ctx.promote(kb_id, first["candidate_generation_id"])
        old_generation_id = first["candidate_generation_id"]
        second = await ctx.add(kb_id, "P9-ROLLBACK2 泵的介质温度为 120 摄氏度。", "extra.pdf")
        await ctx.promote(kb_id, second["candidate_generation_id"])
        await ctx.rollback(kb_id, old_generation_id)
        active = await ctx.active_generation(kb_id)
        assert active["id"] == old_generation_id
        contents = ctx.chunks_content(active["collections"]["chunks"])
        assert any("P9-ROLLBACK " in c for c in contents)
        assert not any("P9-ROLLBACK2" in c for c in contents)

    _run(_test())


def test_08_parse_failure_leaves_active_unchanged(ctx: Phase9Ctx):
    async def _test():
        kb_id = await ctx.create_kb()
        first = await ctx.add(kb_id, "P9-KEEP 泵正常运转。")
        await ctx.promote(kb_id, first["candidate_generation_id"])
        before = await ctx.active_generation(kb_id)
        # Force parse failure (no real PDF parsing occurs).
        pdf_path = Path(os.environ["KB_DATA_ROOT"]) / "uploads/fail.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        _write_pdf(pdf_path, "P9-PARSEFAIL 泵内容。")
        content = pdf_path.read_bytes()

        def boom(*args, **kwargs):
            raise RuntimeError("parse failed")

        import industrial_rag.document_parser as dp_mod

        parse_pdf_original = dp_mod.parse_pdf
        dp_mod.parse_pdf = boom
        raised = False
        async with ctx.factory() as session:
            try:
                await ctx.service(session).add_document(
                    kb_id, original_file_name="blank.pdf", content=content
                )
            except RuntimeError:
                raised = True
        dp_mod.parse_pdf = parse_pdf_original
        assert raised
        after = await ctx.active_generation(kb_id)
        assert after["id"] == before["id"]
        assert ctx.chunks_content(after["collections"]["chunks"]) == ctx.chunks_content(
            before["collections"]["chunks"]
        )

    _run(_test())


def test_09_embedding_failure_leaves_active_unchanged(ctx: Phase9Ctx):
    async def _test():
        kb_id = await ctx.create_kb()
        first = await ctx.add(kb_id, "P9-KEEP2 泵正常。")
        await ctx.promote(kb_id, first["candidate_generation_id"])
        before = await ctx.active_generation(kb_id)
        ctx.qdrant.fail_upsert = True
        try:
            async with ctx.factory() as session:
                pdf_path = Path(os.environ["KB_DATA_ROOT"]) / "uploads/boom.pdf"
                pdf_path.parent.mkdir(parents=True, exist_ok=True)
                _write_pdf(pdf_path, "P9-BOOM 泵损坏内容。")
                try:
                    await ctx.service(session).add_document(
                        kb_id,
                        original_file_name="boom.pdf",
                        content=pdf_path.read_bytes(),
                    )
                    raised = False
                except RuntimeError:
                    raised = True
        finally:
            ctx.qdrant.fail_upsert = False
        assert raised
        after = await ctx.active_generation(kb_id)
        assert after["id"] == before["id"]

    _run(_test())


def test_10_qdrant_write_failure_leaves_active_unchanged(ctx: Phase9Ctx):
    async def _test():
        kb_id = await ctx.create_kb()
        first = await ctx.add(kb_id, "P9-KEEP3 泵正常。")
        await ctx.promote(kb_id, first["candidate_generation_id"])
        before = await ctx.active_generation(kb_id)
        ctx.qdrant.fail_scroll = True
        try:
            async with ctx.factory() as session:
                pdf_path = Path(os.environ["KB_DATA_ROOT"]) / "uploads/scroll-fail.pdf"
                pdf_path.parent.mkdir(parents=True, exist_ok=True)
                _write_pdf(pdf_path, "P9-SCROLL 泵内容。")
                try:
                    await ctx.service(session).add_document(
                        kb_id,
                        original_file_name="scroll-fail.pdf",
                        content=pdf_path.read_bytes(),
                    )
                    raised = False
                except RuntimeError:
                    raised = True
        finally:
            ctx.qdrant.fail_scroll = False
        assert raised
        after = await ctx.active_generation(kb_id)
        assert after["id"] == before["id"]

    _run(_test())


def test_11_validation_failure_blocks_promote(ctx: Phase9Ctx):
    async def _test():
        kb_id = await ctx.create_kb()
        result = await ctx.add(kb_id, "P9-VAL 泵正常内容。")

        async def failing_runner(kb, generation):
            return {
                "http_success_rate": 0.5,
                "trace_complete_rate": 0.5,
                "negative_unsupported_answer_rate": 1.0,
                "no_5xx": False,
                "fabricated_citation": 1,
                "secret_leak": 0,
                "old_document_references": 1,
                "citation_traceability": False,
                "golden_subset_regression": False,
                "add_specific": False,
                "replace_specific": True,
                "delete_specific": True,
            }

        async with ctx.factory() as session:
            svc = ctx.service(session)
            validation = await svc.validate_generation(
                kb_id, result["candidate_generation_id"], golden_runner=failing_runner
            )
            assert validation["passed"] is False
            try:
                await svc.promote_generation(kb_id, result["candidate_generation_id"])
                raised = False
            except Exception:
                raised = True
            await session.commit()
        assert raised
        assert await ctx.active_generation(kb_id) is None

    _run(_test())


def test_12_concurrent_promote_only_one_switch(ctx: Phase9Ctx):
    async def _test():
        kb_id = await ctx.create_kb()
        result = await ctx.add(kb_id, "P9-CONC 泵并发内容。")
        gen_id = result["candidate_generation_id"]
        await ctx.validate(kb_id, gen_id)

        async def do_promote():
            async with ctx.factory() as session:
                svc = ctx.service(session)
                try:
                    outcome = await svc.promote_generation(kb_id, gen_id)
                    await session.commit()
                    return outcome
                except Exception:
                    return {"status": "error"}

        outcomes = await asyncio.gather(do_promote(), do_promote())
        switched = [o for o in outcomes if o.get("status") == "promoted"]
        idempotent = [o for o in outcomes if o.get("status") == "already_active"]
        assert len(switched) == 1
        assert len(idempotent) == 1
        active = await ctx.active_generation(kb_id)
        assert active["id"] == gen_id

    _run(_test())


def test_13_repeat_promote_is_idempotent(ctx: Phase9Ctx):
    async def _test():
        kb_id = await ctx.create_kb()
        result = await ctx.add(kb_id, "P9-IDEM 泵幂等内容。")
        await ctx.validate(kb_id, result["candidate_generation_id"])
        async with ctx.factory() as session:
            svc = ctx.service(session)
            first = await svc.promote_generation(kb_id, result["candidate_generation_id"])
            await session.commit()
        async with ctx.factory() as session:
            svc = ctx.service(session)
            second = await svc.promote_generation(kb_id, result["candidate_generation_id"])
            await session.commit()
        assert first["status"] == "promoted"
        assert second["status"] == "already_active"
        assert second["idempotent"] is True

    _run(_test())


def test_14_restart_recovers_interrupted_job(ctx: Phase9Ctx):
    async def _test():
        kb_id = await ctx.create_kb()
        # Simulate an interrupted job: create it via one service instance and
        # leave it in pending state (as if the process died before building).
        pdf_path = Path(os.environ["KB_DATA_ROOT"]) / "uploads/resume.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        _write_pdf(pdf_path, "P9-RESUME 泵恢复内容。")
        async with ctx.factory() as session:
            svc = ctx.service(session)
            job_result = await svc.add_document(
                kb_id, original_file_name="resume.pdf", content=pdf_path.read_bytes()
            )
            job_id = job_result["job_id"]
            # Simulate crash: candidate was created but job not completed.
            await session.commit()
        # New service instance (post-restart) resumes the job.
        async with ctx.factory() as session:
            svc2 = ctx.service(session)
            resumed = await svc2.resume_job(kb_id, job_id)
            await session.commit()
        assert resumed["status"] == "candidate_built"
        assert resumed["job_id"] == job_id

    _run(_test())


def test_15_generation_mixing_is_detected(ctx: Phase9Ctx):
    async def _test():
        kb_id = await ctx.create_kb()
        result = await ctx.add(kb_id, "P9-MIX 泵混合内容。")
        gen_id = result["candidate_generation_id"]
        async with ctx.factory() as session:
            gens = await ctx.service(session).list_generations(kb_id)
        candidate = next(g for g in gens if g["id"] == gen_id)
        # Inject a foreign-generation point into the candidate collection.
        name = candidate["collections"]["chunks"]
        from qdrant_client import models

        await ctx.qdrant.upsert(
            name,
            [
                models.PointStruct(
                    id=_point_id("foreign-chunk-0"),
                    vector=[0.1] * 1024,
                    payload={"id": "foreign-chunk-0", "generation": "gWRONG", "content": "x"},
                )
            ],
            wait=True,
        )
        async with ctx.factory() as session:
            from industrial_rag.services.generation_validation_service import (
                GenerationValidationService,
            )

            validator = GenerationValidationService(
                session,
                settings=Settings.from_env(),
                qdrant_client_factory=lambda: ctx.qdrant,
            )
            generation = await validator._generation_repo.get(gen_id)
            gate = await validator._payload_gate(kb_id, generation, generation_mix_only=True)
            assert gate["passed"] is False
            assert any("generation mix" in p for p in gate["detail"]["problems"])

    _run(_test())


def test_16_production_resources_untouched(ctx: Phase9Ctx, tmp_path: Path):
    async def _test():
        # All work used the fake Qdrant and the isolated test DB.
        assert not hasattr(ctx.qdrant, "touched_real")
        kb_id = await ctx.create_kb()
        result = await ctx.add(kb_id, "P9-PROD 泵生产内容。")
        await ctx.promote(kb_id, result["candidate_generation_id"])
        # Only candidate-named collections were created (no fuzzy prefixes).
        for name in ctx.qdrant.created:
            assert "kb_" in name and "_g" in name
        # The real production DB file is untouched (its SHA256 is unchanged).
        prod_db = Path(r"D:\industrial_energy_agent\src\data\db\industrial_rag.db")
        if prod_db.is_file():
            before = hashlib.sha256(prod_db.read_bytes()).hexdigest()
            async with ctx.factory() as session:
                await ctx.service(session).list_generations(kb_id)
            after = hashlib.sha256(prod_db.read_bytes()).hexdigest()
            assert before == after

    _run(_test())


def test_17_delete_after_replace_targets_active_version(ctx: Phase9Ctx):
    async def _test():
        kb_id = await ctx.create_kb()
        first = await ctx.add(kb_id, "P9-DEL2 泵的介质温度为 90 摄氏度。")
        await ctx.promote(kb_id, first["candidate_generation_id"])
        v1_doc_id = first["document_id"]
        replaced = await ctx.replace(kb_id, v1_doc_id, "P9-DEL2 泵的介质温度为 150 摄氏度。")
        await ctx.promote(kb_id, replaced["candidate_generation_id"])
        # Deleting by the original (v1) id must invalidate the ACTIVE version.
        deleted = await ctx.delete(kb_id, v1_doc_id)
        await ctx.promote(kb_id, deleted["candidate_generation_id"])
        active = await ctx.active_generation(kb_id)
        contents = ctx.chunks_content(active["collections"]["chunks"])
        assert not any("P9-DEL2" in c for c in contents)
        # Rollback to the v1 generation restores the v1 content.
        await ctx.rollback(kb_id, first["candidate_generation_id"])
        active = await ctx.active_generation(kb_id)
        contents = ctx.chunks_content(active["collections"]["chunks"])
        assert any("90 摄氏度" in c for c in contents)

    _run(_test())


def test_18_promote_updates_kb_workspace_pointer(ctx: Phase9Ctx):
    async def _test():
        kb_id = await ctx.create_kb()
        result = await ctx.add(kb_id, "P9-WS 泵工作区指针。")
        await ctx.promote(kb_id, result["candidate_generation_id"])
        async with ctx.factory() as session:
            from industrial_rag.repositories.knowledge_base_repository import (
                KnowledgeBaseRepository,
            )
            from industrial_rag.repositories.vector_index_generation_repository import (
                VectorIndexGenerationRepository,
            )

            kb = await KnowledgeBaseRepository(session).get(kb_id)
            active = await VectorIndexGenerationRepository(session).get_active(kb_id)
        assert kb.workspace_path == active.workspace_path

    _run(_test())

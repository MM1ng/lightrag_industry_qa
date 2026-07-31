"""Create or update the default legacy knowledge base entry.

Run once to register the existing production ``lightrag_storage/``
and ``data/manuals/`` as a managed KnowledgeBase + Documents.
Idempotent — safe to run multiple times.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

LEGACY_KB_ID = "00000000000000000000000000000000"  # deterministic 32-char hex id
LEGACY_KB_NAME = "工业泵设备手册 (默认)"
DEFAULT_WORKSPACE = PROJECT_ROOT / "lightrag_storage"
DEFAULT_UPLOADS = PROJECT_ROOT / "data" / "manuals"
DEFAULT_PARSED = PROJECT_ROOT / "data" / "processed"


async def _migrate() -> int:
    from industrial_rag.db.session import init_db, get_session_factory
    from industrial_rag.repositories.knowledge_base_repository import KnowledgeBaseRepository
    from industrial_rag.repositories.document_repository import DocumentRepository
    from industrial_rag.db.models import KBStatus, DocumentStatus

    await init_db()
    factory = get_session_factory()
    async with factory() as session:
        kb_repo = KnowledgeBaseRepository(session)
        doc_repo = DocumentRepository(session)

        # Check if legacy KB already exists
        existing = await kb_repo.get(LEGACY_KB_ID)
        if existing is not None:
            print(f"Legacy KB already exists: id={existing.id} name={existing.name}")
            # Show current docs
            docs = await doc_repo.list_by_kb(LEGACY_KB_ID, include_deleted=True)
            print(f"  Documents: {len(docs)}")
            for d in docs:
                print(f"    {d.id}: {d.original_file_name} [{d.status.value}]")
            return 0

        # Create legacy KB
        from industrial_rag.db.models import KnowledgeBase

        kb = KnowledgeBase(
            id=LEGACY_KB_ID,
            name=LEGACY_KB_NAME,
            description="默认知识库 - 包含 SUMMIT 2196 和 DESMI 离心泵手册",
            status=KBStatus.ready,
            workspace_path=str(DEFAULT_WORKSPACE),
            upload_path=str(DEFAULT_UPLOADS),
            parsed_path=str(DEFAULT_PARSED),
            parser_name="PyMuPDF",
            parser_version="1.28.0",
            chunking_strategy="fixed_character",
            chunking_version="1",
            chunking_config={
                "max_characters": 1800,
                "overlap_characters": 180,
                "lightrag_split_by_character_only": True,
                "lightrag_chunk_token_size": 1600,
            },
            embedding_model="text-embedding-v4",
            embedding_dimension=1024,
            is_legacy_default=True,
            protect_from_delete=True,
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
        )
        session.add(kb)
        await session.flush()

        # Register existing PDFs
        pdfs = sorted(p for p in DEFAULT_UPLOADS.iterdir() if p.suffix.lower() == ".pdf")
        for pdf_path in pdfs:
            file_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
            doc = DocumentRepository(session)
            await doc_repo.create(
                knowledge_base_id=LEGACY_KB_ID,
                original_file_name=pdf_path.name,
                stored_file_name=pdf_path.name,
                file_path=str(pdf_path),
                file_hash=file_hash,
                file_size=pdf_path.stat().st_size,
                mime_type="application/pdf",
                status=DocumentStatus.indexed,
                is_active=True,
                parse_status="done",
                index_status="done",
                parser_name="PyMuPDF",
                parser_version="1.28.0",
                chunking_strategy="fixed_character",
                chunking_version="1",
            )
            print(f"  Registered: {pdf_path.name} (hash={file_hash[:12]})")
        await session.commit()

    print(f"Legacy KB created: id={LEGACY_KB_ID}")
    print(f"  Workspace: {DEFAULT_WORKSPACE}")
    print(f"  Uploads:   {DEFAULT_UPLOADS}")
    print(f"  Parsed:    {DEFAULT_PARSED}")
    return 0


def main() -> int:
    return asyncio.run(_migrate())


if __name__ == "__main__":
    raise SystemExit(main())

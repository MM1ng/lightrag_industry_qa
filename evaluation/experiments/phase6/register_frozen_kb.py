"""Register the Phase 4 frozen Qdrant index as a read-only KB in the app DB.

Metadata-only registration: it never creates/updates Qdrant collections and
never modifies the frozen index. Idempotent; if the KB already exists it only
verifies the generation identity and refuses to overwrite.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from industrial_rag.db.models import (
    KBStatus,
    KnowledgeBase,
    VectorIndexGeneration,
    VectorIndexGenerationStatus,
)
from industrial_rag.db.session import init_db
from .config import FROZEN_INDEX_MANIFEST, PROJECT_ROOT

KB_ID = "8fce4626859d44abb70a9ae5b0372cea"
GEN_ID = "a2d1c77ce08b414495e9d845cc42f799"
GENERATION = "g5162e7fb4208635103ff4ebb"
KB_DATA_ROOT = (
    PROJECT_ROOT
    / "evaluation"
    / "experiments"
    / "phase4"
    / "parent_expansion"
    / "tmp"
    / "phase4_index"
    / "kb_data"
    / KB_ID
)
NANO_WORKSPACE = KB_DATA_ROOT / "nano" / "workspace"
QDRANT_WORKSPACE = (
    KB_DATA_ROOT
    / "qdrant"
    / "generations"
    / GENERATION
    / "workspace"
)


async def _register() -> dict:
    manifest = json.loads(FROZEN_INDEX_MANIFEST.read_text(encoding="utf-8"))
    from sqlalchemy import select

    from industrial_rag.db.session import get_session_factory

    await init_db()
    async with get_session_factory()() as session:
        existing = await session.get(KnowledgeBase, KB_ID)
        if existing is not None:
            generation = await session.get(VectorIndexGeneration, GEN_ID)
            assert generation is not None and generation.generation == GENERATION
            return {
                "status": "already_registered",
                "kb_id": KB_ID,
                "generation": GENERATION,
                "verified": True,
            }
        generation = VectorIndexGeneration(
            id=GEN_ID,
            knowledge_base_id=KB_ID,
            backend="qdrant",
            generation=GENERATION,
            status=VectorIndexGenerationStatus.active,
            workspace_path=str(QDRANT_WORKSPACE.resolve()),
            collections=manifest["collections"],
            document_manifest_hash="63c81987ab34905c5aa974499542144892467838f7ab2e9b8eef231514e75d0b",
            child_chunks_manifest_hash="0214dd0795f3dda42c7e8705ca07d0d9802d4c4a7bddcd84f54ded23a71daf02",
            embedding_config_hash="96f9ae1c4d538f41505da56aa260b6420da988508a00a0a1416383f1fa8d6f99",
            chunking_config_hash="e5a5864a3d12b1491c1548d5dcb1bdd9753c11c8a60d76b675ad3907f94a93bf",
            activated_at=datetime.now(tz=UTC),
        )
        session.add(generation)
        kb = KnowledgeBase(
            id=KB_ID,
            name="Phase4-Frozen-PyMuPDF-Qdrant",
            description="Read-only registration of the Phase 4 frozen index (metadata only).",
            status=KBStatus.ready,
            workspace_path=str(NANO_WORKSPACE.resolve()),
            upload_path=str((KB_DATA_ROOT / "uploads").resolve()),
            parsed_path=str((KB_DATA_ROOT / "parsed").resolve()),
            parser_name="PyMuPDF",
            embedding_model="text-embedding-v4",
            embedding_dimension=1024,
            vector_backend="qdrant",
            active_vector_generation_id=GEN_ID,
            protect_from_delete=True,
            is_legacy_default=False,
        )
        session.add(kb)
        await session.flush()
        await session.commit()
        return {
            "status": "registered",
            "kb_id": KB_ID,
            "generation": GENERATION,
            "collections": manifest["collections"],
        }


def main() -> int:
    result = asyncio.run(_register())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

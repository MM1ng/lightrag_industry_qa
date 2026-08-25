"""Create an isolated Phase 9B staging copy without modifying source resources."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from qdrant_client import AsyncQdrantClient, models

FORMAL_KB_ID = "8fce4626859d44abb70a9ae5b0372cea"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare_files(source_root: Path, target_root: Path) -> tuple[Path, list[Path]]:
    if target_root.exists():
        raise RuntimeError("target staging root already exists")
    runtime = target_root / "runtime"
    (target_root / "logs").mkdir(parents=True)
    (target_root / "acceptance").mkdir()
    (target_root / "security").mkdir()
    (target_root / "rehearsal").mkdir()
    runtime.mkdir()
    source_db = source_root / "runtime" / "industrial_rag_staging.db"
    target_db = runtime / "industrial_rag_phase9b.db"
    with sqlite3.connect(source_db) as source, sqlite3.connect(target_db) as target:
        source.backup(target)
    source_workspace = (
        source_root
        / "runtime"
        / "kb_workspace"
        / FORMAL_KB_ID
        / "nano"
        / "workspace"
    )
    target_workspace = (
        runtime / "kb_data" / FORMAL_KB_ID / "qdrant" / "generations" / "formal" / "workspace"
    )
    shutil.copytree(
        source_workspace,
        target_workspace,
        ignore=shutil.ignore_patterns("kv_store_llm_response_cache.json"),
    )
    uploads = runtime / "kb_data" / FORMAL_KB_ID / "uploads"
    uploads.mkdir(parents=True)
    manuals = []
    for name in ("2196-ANSI-Manual-Chinese.pdf", "t1739cn.pdf"):
        source = Path(__file__).resolve().parents[1] / "data" / "manuals" / name
        target = uploads / name
        shutil.copy2(source, target)
        manuals.append(target)
    with sqlite3.connect(target_db) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "UPDATE knowledge_bases SET workspace_path=?, upload_path=?, parsed_path=? WHERE id=?",
            (
                str(target_workspace),
                str(uploads),
                str(runtime / "kb_data" / FORMAL_KB_ID / "parsed"),
                FORMAL_KB_ID,
            ),
        )
        connection.execute(
            "UPDATE vector_index_generations SET workspace_path=? WHERE knowledge_base_id=? AND status='active'",
            (str(target_workspace), FORMAL_KB_ID),
        )
        for index, manual in enumerate(manuals, start=1):
            document_id = hashlib.sha256(f"phase9b:{manual.name}".encode()).hexdigest()[:32]
            connection.execute(
                """
                INSERT OR IGNORE INTO documents (
                    id, knowledge_base_id, original_file_name, logical_name, source_type,
                    stored_file_name, file_path, file_hash, file_size, mime_type, version,
                    status, is_active, parse_status, index_status, parser_name,
                    parser_version, chunking_strategy, chunking_version, page_count,
                    parent_chunk_count, child_chunk_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'application/pdf', ?, ?, ?, ?, 'application/pdf', 1,
                    'indexed', 1, 'done', 'done', 'PyMuPDF', '1.28.0',
                    'fixed_character', '1', NULL, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    document_id,
                    FORMAL_KB_ID,
                    manual.name,
                    manual.name,
                    manual.name,
                    str(manual),
                    sha256(manual),
                    manual.stat().st_size,
                ),
            )
        connection.execute(
            "UPDATE knowledge_bases SET document_count=2, active_document_count=2 WHERE id=?",
            (FORMAL_KB_ID,),
        )
        connection.commit()
    return target_db, manuals


async def clone_formal_collections(source_url: str, target_url: str, database: Path) -> dict[str, int]:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT collections FROM vector_index_generations WHERE knowledge_base_id=? AND status='active'",
            (FORMAL_KB_ID,),
        ).fetchone()
    if row is None:
        raise RuntimeError("formal active generation is missing from staging database")
    collection_names = json.loads(row[0])
    source = AsyncQdrantClient(url=source_url)
    target = AsyncQdrantClient(url=target_url)
    counts: dict[str, int] = {}
    try:
        for namespace, collection_name in sorted(collection_names.items()):
            info = await source.get_collection(collection_name)
            if not await target.collection_exists(collection_name):
                await target.create_collection(
                    collection_name=collection_name,
                    vectors_config=info.config.params.vectors,
                    sparse_vectors_config=info.config.params.sparse_vectors,
                )
            offset = None
            copied = 0
            while True:
                records, next_offset = await source.scroll(
                    collection_name=collection_name,
                    limit=256,
                    offset=offset,
                    with_payload=True,
                    with_vectors=True,
                )
                points = [
                    models.PointStruct(
                        id=record.id,
                        vector=record.vector,
                        payload=record.payload or {},
                    )
                    for record in records
                ]
                if points:
                    await target.upsert(collection_name, points, wait=True)
                    copied += len(points)
                if next_offset is None:
                    break
                offset = next_offset
            counts[namespace] = copied
    finally:
        await source.close()
        await target.close()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-staging", type=Path, required=True)
    parser.add_argument("--target-staging", type=Path, required=True)
    parser.add_argument("--source-qdrant", default="http://127.0.0.1:16333")
    parser.add_argument("--target-qdrant", default="http://127.0.0.1:17333")
    args = parser.parse_args()
    database, manuals = prepare_files(args.source_staging.resolve(), args.target_staging.resolve())
    counts = asyncio.run(
        clone_formal_collections(args.source_qdrant, args.target_qdrant, database)
    )
    manifest = {
        "target": "local_staging",
        "database": str(database),
        "database_sha256": sha256(database),
        "formal_kb_id": FORMAL_KB_ID,
        "manuals": [
            {"name": path.name, "sha256": sha256(path), "size": path.stat().st_size}
            for path in manuals
        ],
        "qdrant_counts": counts,
        "source_resources_modified": False,
        "llm_cache_copied": False,
    }
    output = args.target_staging / "runtime" / "preparation_manifest.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"prepared": True, "qdrant_counts": counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

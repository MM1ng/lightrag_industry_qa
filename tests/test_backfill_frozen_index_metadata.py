import json
import sqlite3
from pathlib import Path

import fitz
from scripts.backfill_frozen_index_metadata import run_backfill

KB_ID = "8fce4626859d44abb70a9ae5b0372cea"
GENERATION_ID = "a2d1c77ce08b414495e9d845cc42f799"


def _create_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    source_dir = tmp_path / "manuals"
    source_dir.mkdir()
    for name, page_count in (("alpha.pdf", 2), ("beta.pdf", 3)):
        document = fitz.open()
        for _ in range(page_count):
            document.new_page()
        document.save(source_dir / name)
        document.close()

    workspace = tmp_path / "workspace"
    context_registry = workspace / "context_registry"
    context_registry.mkdir(parents=True)
    chunks = context_registry / "chunks.jsonl"
    rows = [
        {
            "document_id": "doc-alpha",
            "document_name": "alpha.pdf",
            "document_version": "1",
            "parent_chunk_id": "parent-alpha-1",
            "page_start": 1,
            "page_end": 1,
            "parser": "PyMuPDF",
            "parser_version": "1.28.0",
            "chunking_strategy": "pymupdf-v1",
            "chunking_version": "0.1.0",
        },
        {
            "document_id": "doc-alpha",
            "document_name": "alpha.pdf",
            "document_version": "1",
            "parent_chunk_id": "parent-alpha-2",
            "page_start": 2,
            "page_end": 2,
            "parser": "PyMuPDF",
            "parser_version": "1.28.0",
            "chunking_strategy": "pymupdf-v1",
            "chunking_version": "0.1.0",
        },
        {
            "document_id": "doc-beta",
            "document_name": "beta.pdf",
            "document_version": "1",
            "parent_chunk_id": "parent-beta-1",
            "page_start": 1,
            "page_end": 3,
            "parser": "PyMuPDF",
            "parser_version": "1.28.0",
            "chunking_strategy": "pymupdf-v1",
            "chunking_version": "0.1.0",
        },
    ]
    chunks.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (context_registry / "manifest.json").write_text(
        json.dumps(
            {
                "knowledge_base_id": KB_ID,
                "generation_id": GENERATION_ID,
                "source_artifacts": [{"path": str(chunks)}],
                "record_count": len(rows),
            }
        ),
        encoding="utf-8",
    )

    database = tmp_path / "staging.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE knowledge_bases (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            workspace_path TEXT NOT NULL,
            upload_path TEXT NOT NULL,
            parsed_path TEXT NOT NULL,
            parser_name TEXT NOT NULL,
            parser_version TEXT,
            chunking_strategy TEXT NOT NULL,
            chunking_version TEXT NOT NULL,
            document_count INTEGER NOT NULL DEFAULT 0,
            active_document_count INTEGER NOT NULL DEFAULT 0,
            chunk_count INTEGER NOT NULL DEFAULT 0,
            active_vector_generation_id TEXT,
            updated_at TEXT
        );
        CREATE TABLE vector_index_generations (
            id TEXT PRIMARY KEY,
            knowledge_base_id TEXT NOT NULL,
            generation TEXT NOT NULL,
            status TEXT NOT NULL,
            workspace_path TEXT NOT NULL,
            created_at TEXT,
            activated_at TEXT
        );
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            knowledge_base_id TEXT NOT NULL,
            original_file_name TEXT NOT NULL,
            logical_name TEXT,
            source_type TEXT,
            stored_file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            mime_type TEXT NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL,
            is_active INTEGER NOT NULL,
            parse_status TEXT NOT NULL,
            index_status TEXT NOT NULL,
            parser_name TEXT NOT NULL,
            parser_version TEXT,
            chunking_strategy TEXT NOT NULL,
            chunking_version TEXT NOT NULL,
            page_count INTEGER,
            parent_chunk_count INTEGER NOT NULL,
            child_chunk_count INTEGER NOT NULL,
            created_at TEXT,
            updated_at TEXT,
            indexed_at TEXT,
            deleted_at TEXT,
            last_error TEXT
        );
        CREATE TABLE update_jobs (id TEXT PRIMARY KEY, knowledge_base_id TEXT NOT NULL);
        """
    )
    connection.execute(
        """
        INSERT INTO knowledge_bases (
            id, name, status, workspace_path, upload_path, parsed_path,
            parser_name, parser_version, chunking_strategy, chunking_version,
            active_vector_generation_id
        ) VALUES (?, ?, 'ready', ?, ?, ?, 'PyMuPDF', NULL, 'fixed_character', '1', ?)
        """,
        (KB_ID, "Frozen KB", str(workspace), str(tmp_path / "uploads"), str(tmp_path / "parsed"), GENERATION_ID),
    )
    connection.execute(
        """
        INSERT INTO vector_index_generations
        (id, knowledge_base_id, generation, status, workspace_path, created_at, activated_at)
        VALUES (?, ?, 'g-frozen', 'active', ?, '2026-08-02T00:00:00+00:00', '2026-08-02T00:00:00+00:00')
        """,
        (GENERATION_ID, KB_ID, str(workspace)),
    )
    connection.commit()
    connection.close()
    return database, source_dir, workspace


def test_dry_run_does_not_write_metadata_or_copy_files(tmp_path: Path) -> None:
    database, source_dir, workspace = _create_fixture(tmp_path)

    result = run_backfill(database, KB_ID, source_dir, workspace, apply=False)

    assert result.inserted == 2
    assert result.document_count == 2
    assert result.chunk_count == 3
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    assert connection.execute("SELECT document_count FROM knowledge_bases").fetchone()[0] == 0
    connection.close()
    assert not (tmp_path / "uploads").exists()


def test_apply_backfills_documents_counts_and_is_idempotent(tmp_path: Path) -> None:
    database, source_dir, workspace = _create_fixture(tmp_path)

    first = run_backfill(database, KB_ID, source_dir, workspace, apply=True)
    second = run_backfill(database, KB_ID, source_dir, workspace, apply=True)

    assert first.inserted == 2
    assert second.inserted == 0
    assert second.skipped_existing == 2
    connection = sqlite3.connect(database)
    rows = connection.execute(
        "SELECT original_file_name, status, parse_status, index_status, page_count, "
        "parent_chunk_count, child_chunk_count FROM documents ORDER BY original_file_name"
    ).fetchall()
    assert rows == [
        ("alpha.pdf", "indexed", "done", "done", 2, 2, 2),
        ("beta.pdf", "indexed", "done", "done", 3, 1, 1),
    ]
    assert connection.execute(
        "SELECT document_count, active_document_count, chunk_count FROM knowledge_bases"
    ).fetchone() == (2, 2, 3)
    assert connection.execute("SELECT COUNT(*) FROM update_jobs").fetchone()[0] == 0
    connection.close()
    assert len(list((tmp_path / "uploads").glob("*.pdf"))) == 2

"""Backfill management metadata for an already-built frozen LightRAG index.

This tool deliberately does not call LightRAG, Qdrant, or the update-job
service. It reads the active generation's context registry, copies the source
PDFs into the normal KB upload area, and registers indexed Document rows so
the admin console reflects the existing index.

The default mode is a read-only dry run. Pass ``--apply`` to write the
database and copy source PDFs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fitz


class BackfillError(RuntimeError):
    """Raised when the frozen index cannot be safely mapped to source files."""


@dataclass(frozen=True)
class BackfillResult:
    inserted: int
    skipped_existing: int
    copied_files: int
    document_count: int
    active_document_count: int
    chunk_count: int
    dry_run: bool


@dataclass(frozen=True)
class _DocumentSpec:
    name: str
    source_path: Path
    file_hash: str
    file_size: int
    page_count: int
    parent_chunk_count: int
    child_chunk_count: int
    parser_name: str
    parser_version: str | None
    chunking_strategy: str
    chunking_version: str
    source_mtime: str


def run_backfill(
    database: Path,
    knowledge_base_id: str,
    source_dir: Path,
    workspace: Path,
    *,
    apply: bool,
) -> BackfillResult:
    """Plan or apply a frozen-index metadata backfill for one knowledge base."""

    database = database.resolve()
    source_dir = source_dir.resolve()
    workspace = workspace.resolve()
    if not database.is_file():
        raise BackfillError(f"数据库不存在: {database}")
    if not source_dir.is_dir():
        raise BackfillError(f"源文档目录不存在: {source_dir}")

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        kb = connection.execute(
            "SELECT * FROM knowledge_bases WHERE id = ?", (knowledge_base_id,)
        ).fetchone()
        if kb is None:
            raise BackfillError(f"知识库不存在: {knowledge_base_id}")

        generation = _active_generation(connection, kb, knowledge_base_id)
        manifest = _load_manifest(workspace, generation)
        specs = _build_specs(manifest, workspace, source_dir)
        upload_dir = Path(kb["upload_path"] or (source_dir / "uploads"))
        existing_by_hash = {
            row["file_hash"]: row
            for row in connection.execute(
                """
                SELECT * FROM documents
                WHERE knowledge_base_id = ? AND file_hash IS NOT NULL AND is_active = 1
                """,
                (knowledge_base_id,),
            ).fetchall()
        }
        _reject_name_collisions(connection, knowledge_base_id, specs, existing_by_hash)

        inserted = 0
        skipped_existing = 0
        copied_files = 0
        if apply:
            connection.execute("BEGIN")

        try:
            for spec in specs:
                existing = existing_by_hash.get(spec.file_hash)
                stored_name = _stored_file_name(spec.name, spec.file_hash)
                stored_path = upload_dir / stored_name
                if existing is not None:
                    skipped_existing += 1
                    if apply:
                        copied_files += _ensure_copy(spec.source_path, stored_path, spec.file_hash)
                    continue

                inserted += 1
                if not apply:
                    continue

                copied_files += _ensure_copy(spec.source_path, stored_path, spec.file_hash)
                document_id = _stable_document_id(knowledge_base_id, spec.file_hash)
                indexed_at = generation["activated_at"] or generation["created_at"] or _utc_now()
                connection.execute(
                    """
                    INSERT INTO documents (
                        id, knowledge_base_id, original_file_name, logical_name,
                        source_type, stored_file_name, file_path, file_hash, file_size,
                        mime_type, version, status, is_active, parse_status, index_status,
                        parser_name, parser_version, chunking_strategy, chunking_version,
                        page_count, parent_chunk_count, child_chunk_count, created_at,
                        updated_at, indexed_at, deleted_at, last_error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'indexed', 1, 'done', 'done',
                              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                    """,
                    (
                        document_id,
                        knowledge_base_id,
                        spec.name,
                        spec.name,
                        "application/pdf",
                        stored_name,
                        str(stored_path.resolve()),
                        spec.file_hash,
                        spec.file_size,
                        "application/pdf",
                        spec.parser_name,
                        spec.parser_version,
                        spec.chunking_strategy,
                        spec.chunking_version,
                        spec.page_count,
                        spec.parent_chunk_count,
                        spec.child_chunk_count,
                        spec.source_mtime,
                        _utc_now(),
                        indexed_at,
                    ),
                )

            if apply:
                _update_knowledge_base_counts(connection, knowledge_base_id)
                connection.commit()
        except Exception:
            if apply:
                connection.rollback()
            raise

        counts = _counts_after_plan(connection, knowledge_base_id, specs, apply=apply)
        return BackfillResult(
            inserted=inserted,
            skipped_existing=skipped_existing,
            copied_files=copied_files,
            document_count=counts[0],
            active_document_count=counts[1],
            chunk_count=counts[2],
            dry_run=not apply,
        )
    finally:
        connection.close()


def _active_generation(
    connection: sqlite3.Connection, kb: sqlite3.Row, knowledge_base_id: str
) -> sqlite3.Row:
    generation_id = kb["active_vector_generation_id"]
    if generation_id:
        generation = connection.execute(
            "SELECT * FROM vector_index_generations WHERE id = ? AND knowledge_base_id = ?",
            (generation_id, knowledge_base_id),
        ).fetchone()
    else:
        generation = connection.execute(
            """
            SELECT * FROM vector_index_generations
            WHERE knowledge_base_id = ? AND status = 'active'
            ORDER BY created_at DESC LIMIT 1
            """,
            (knowledge_base_id,),
        ).fetchone()
    if generation is None:
        raise BackfillError("知识库没有可用的 active Generation")
    return generation


def _load_manifest(workspace: Path, generation: sqlite3.Row) -> dict[str, Any]:
    expected_workspace = Path(generation["workspace_path"]).resolve()
    if expected_workspace != workspace:
        raise BackfillError(
            f"回填 workspace 与 active Generation 不一致: {workspace} != {expected_workspace}"
        )
    manifest_path = workspace / "context_registry" / "manifest.json"
    if not manifest_path.is_file():
        raise BackfillError(f"索引 manifest 不存在: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackfillError(f"无法读取索引 manifest: {manifest_path}") from exc
    manifest_generation = manifest.get("generation_id")
    if manifest_generation and manifest_generation != generation["id"]:
        raise BackfillError("manifest 不属于当前 active Generation")
    if not manifest.get("source_artifacts"):
        raise BackfillError("manifest 没有 source_artifacts")
    return manifest


def _build_specs(
    manifest: dict[str, Any], workspace: Path, source_dir: Path
) -> list[_DocumentSpec]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for artifact in manifest["source_artifacts"]:
        artifact_path = Path(artifact["path"])
        if not artifact_path.is_absolute():
            artifact_path = workspace / artifact_path
        if not artifact_path.is_file():
            raise BackfillError(f"source artifact 不存在: {artifact_path}")
        try:
            lines = artifact_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise BackfillError(f"无法读取 source artifact: {artifact_path}") from exc
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BackfillError(f"source artifact JSON 错误: {artifact_path}:{line_number}") from exc
            name = row.get("document_name")
            if not isinstance(name, str) or not name.strip() or Path(name).name != name:
                raise BackfillError(f"source artifact 文档名非法: {name!r}")
            grouped[name].append(row)

    specs: list[_DocumentSpec] = []
    for name, rows in sorted(grouped.items()):
        source_path = source_dir / name
        if not source_path.is_file():
            raise BackfillError(f"源 PDF 不存在: {source_path}")
        source_hash = _sha256(source_path)
        try:
            with fitz.open(source_path) as pdf:
                page_count = pdf.page_count
        except Exception as exc:
            raise BackfillError(f"无法读取 PDF 页数: {source_path}") from exc
        first = rows[0]
        specs.append(
            _DocumentSpec(
                name=name,
                source_path=source_path,
                file_hash=source_hash,
                file_size=source_path.stat().st_size,
                page_count=page_count,
                parent_chunk_count=len({row.get("parent_chunk_id") for row in rows if row.get("parent_chunk_id")}),
                child_chunk_count=len(rows),
                parser_name=str(first.get("parser") or "PyMuPDF"),
                parser_version=first.get("parser_version"),
                chunking_strategy=str(first.get("chunking_strategy") or "fixed_character"),
                chunking_version=str(first.get("chunking_version") or "1"),
                source_mtime=datetime.fromtimestamp(source_path.stat().st_mtime, tz=UTC).isoformat(),
            )
        )
    if not specs:
        raise BackfillError("索引 source_artifacts 没有可回填的文档")
    return specs


def _reject_name_collisions(
    connection: sqlite3.Connection,
    knowledge_base_id: str,
    specs: list[_DocumentSpec],
    existing_by_hash: dict[str, sqlite3.Row],
) -> None:
    for spec in specs:
        existing = connection.execute(
            """
            SELECT file_hash FROM documents
            WHERE knowledge_base_id = ? AND original_file_name = ? AND is_active = 1
            LIMIT 1
            """,
            (knowledge_base_id, spec.name),
        ).fetchone()
        if existing is not None and existing["file_hash"] != spec.file_hash and spec.file_hash not in existing_by_hash:
            raise BackfillError(f"同名文档已存在但内容不同，拒绝覆盖: {spec.name}")


def _update_knowledge_base_counts(connection: sqlite3.Connection, knowledge_base_id: str) -> None:
    counts = connection.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE status != 'deleted') AS document_count,
            COUNT(*) FILTER (WHERE is_active = 1 AND status != 'deleted') AS active_document_count,
            COALESCE(SUM(child_chunk_count) FILTER (WHERE is_active = 1 AND status != 'deleted'), 0) AS chunk_count
        FROM documents WHERE knowledge_base_id = ?
        """,
        (knowledge_base_id,),
    ).fetchone()
    connection.execute(
        """
        UPDATE knowledge_bases
        SET document_count = ?, active_document_count = ?, chunk_count = ?, updated_at = ?
        WHERE id = ?
        """,
        (counts["document_count"], counts["active_document_count"], counts["chunk_count"], _utc_now(), knowledge_base_id),
    )


def _counts_after_plan(
    connection: sqlite3.Connection,
    knowledge_base_id: str,
    specs: list[_DocumentSpec],
    *,
    apply: bool,
) -> tuple[int, int, int]:
    if apply:
        row = connection.execute(
            "SELECT document_count, active_document_count, chunk_count FROM knowledge_bases WHERE id = ?",
            (knowledge_base_id,),
        ).fetchone()
        return int(row[0]), int(row[1]), int(row[2])
    existing = connection.execute(
        """
        SELECT COUNT(*) FILTER (WHERE status != 'deleted'),
               COUNT(*) FILTER (WHERE is_active = 1 AND status != 'deleted'),
               COALESCE(SUM(child_chunk_count) FILTER (WHERE is_active = 1 AND status != 'deleted'), 0)
        FROM documents WHERE knowledge_base_id = ?
        """,
        (knowledge_base_id,),
    ).fetchone()
    pending_hashes = {
        spec.file_hash
        for spec in specs
        if connection.execute(
            "SELECT 1 FROM documents WHERE knowledge_base_id = ? AND file_hash = ? AND is_active = 1 LIMIT 1",
            (knowledge_base_id, spec.file_hash),
        ).fetchone()
        is None
    }
    return int(existing[0]) + len(pending_hashes), int(existing[1]) + len(pending_hashes), int(existing[2]) + sum(
        spec.child_chunk_count for spec in specs if spec.file_hash in pending_hashes
    )


def _ensure_copy(source: Path, target: Path, expected_hash: str) -> int:
    if target.is_file():
        if _sha256(target) != expected_hash:
            raise BackfillError(f"目标上传文件内容不匹配，拒绝覆盖: {target}")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return 1


def _stored_file_name(name: str, file_hash: str) -> str:
    source = Path(name)
    stem = re.sub(r"[^a-zA-Z0-9._-]", "_", source.stem)
    return f"{stem}_{file_hash[:8]}{source.suffix.lower()}"


def _stable_document_id(knowledge_base_id: str, file_hash: str) -> str:
    return hashlib.sha256(f"frozen-index:{knowledge_base_id}:{file_hash}".encode()).hexdigest()[:32]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$", line)
        if match and match.group(1) not in os.environ:
            os.environ[match.group(1)] = match.group(2).strip().strip("'\"")


def _database_from_url(url: str) -> Path:
    if url.startswith("sqlite+aiosqlite:///"):
        return Path(url.removeprefix("sqlite+aiosqlite:///"))
    if url.startswith("sqlite:///"):
        return Path(url.removeprefix("sqlite:///"))
    raise BackfillError("当前回填工具只支持 SQLite DATABASE_URL")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, help="SQLite 数据库路径")
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--source-dir", type=Path, default=Path("data/manuals"))
    parser.add_argument("--workspace", type=Path, help="active Generation workspace")
    parser.add_argument("--env-file", type=Path, default=Path(".env.local_staging"))
    parser.add_argument("--apply", action="store_true", help="写入数据库并复制 PDF；默认 dry-run")
    args = parser.parse_args()

    _load_env_file(args.env_file)
    database = args.database or _database_from_url(os.environ.get("DATABASE_URL", ""))
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT vig.workspace_path
            FROM knowledge_bases kb
            JOIN vector_index_generations vig ON vig.id = kb.active_vector_generation_id
            WHERE kb.id = ?
            """,
            (args.knowledge_base_id,),
        ).fetchone()
    finally:
        connection.close()
    workspace = args.workspace or (Path(row[0]) if row else None)
    if workspace is None:
        raise BackfillError("无法从知识库找到 active Generation workspace，请传入 --workspace")

    result = run_backfill(database, args.knowledge_base_id, args.source_dir, workspace, apply=args.apply)
    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(
        f"{mode}: inserted={result.inserted} skipped_existing={result.skipped_existing} "
        f"copied_files={result.copied_files} documents={result.document_count} "
        f"active_documents={result.active_document_count} chunks={result.chunk_count}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BackfillError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc

"""Materialize a generation-local context registry from real child artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-source-generation", action="store_true")
    args = parser.parse_args()

    connection = sqlite3.connect(args.database)
    row = connection.execute(
        "select knowledge_base_id, generation, workspace_path from vector_index_generations "
        "where id=? and knowledge_base_id=?",
        (args.generation_id, args.knowledge_base_id),
    ).fetchone()
    if row is None:
        raise SystemExit("generation_not_found")
    if args.verify_source_generation and Path(row[2]).resolve() != args.workspace.resolve():
        raise SystemExit("generation_workspace_mismatch")

    parsed_root = args.workspace.parents[3] / "parsed" / "documents"
    source_files = sorted(parsed_root.glob("*/current/child_chunks.jsonl"))
    if not source_files:
        raise SystemExit("child_chunks_not_found")

    chunks: list[dict[str, object]] = []
    relationships: list[dict[str, object]] = []
    for source_file in source_files:
        document_chunks = [
            json.loads(line)
            for line in source_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for order, item in enumerate(document_chunks):
            if item.get("document_id") is None or item.get("chunk_id") is None:
                raise SystemExit("chunk_identity_missing")
            record = {
                "knowledge_base_id": args.knowledge_base_id,
                "generation_id": args.generation_id,
                "document_id": item["document_id"],
                "document_name": item.get("document_name", ""),
                "chunk_id": item["chunk_id"],
                "chunk_order": order,
                "page_start": item.get("page_start"),
                "page_end": item.get("page_end"),
                "section_path": item.get("section_path", []),
                "parent_chunk_id": None,
                "previous_chunk_id": document_chunks[order - 1]["chunk_id"] if order else None,
                "next_chunk_id": document_chunks[order + 1]["chunk_id"] if order + 1 < len(document_chunks) else None,
                "table_id": None,
                "table_header_chunk_id": None,
                "content_sha256": hashlib.sha256(str(item.get("content", "")).encode("utf-8")).hexdigest(),
                "content": str(item.get("content", ""))[:600],
            }
            chunks.append(record)
            for relation, target in (("previous", record["previous_chunk_id"]), ("next", record["next_chunk_id"])):
                if target:
                    relationships.append({"source_chunk_id": record["chunk_id"], "target_chunk_id": target, "relation": relation, "document_id": record["document_id"], "generation_id": args.generation_id})

    args.output.mkdir(parents=True, exist_ok=True)
    chunks_path = args.output / "chunks.jsonl"
    relationships_path = args.output / "relationships.jsonl"
    chunks_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in chunks) + "\n", encoding="utf-8")
    relationships_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in relationships) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "phase10b3b-context-registry-v1",
        "knowledge_base_id": args.knowledge_base_id,
        "generation_id": args.generation_id,
        "parser_name": "PyMuPDF",
        "parser_version": "1.28.0",
        "chunking_strategy": "pymupdf-v1",
        "chunking_version": "0.1.0",
        "source_artifacts": [{"path": str(path), "sha256": _sha256(path)} for path in source_files],
        "chunks_sha256": _sha256(chunks_path),
        "relationships_sha256": _sha256(relationships_path),
        "record_count": len(chunks),
        "relationship_count": len(relationships),
        "parent_supported": False,
        "table_supported": False,
        "created_at": datetime.now(UTC).isoformat(),
        "creation_commit": "fcbf6fcfbd804666899425e2fa98770d57cab533",
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

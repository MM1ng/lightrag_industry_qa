"""Build an isolated parsed Candidate Generation from the controlled manuals."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from industrial_rag.document_parser import parse_pdf
from industrial_rag.structured_chunker import (
    ChunkerConfig,
    build_parent_child_chunks,
    pymupdf_chunks_to_blocks,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--manual-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--creation-commit", required=True)
    args = parser.parse_args()

    parsed_root = args.output / "parsed"
    registry_root = args.output / "context_registry"
    artifact_root = args.output / "artifacts"
    for path in (parsed_root, registry_root, artifact_root):
        path.mkdir(parents=True, exist_ok=True)
    cfg = ChunkerConfig(strategy="pymupdf-v1", version="0.1.0")
    all_parents: list[dict[str, object]] = []
    all_children: list[dict[str, object]] = []
    source_artifacts: list[dict[str, str]] = []
    for pdf in sorted(args.manual_dir.glob("*.pdf")):
        chunks = parse_pdf(pdf)
        blocks = pymupdf_chunks_to_blocks(chunks, pdf.name)
        parents, children = build_parent_child_chunks(blocks, pdf.name, config=cfg)
        document_id = children[0].document_id if children else ""
        document_dir = parsed_root / document_id
        document_dir.mkdir(parents=True, exist_ok=True)
        parent_rows = [
            {
                "parent_chunk_id": item.parent_chunk_id,
                "document_id": item.document_id,
                "document_name": item.document_name,
                "page_start": item.page_start,
                "page_end": item.page_end,
                "section_path": list(item.section_path),
                "content": item.content,
                "content_sha256": hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
                "child_chunk_ids": list(item.child_chunk_ids),
                "token_count": item.token_count,
            }
            for item in parents
        ]
        child_rows = []
        for order, item in enumerate(children):
            child_rows.append(
                {
                    "knowledge_base_id": args.knowledge_base_id,
                    "generation_id": args.generation_id,
                    "document_id": item.document_id,
                    "document_name": item.document_name,
                    "chunk_id": item.chunk_id,
                    "chunk_order": order,
                    "page_start": item.page_start,
                    "page_end": item.page_end,
                    "section_path": list(item.section_path),
                    "parent_chunk_id": item.parent_chunk_id,
                    "previous_chunk_id": None,
                    "next_chunk_id": None,
                    "table_id": None,
                    "table_header_chunk_id": None,
                    "content": item.content,
                    "content_sha256": hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
                    "embedding_content": item.embedding_content,
                }
            )
        for index, row in enumerate(child_rows):
            if index:
                row["previous_chunk_id"] = child_rows[index - 1]["chunk_id"]
            if index + 1 < len(child_rows):
                row["next_chunk_id"] = child_rows[index + 1]["chunk_id"]
        _write_jsonl(document_dir / "parent_chunks.jsonl", parent_rows)
        _write_jsonl(document_dir / "child_chunks.jsonl", child_rows)
        all_parents.extend(parent_rows)
        all_children.extend(child_rows)
        source_artifacts.extend(
            [
                {"path": str(document_dir / "parent_chunks.jsonl"), "sha256": _sha256(document_dir / "parent_chunks.jsonl")},
                {"path": str(document_dir / "child_chunks.jsonl"), "sha256": _sha256(document_dir / "child_chunks.jsonl")},
            ]
        )

    _write_jsonl(registry_root / "parents.jsonl", all_parents)
    _write_jsonl(registry_root / "chunks.jsonl", all_children)
    relationships = []
    for row in all_children:
        for relation, target in (("previous", row["previous_chunk_id"]), ("next", row["next_chunk_id"]), ("parent", row["parent_chunk_id"])):
            if target:
                relationships.append({"source_chunk_id": row["chunk_id"], "target_chunk_id": target, "relation": relation, "document_id": row["document_id"], "generation_id": args.generation_id})
    _write_jsonl(registry_root / "relationships.jsonl", relationships)
    _write_jsonl(registry_root / "tables.jsonl", [])
    manifest = {
        "schema_version": "phase10b3c-context-registry-v1",
        "knowledge_base_id": args.knowledge_base_id,
        "generation_id": args.generation_id,
        "parser_name": "PyMuPDF",
        "parser_version": "1.28.0",
        "chunking_strategy": cfg.strategy,
        "chunking_version": cfg.version,
        "source_artifacts": source_artifacts,
        "record_count": len(all_children),
        "parent_count": len(all_parents),
        "relationship_count": len(relationships),
        "table_supported": False,
        "created_at": datetime.now(UTC).isoformat(),
        "creation_commit": args.creation_commit,
    }
    (registry_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (artifact_root / "parser_build_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

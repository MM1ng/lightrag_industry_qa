"""P0: PyMuPDF parse -> ParsedBlock -> StructuredChunker -> Parent/Child."""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from industrial_rag.document_parser import parse_pdf
from industrial_rag.parser_models import ChildChunk, ParentChunk
from industrial_rag.structured_chunker import (
    build_parent_child_chunks,
    pymupdf_chunks_to_blocks,
)

from .common import plain, write_json, write_jsonl
from .config import CHUNKER_CONFIG, PDF_FACTS, PDF_NAMES, group_dir
from .quality import chunk_stats, page_stats, pdf_facts, structure_stats, text_stats


def _git_head() -> str:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=Path.cwd()
            )
            .stdout.strip()
        )
    except Exception:
        return "unknown"


def _artifact_paths(pdf_name: str) -> dict[str, Path]:
    out = group_dir("0", pdf_name)
    return {
        "dir": out,
        "blocks": out / "blocks.jsonl",
        "parents": out / "parent_chunks.jsonl",
        "children": out / "child_chunks.jsonl",
        "manifest": out / "manifest.json",
        "quality": out / "quality_stats.json",
    }


def parse_one_pdf(pdf_name: str) -> dict[str, Any]:
    facts = PDF_FACTS[pdf_name]
    pdf_path = Path(str(facts["path"]))
    paths = _artifact_paths(pdf_name)
    paths["dir"].mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    source_chunks = parse_pdf(pdf_path)
    blocks = pymupdf_chunks_to_blocks(source_chunks, pdf_name)
    parents, children = build_parent_child_chunks(blocks, pdf_name, config=CHUNKER_CONFIG)
    parse_seconds = round(time.monotonic() - started, 3)

    write_jsonl(
        paths["blocks"],
        [plain(b.to_dict()) for b in blocks],
    )
    write_jsonl(
        paths["parents"],
        [plain(asdict(p)) for p in parents],
    )
    write_jsonl(
        paths["children"],
        [plain(c.to_dict()) for c in children],
    )

    manifest = {
        "parser_requested": "pymupdf",
        "parser_used": "pymupdf",
        "fallback_used": False,
        "fallback_reason": None,
        "pdf_name": pdf_name,
        "pdf_size": facts["size"],
        "pdf_sha256": facts["sha256"],
        "pdf_pages": facts["pages"],
        "pdf_encrypted": facts["encrypted"],
        "parse_seconds": parse_seconds,
        "source_chunk_count": len(source_chunks),
        "block_count": len(blocks),
        "parent_count": len(parents),
        "child_count": len(children),
        "chunker_strategy": CHUNKER_CONFIG.strategy,
        "chunker_version": CHUNKER_CONFIG.version,
        "chunker_config": {
            "parent_target_tokens": CHUNKER_CONFIG.parent_target_tokens,
            "parent_max_tokens": CHUNKER_CONFIG.parent_max_tokens,
            "child_target_tokens": CHUNKER_CONFIG.child_target_tokens,
            "child_min_tokens": CHUNKER_CONFIG.child_min_tokens,
            "child_max_tokens": CHUNKER_CONFIG.child_max_tokens,
            "child_overlap_tokens": CHUNKER_CONFIG.child_overlap_tokens,
            "merge_small_children": CHUNKER_CONFIG.merge_small_children,
        },
        "git_commit": _git_head(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    write_json(paths["manifest"], manifest)

    stats = {
        "page_stats": page_stats(pdf_path, blocks),
        "text_stats": text_stats(blocks),
        "structure_stats": structure_stats(blocks),
        "chunk_stats": chunk_stats(parents, children),
    }
    write_json(paths["quality"], stats)
    print(
        f"[P0:{pdf_name}] blocks={len(blocks)} parents={len(parents)} "
        f"children={len(children)} {parse_seconds}s"
    )
    return manifest


def main() -> int:
    for pdf_name in PDF_NAMES:
        parse_one_pdf(pdf_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())

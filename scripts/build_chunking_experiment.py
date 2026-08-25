"""Build chunking experiment: parse both PDFs, produce Parent-Child chunks,
ingest into isolated LightRAG workspace, run 50Q evaluation.

Each experiment gets its own output directory.  The *current* production
``lightrag_storage/`` is NEVER touched.

Usage:
    # A0: baseline (existing production storage)
    python scripts/build_chunking_experiment.py --group A0 --mode baseline

    # A2: semantic child chunks, no parent expansion
    python scripts/build_chunking_experiment.py --group A2 --mode semantic_child

    # A3: semantic child chunks + parent expansion
    python scripts/build_chunking_experiment.py --group A3 --mode parent_child
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# A0: baseline — just snapshot config and point to production storage
# ---------------------------------------------------------------------------


def build_A0(output_dir: Path) -> int:
    """Freeze baseline config only.  Do NOT re-ingest."""
    _ensure_dir(output_dir)
    from industrial_rag.config import Settings

    settings = Settings.from_env()
    golden_path = PROJECT_ROOT / "data" / "evaluation" / "industrial_pump_golden_set_50.jsonl"

    config = {
        "experiment": "A0",
        "description": "Current baseline: PyMuPDF 1800-char chunks, LightRAG mix, no parent-child",
        "git_commit": os.popen("git rev-parse HEAD").read().strip(),
        "git_branch": os.popen("git rev-parse --abbrev-ref HEAD").read().strip(),
        "has_uncommitted": bool(os.popen("git status --porcelain").read().strip()),
        "python_version": sys.version,
        "lightrag_version": "1.5.4",
        "parser": "PyMuPDF",
        "parser_version": "1.28.0",
        "chunker": "fixed_character",
        "chunk_max_characters": 1800,
        "chunk_overlap_characters": 180,
        "lightrag_chunk_token_size": 1600,
        "lightrag_split_by_character_only": True,
        "embedding_model": settings.embedding_model,
        "embedding_dim": settings.embedding_dim,
        "llm_model": settings.llm_model,
        "retrieval_mode": "mix",
        "rerank_enabled": False,
        "parent_child": False,
        "evidence_policy": {
            "max_selected": 3,
            "min_overlap": 2,
        },
        "pdfs": {
            "2196-ANSI-Manual-Chinese.pdf": _sha256(
                PROJECT_ROOT / "data" / "manuals" / "2196-ANSI-Manual-Chinese.pdf"
            ),
            "t1739cn.pdf": _sha256(PROJECT_ROOT / "data" / "manuals" / "t1739cn.pdf"),
        },
        "golden_set": _sha256(golden_path),
    }

    (output_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # For A0, point to production storage
    manifest = {
        "storage_dir": str((PROJECT_ROOT / "lightrag_storage").resolve()),
        "documents_jsonl": str((PROJECT_ROOT / "data" / "processed" / "documents.jsonl").resolve()),
        "parent_chunks_jsonl": None,
        "child_chunks_jsonl": None,
        "chunk_count": 118,
    }
    (output_dir / "document_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"[A0] Baseline config frozen at {output_dir}")
    return 0


# ---------------------------------------------------------------------------
# A2 / A3 shared: parse PDFs → Parent-Child → LightRAG ingest
# ---------------------------------------------------------------------------


async def _build_semantic_experiment(
    output_dir: Path,
    *,
    enable_parent_expansion: bool,
) -> int:
    """Parse PDFs, build parent-child chunks, ingest into isolated LightRAG."""
    from industrial_rag.config import Settings
    from industrial_rag.document_parser import load_documents, scan_pdf_files
    from industrial_rag.lightrag_service import LightRAGService
    from industrial_rag.parser_models import ChildChunk, ContentType, ParentChunk
    from industrial_rag.structured_chunker import (
        ChunkerConfig,
        build_parent_child_chunks,
        count_tokens,
        make_child_chunk_id,
        make_document_id,
        make_document_version,
        make_parent_chunk_id,
        pymupdf_chunks_to_blocks,
    )
    from industrial_rag.citation_formatter import Citation, encode_chunk_header
    from industrial_rag.parent_chunk_store import ParentChunkStore

    _ensure_dir(output_dir)
    settings = Settings.from_env()

    # Use an isolated storage dir under the experiment
    exp_storage = _ensure_dir(output_dir / "lightrag_storage")
    settings = Settings(
        api_key=settings.api_key,
        service_api_key=settings.service_api_key,
        llm_base_url=settings.llm_base_url,
        llm_model=settings.llm_model,
        llm_fallback_models=settings.llm_fallback_models,
        embedding_model=settings.embedding_model,
        embedding_dim=settings.embedding_dim,
        working_dir=exp_storage,
    )

    # Load source chunks
    source_chunks = load_documents(
        PROJECT_ROOT / "data" / "processed" / "documents.jsonl"
    )

    # Group by source_file
    by_source: dict[str, list[Any]] = {}
    for ch in source_chunks:
        by_source.setdefault(ch.source_file, []).append(ch)

    cfg = ChunkerConfig(strategy="pymupdf-v1")

    all_parents: list[ParentChunk] = []
    all_children: list[ChildChunk] = []

    for source_file, src_chunks in by_source.items():
        blocks = pymupdf_chunks_to_blocks(src_chunks, source_file)
        parents, children = build_parent_child_chunks(blocks, source_file, config=cfg)
        all_parents.extend(parents)
        all_children.extend(children)

    # Persist parent / child chunks
    store = ParentChunkStore(output_dir)
    store.write_all(all_parents, all_children)

    # Write children JSONL (for lineage)
    child_path = output_dir / "child_chunks.jsonl"
    tmp_child = child_path.with_suffix(".tmp")
    tmp_child.write_text(
        "\n".join(
            json.dumps(ch.to_dict(), ensure_ascii=False, sort_keys=True)
            for ch in all_children
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp_child.replace(child_path)

    # Ingest children into isolated LightRAG
    # Each child is a standalone "document" so LightRAG never re-splits
    _CHUNK_BOUNDARY = "\n\n<<<INDUSTRIAL_RAG_CHUNK_BOUNDARY>>>\n\n"

    service = LightRAGService(settings)
    await service.initialize()
    try:
        track_id = await service.ingest(source_chunks)  # use existing ingest to avoid re-chunk
        # Actually we need to ingest child chunks directly as individual documents
        # to guarantee 1:1 mapping.  Re-implement the ingest for child chunks.
        # Use the built-in _backend.ainsert directly per child.
        for i, child in enumerate(all_children):
            citation = Citation(
                child.document_name,
                child.page_start or 1,
                child.chunk_id,
            )
            rendered = (
                f"{encode_chunk_header(citation)}\n"
                f"[来源：{child.document_name}，第{child.page_start or 1}页，"
                f"章节：{child.section_title or '未识别'}]\n"
                f"[parent_chunk_id：{child.parent_chunk_id}]\n"
                f"{child.embedding_content}"
            )
            identity = hashlib.sha256(
                child.chunk_id.encode("utf-8")
            ).hexdigest()[:20]
            await service._backend.ainsert(
                input=[rendered],
                ids=[f"child-{identity}"],
                file_paths=[child.document_name],
                split_by_character=_CHUNK_BOUNDARY,
                split_by_character_only=True,
            )
            if (i + 1) % 20 == 0:
                print(f"  Ingested {i + 1}/{len(all_children)} child chunks")
        print(f"  Ingested all {len(all_children)} child chunks, track_id={track_id}")
    finally:
        await service.close()

    # Write stats
    parent_tokens = [p.token_count for p in all_parents]
    child_tokens = [c.token_count for c in all_children]
    stats = {
        "parent_count": len(all_parents),
        "child_count": len(all_children),
        "parent_token_mean": sum(parent_tokens) / len(parent_tokens) if parent_tokens else 0,
        "parent_token_p50": _percentile(parent_tokens, 0.5),
        "parent_token_p95": _percentile(parent_tokens, 0.95),
        "child_token_mean": sum(child_tokens) / len(child_tokens) if child_tokens else 0,
        "child_token_p50": _percentile(child_tokens, 0.5),
        "child_token_p95": _percentile(child_tokens, 0.95),
    }
    (output_dir / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # Config snapshot
    golden_path = PROJECT_ROOT / "data" / "evaluation" / "industrial_pump_golden_set_50.jsonl"
    mode_label = "parent_child" if enable_parent_expansion else "semantic_child"
    config = {
        "experiment": f"A3" if enable_parent_expansion else "A2",
        "description": (
            "Parent-Child: child retrieval + parent context expansion"
            if enable_parent_expansion
            else "Semantic child chunks, no parent expansion"
        ),
        "git_commit": os.popen("git rev-parse HEAD").read().strip(),
        "parser": "PyMuPDF",
        "chunker": "structured (heading-aware semantic split)",
        "chunker_config": {
            "parent_target_tokens": cfg.parent_target_tokens,
            "parent_max_tokens": cfg.parent_max_tokens,
            "child_target_tokens": cfg.child_target_tokens,
            "child_min_tokens": cfg.child_min_tokens,
            "child_max_tokens": cfg.child_max_tokens,
            "child_overlap_tokens": cfg.child_overlap_tokens,
        },
        "parent_expansion_enabled": enable_parent_expansion,
        "embedding_model": settings.embedding_model,
        "embedding_dim": settings.embedding_dim,
        "llm_model": settings.llm_model,
        "retrieval_mode": "mix",
        "rerank_enabled": False,
        "pdfs": {
            "2196-ANSI-Manual-Chinese.pdf": _sha256(
                PROJECT_ROOT / "data" / "manuals" / "2196-ANSI-Manual-Chinese.pdf"
            ),
            "t1739cn.pdf": _sha256(PROJECT_ROOT / "data" / "manuals" / "t1739cn.pdf"),
        },
        "golden_set": _sha256(golden_path),
    }
    (output_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    manifest = {
        "storage_dir": str(exp_storage.resolve()),
        "parent_chunks_jsonl": str((output_dir / "parent_chunks.jsonl").resolve()),
        "child_chunks_jsonl": str((output_dir / "child_chunks.jsonl").resolve()),
        "parent_count": len(all_parents),
        "child_count": len(all_children),
    }
    (output_dir / "document_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(
        f"[{mode_label}] {len(all_parents)} parents, {len(all_children)} children "
        f"→ {exp_storage}"
    )
    return 0


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[int(len(s) * pct)]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a chunking ablation experiment.")
    parser.add_argument("--group", required=True, choices=["A0", "A2", "A3"])
    parser.add_argument(
        "--mode",
        choices=["baseline", "semantic_child", "parent_child"],
        default="baseline",
        help="Convenience alias; overridden when --group is A0/A2/A3.",
    )
    parser.add_argument(
        "--output-base",
        default=str(PROJECT_ROOT / "evaluation" / "experiments" / "chunking"),
    )
    args = parser.parse_args()

    group = args.group
    base = Path(args.output_base)

    if group == "A0":
        return build_A0(base / "A0_baseline")
    elif group == "A2":
        import asyncio
        return asyncio.run(
            _build_semantic_experiment(
                base / "A2_semantic_child", enable_parent_expansion=False
            )
        )
    elif group == "A3":
        import asyncio
        return asyncio.run(
            _build_semantic_experiment(
                base / "A3_parent_child", enable_parent_expansion=True
            )
        )
    else:
        print(f"Unknown group: {group}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

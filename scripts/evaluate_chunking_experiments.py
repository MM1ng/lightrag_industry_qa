"""Local (offline) A2/A3 structural evaluation — no Bailian API needed.

Runs the structured chunker on the two PDFs, measures chunk statistics,
and verifies that every child chunk has a parent and every chunk carries
correct document/ page provenance.

This is NOT the full 50-Q retrieval evaluation (which requires the
Bailian LLM + embedding API). That evaluation must be run separately
when the API key is configured.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def main() -> int:
    from industrial_rag.document_parser import load_documents
    from industrial_rag.structured_chunker import (
        ChunkerConfig,
        build_parent_child_chunks,
        pymupdf_chunks_to_blocks,
    )

    chunks = load_documents(PROJECT_ROOT / "data" / "processed" / "documents.jsonl")
    print(f"Loaded {len(chunks)} source chunks")

    by_source: dict[str, list] = {}
    for ch in chunks:
        by_source.setdefault(ch.source_file, []).append(ch)

    cfg = ChunkerConfig(strategy="pymupdf-v1")
    total_parents = 0
    total_children = 0

    for source_file, src_chunks in by_source.items():
        blocks = pymupdf_chunks_to_blocks(src_chunks, source_file)
        parents, children = build_parent_child_chunks(blocks, source_file, config=cfg)
        total_parents += len(parents)
        total_children += len(children)

        print(f"\n--- {source_file} ---")
        print(f"  Source chunks: {len(src_chunks)}")
        print(f"  Parsed blocks: {len(blocks)}")
        print(f"  Parent chunks: {len(parents)}")
        print(f"  Child chunks:  {len(children)}")

        child_tokens = [c.token_count for c in children]
        parent_tokens = [p.token_count for p in parents]
        if child_tokens:
            print(f"  Child tokens:  min={min(child_tokens)} mean={sum(child_tokens)/len(child_tokens):.0f} max={max(child_tokens)}")
        if parent_tokens:
            print(f"  Parent tokens: min={min(parent_tokens)} mean={sum(parent_tokens)/len(parent_tokens):.0f} max={max(parent_tokens)}")

        # Verify invariants
        orphan_children = 0
        parent_id_set = {p.parent_chunk_id for p in parents}
        for child in children:
            if child.parent_chunk_id not in parent_id_set:
                orphan_children += 1
                print(f"  ORPHAN: {child.chunk_id} → parent {child.parent_chunk_id} not found")
        empty_parents = 0
        child_ids_by_parent: dict[str, int] = {}
        for child in children:
            child_ids_by_parent[child.parent_chunk_id] = child_ids_by_parent.get(child.parent_chunk_id, 0) + 1
        for p in parents:
            if child_ids_by_parent.get(p.parent_chunk_id, 0) == 0:
                empty_parents += 1
                print(f"  EMPTY PARENT: {p.parent_chunk_id}")
        print(f"  Orphan children: {orphan_children}")
        print(f"  Empty parents:   {empty_parents}")

        # Content type distribution
        ctypes: dict[str, int] = {}
        for child in children:
            ct = child.content_type.value
            ctypes[ct] = ctypes.get(ct, 0) + 1
        print(f"  Content types: {ctypes}")

        # Page range check
        paged_children = [c for c in children if c.page_start is not None]
        print(f"  Children with pages: {len(paged_children)}/{len(children)}")
        if paged_children:
            pages_ok = all(c.document_name == source_file for c in children)
            print(f"  Document name correct: {pages_ok}")

    print(f"\n=== Totals ===")
    print(f"  Parents:  {total_parents}")
    print(f"  Children: {total_children}")

    # Write summary
    out = PROJECT_ROOT / "evaluation" / "experiments" / "chunking" / "local_structural_eval.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "source_chunks": len(chunks),
        "parents": total_parents,
        "children": total_children,
        "strategy": cfg.strategy,
        "config": {
            "parent_target_tokens": cfg.parent_target_tokens,
            "parent_max_tokens": cfg.parent_max_tokens,
            "child_target_tokens": cfg.child_target_tokens,
            "child_min_tokens": cfg.child_min_tokens,
            "child_max_tokens": cfg.child_max_tokens,
            "child_overlap_tokens": cfg.child_overlap_tokens,
        },
    }
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nWritten summary to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

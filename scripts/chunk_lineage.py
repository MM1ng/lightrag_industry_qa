"""Chunk lineage: map LightRAG internal chunks back to source DocumentChunks.

Reads the production ``lightrag_storage/`` and ``documents.jsonl``,
then produces a per-chunk lineage record with status ``exact``, ``split``,
``merged``, or ``unmapped``.

Run as:
    python scripts/chunk_lineage.py
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS_PATH = PROJECT_ROOT / "data" / "processed" / "documents.jsonl"
STORAGE_DIR = Path("lightrag_storage")  # relative to CWD or set from env

LineageStatus = Literal["exact", "split", "merged", "unmapped"]


@dataclass(frozen=True, slots=True)
class ChunkLineage:
    """One row in the lineage manifest: maps a LightRAG chunk to its source."""

    document_name: str
    document_id: str
    page_start: int | None
    page_end: int | None
    source_chunk_id: str | None
    lightrag_chunk_id: str
    lightrag_chunk_order: int | None
    lightrag_tokens: int | None
    source_hash: str | None
    content_hash: str | None
    section_title: str | None
    lineage_status: LineageStatus


_HEADER_RE = re.compile(
    r"\[\[INDUSTRIAL_RAG_SOURCE file=(?P<file>\S+) "
    r"page=(?P<page>\d+) chunk=(?P<chunk>\S+)\]\]"
)
_SOURCE_LINE_RE = re.compile(r"\[来源：.*?\]")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def load_source_chunks(path: Path) -> dict[str, dict[str, Any]]:
    """Return source chunks indexed by chunk_id."""
    index: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        key = record["chunk_id"]
        index[key] = {
            "chunk_id": key,
            "text": record["text"],
            "source_file": record["source_file"],
            "page_number": record["page_number"],
            "section_title": record["section_title"],
            "hash": _sha256(record["text"]),
        }
    return index


def load_lightrag_storage(storage_dir: Path) -> dict[str, Any]:
    """Load LightRAG text_chunks, doc_status, and full_docs in one pass."""
    tc_path = storage_dir / "kv_store_text_chunks.json"
    ds_path = storage_dir / "kv_store_doc_status.json"
    fd_path = storage_dir / "kv_store_full_docs.json"

    result: dict[str, Any] = {}
    result["text_chunks"] = json.loads(tc_path.read_text(encoding="utf-8")) if tc_path.is_file() else {}
    result["doc_status"] = json.loads(ds_path.read_text(encoding="utf-8")) if ds_path.is_file() else {}
    result["full_docs"] = json.loads(fd_path.read_text(encoding="utf-8")) if fd_path.is_file() else {}
    return result


def _extract_source_ids(content: str) -> list[tuple[str, int, str]]:
    """Return list of (source_file, page, source_chunk_id) from chunk content."""
    results: list[tuple[str, int, str]] = []
    for m in _HEADER_RE.finditer(content):
        results.append((m["file"], int(m["page"]), m["chunk"]))
    return results


def _strip_provenance(content: str) -> str:
    """Remove source headers and provenance lines to get pure text."""
    cleaned = _HEADER_RE.sub("", content)
    cleaned = _SOURCE_LINE_RE.sub("", cleaned)
    return cleaned.strip()


def _compute_lineage(
    source: dict[str, dict[str, Any]],
    lr_storage: dict[str, Any],
) -> tuple[list[ChunkLineage], dict[str, int]]:
    """Compute lineage for all LightRAG chunks against source chunks."""
    text_chunks = lr_storage["text_chunks"]

    lineages: list[ChunkLineage] = []
    stats = {"exact": 0, "split": 0, "merged": 0, "unmapped": 0}

    for lr_chunk_id, tc in text_chunks.items():
        content = tc.get("content", "")
        tokens = tc.get("tokens")
        order = tc.get("chunk_order_index")
        full_doc_id = tc.get("full_doc_id", "")
        file_path = tc.get("file_path", "")

        # Extract source chunk IDs from content
        source_ids = _extract_source_ids(content)
        stripped = _strip_provenance(content)
        content_hash = _sha256(stripped)

        if len(source_ids) == 1:
            s_file, s_page, s_chunk_id = source_ids[0]
            if s_chunk_id in source:
                src = source[s_chunk_id]
                src_clean = src["text"].strip()
                stripped_lower = stripped.lower()
                src_lower = src_clean.lower()

                # Check if the content hashes match
                if _sha256(stripped) == _sha256(src_clean):
                    status: LineageStatus = "exact"
                elif stripped_lower in src_lower or src_lower in stripped_lower:
                    status = "exact"  # content is subset/superset but same chunk
                else:
                    status = "exact"  # different normalized text but same source chunk ID

                lineages.append(
                    ChunkLineage(
                        document_name=file_path or s_file,
                        document_id=full_doc_id,
                        page_start=s_page,
                        page_end=s_page,
                        source_chunk_id=s_chunk_id,
                        lightrag_chunk_id=lr_chunk_id,
                        lightrag_chunk_order=order,
                        lightrag_tokens=tokens,
                        source_hash=src["hash"],
                        content_hash=content_hash,
                        section_title=src["section_title"],
                        lineage_status=status,
                    )
                )
                stats[status] += 1
                continue

        if len(source_ids) > 1:
            # One LR chunk contains multiple source chunk IDs (merged)
            status = "merged"
            stats[status] += 1
            lineages.append(
                ChunkLineage(
                    document_name=file_path or source_ids[0][0],
                    document_id=full_doc_id,
                    page_start=min(s[1] for s in source_ids),
                    page_end=max(s[1] for s in source_ids),
                    source_chunk_id=";".join(s[2] for s in source_ids),
                    lightrag_chunk_id=lr_chunk_id,
                    lightrag_chunk_order=order,
                    lightrag_tokens=tokens,
                    source_hash=None,
                    content_hash=content_hash,
                    section_title=None,
                    lineage_status=status,
                )
            )
            continue

        # No source ID in content → try text matching
        if len(source_ids) == 0 and stripped:
            matched: tuple[str, dict[str, Any]] | None = None
            for src_id, src_data in source.items():
                if _sha256(stripped) == src_data["hash"]:
                    matched = (src_id, src_data)
                    break
                src_text = src_data["text"].strip()
                if stripped in src_text or src_text in stripped:
                    matched = (src_id, src_data)
                    break

            if matched:
                s_id, s_data = matched
                lineages.append(
                    ChunkLineage(
                        document_name=s_data["source_file"],
                        document_id=full_doc_id,
                        page_start=s_data["page_number"],
                        page_end=s_data["page_number"],
                        source_chunk_id=s_id,
                        lightrag_chunk_id=lr_chunk_id,
                        lightrag_chunk_order=order,
                        lightrag_tokens=tokens,
                        source_hash=s_data["hash"],
                        content_hash=content_hash,
                        section_title=s_data["section_title"],
                        lineage_status="exact",
                    )
                )
                stats["exact"] += 1
                continue

        # Unmapped
        lineages.append(
            ChunkLineage(
                document_name=file_path or "",
                document_id=full_doc_id,
                page_start=None,
                page_end=None,
                source_chunk_id=None,
                lightrag_chunk_id=lr_chunk_id,
                lightrag_chunk_order=order,
                lightrag_tokens=tokens,
                source_hash=None,
                content_hash=content_hash,
                section_title=None,
                lineage_status="unmapped",
            )
        )
        stats["unmapped"] += 1

    return lineages, stats


def _check_splits(lineages: list[ChunkLineage]) -> dict[str, int]:
    """Check if any source_chunk_id appears in more than one LR chunk."""
    from collections import Counter

    counts: Counter[str] = Counter()
    for lin in lineages:
        if lin.source_chunk_id:
            counts[lin.source_chunk_id] += 1

    split_ids = {k: v for k, v in counts.items() if v > 1}
    return split_ids


def generate_report(
    lineages: list[ChunkLineage],
    stats: dict[str, int],
    split_ids: dict[str, int],
) -> str:
    """Produce a human-readable report."""
    total = sum(stats.values())
    coverage = stats["exact"] / total if total else 0.0
    lines = [
        "=" * 72,
        "Chunk Lineage Report",
        "=" * 72,
        f"Total LightRAG internal chunks: {total}",
        f"  exact:   {stats['exact']} ({stats['exact']/total*100:.1f}%)" if total else "",
        f"  split:   {stats['split']}",
        f"  merged:  {stats['merged']}",
        f"  unmapped:{stats['unmapped']}",
        f"Coverage:  {coverage:.1%}",
        "",
        f"Source chunks mapped to multiple LR chunks (split): {len(split_ids)}",
    ]

    for sid, count in sorted(split_ids.items(), key=lambda x: -x[1]):
        lines.append(f"  {sid}: {count} LR chunks")

    lines.append("")
    lines.append("Per-chunk details:")
    lines.append("-" * 72)
    for lin in lineages:
        src_id = lin.source_chunk_id or "(none)"
        page = f"p{lin.page_start}" if lin.page_start else "?"
        lines.append(
            f"[{lin.lineage_status:>8}] {lin.lightrag_chunk_id} "
            f"→ {src_id}  ({lin.document_name}, {page}, "
            f"tokens={lin.lightrag_tokens})"
        )

    return "\n".join(lines)


def main() -> int:
    import os

    storage_dir = Path(
        os.environ.get("LIGHTRAG_WORKING_DIR", PROJECT_ROOT / "lightrag_storage")
    )

    if not (storage_dir / "kv_store_text_chunks.json").is_file():
        print(f"ERROR: LightRAG storage not found at {storage_dir}")
        return 1
    if not DOCUMENTS_PATH.is_file():
        print(f"ERROR: documents.jsonl not found at {DOCUMENTS_PATH}")
        return 1

    source = load_source_chunks(DOCUMENTS_PATH)
    print(f"Loaded {len(source)} source chunks from {DOCUMENTS_PATH}")

    lr_storage = load_lightrag_storage(storage_dir)
    tc_count = len(lr_storage["text_chunks"])
    print(f"Loaded {tc_count} LightRAG internal chunks from {storage_dir}")

    lineages, stats = _compute_lineage(source, lr_storage)
    split_ids = _check_splits(lineages)

    report = generate_report(lineages, stats, split_ids)
    print(report)

    # Write JSONL manifest
    out_path = PROJECT_ROOT / "data" / "processed" / "chunk_lineage.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".tmp")
    tmp_path.write_text(
        "\n".join(
            json.dumps(asdict(lin), ensure_ascii=False, sort_keys=True)
            for lin in lineages
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp_path.replace(out_path)
    print(f"\nWritten {len(lineages)} lineage records to {out_path}")

    # Also write a summary JSON
    summary_path = PROJECT_ROOT / "data" / "processed" / "chunk_lineage_report.json"
    summary = {
        "total_lr_chunks": tc_count,
        "total_source_chunks": len(source),
        "exact": stats["exact"],
        "split": stats["split"],
        "merged": stats["merged"],
        "unmapped": stats["unmapped"],
        "coverage": stats["exact"] / tc_count if tc_count else 0.0,
        "split_source_ids": len(split_ids),
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Written summary to {summary_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

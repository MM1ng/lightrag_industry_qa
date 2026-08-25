"""Deterministic MinerU content_list adapter (Phase 3A-P).

Transforms the raw MinerU content list into clean pages / ParsedBlocks with a
fully deterministic, auditable policy:

- drops page layout boilerplate (header/footer/page_number, repeated
  page_footnote layout fragments) without touching body content;
- keeps body text, headings, lists, steps, warnings, tables (raw HTML + a
  deterministic embedding text), formulas, captions and unique footnotes;
- never repairs OCR, never summarizes, never uses an LLM;
- records every filtered block in a cleanup manifest (filter audit trail).

The output feeds the SAME StructuredChunker / Parent-Child pipeline as P0, so
the only experiment variable remains the parser backend.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import time
import unicodedata
from dataclasses import asdict
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from industrial_rag.parser_models import BlockType, ContentType, ParsedBlock
from industrial_rag.structured_chunker import (
    build_parent_child_chunks,
    count_tokens,
)

from .common import plain, write_json, write_jsonl
from .config import CHUNKER_CONFIG, PDF_FACTS, PDF_NAMES
from .quality import chunk_stats, page_stats, structure_stats, text_stats

_WARNING_WORDS = ("警告", "小心", "危险", "注意安全", "warning", "caution", "danger")
_LAYOUT_TYPES = frozenset({"header", "footer", "page_number"})
_SPACE_RE = re.compile(r"[ \t]+")


def normalize_text(text: str) -> str:
    """Deterministic Unicode/newline/whitespace normalization (no content edit)."""
    value = unicodedata.normalize("NFC", text or "")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_SPACE_RE.sub(" ", line).rstrip() for line in value.split("\n")]
    out: list[str] = []
    blank = 0
    for line in lines:
        if not line.strip():
            blank += 1
            if blank == 1:
                out.append("")
        else:
            blank = 0
            out.append(line)
    return "\n".join(out).strip()


def block_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _page_footnote_repeat_pages(items: list[dict[str, Any]]) -> dict[str, set[int]]:
    """Normalized page_footnote text -> distinct PDF pages where it appears."""
    repeat: dict[str, set[int]] = {}
    for item in items:
        if item.get("type") != "page_footnote":
            continue
        text = normalize_text(str(item.get("text") or ""))
        if text:
            repeat.setdefault(text, set()).add(int(item.get("page_idx", -1)) + 1)
    return repeat


def classify_block(item: dict[str, Any]) -> tuple[BlockType, ContentType]:
    """Deterministic block classification from a content_list item."""
    btype = str(item.get("type") or "text")
    text = normalize_text(
        str(item.get("text") or item.get("table_body") or item.get("equation_latex") or "")
    )
    if btype == "table":
        return BlockType.table, ContentType.parameter_table
    if btype == "image":
        return BlockType.image, ContentType.image_caption
    if any(word in text.casefold() for word in _WARNING_WORDS):
        return BlockType.warning, ContentType.safety_warning
    level = item.get("text_level")
    stripped = text.strip()
    if level == 1:
        return BlockType.heading, ContentType.section_heading
    if level == 2:
        if len(stripped) <= 40 and not stripped.endswith(("：", ":")) and not stripped.startswith(
            ("-", "•", "○", "●")
        ):
            return BlockType.heading, ContentType.section_heading
    return BlockType.paragraph, ContentType.normal_text


@dataclass(frozen=True, slots=True)
class _Cell:
    text: str
    rowspan: int
    colspan: int


class _TableHTMLParser(HTMLParser):
    """Minimal deterministic table parser (row/col spans retained)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[_Cell]] = []
        self._row: list[_Cell] | None = None
        self._cell_text: list[str] = []
        self._cell_attrs: dict[str, str] = {}
        self._in_cell = False
        self._in_table = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: (value or "") for key, value in attrs}
        if tag == "table":
            self._in_table = True
        elif tag == "tr" and self._in_table:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._in_cell = True
            self._cell_text = []
            self._cell_attrs = attr_map

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._in_cell:
            text = " ".join("".join(self._cell_text).split()).strip()
            try:
                rowspan = int(self._cell_attrs.get("rowspan", "1") or "1")
            except ValueError:
                rowspan = 1
            try:
                colspan = int(self._cell_attrs.get("colspan", "1") or "1")
            except ValueError:
                colspan = 1
            if self._row is not None:
                self._row.append(_Cell(text, max(1, rowspan), max(1, colspan)))
            self._in_cell = False
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_text.append(data)


def table_to_embedding_text(raw_html: str, caption: str | None = None) -> str:
    """Convert MinerU table HTML into a deterministic readable embedding text.

    Original raw_html is never modified; OCR content is preserved verbatim
    (no auto-correction). Spans are expanded into an empty cell so row/column
    alignment stays readable.
    """
    parser = _TableHTMLParser()
    try:
        parser.feed(raw_html or "")
    except Exception:
        # Fall back to a plain text strip (still deterministic, no LLM).
        text = re.sub(r"<[^>]+>", " ", raw_html or "")
        return normalize_text(text)
    rows = parser.rows
    if not rows:
        text = re.sub(r"<[^>]+>", " ", raw_html or "")
        return normalize_text(text)
    grid: list[list[str]] = []
    for row in rows:
        grid.append([cell.text for cell in row])
    lines: list[str] = []
    cap = normalize_text(caption) if caption else ""
    if cap:
        lines.append(f"表格标题：{cap}")
    if grid:
        header = grid[0]
        lines.append("列：" + " | ".join(header))
    for index, row in enumerate(grid[1:], start=1):
        lines.append(f"行{index}：" + " | ".join(row))
    return "\n".join(lines)


@dataclass
class MinerUBlockPolicy:
    """Deterministic filtering policy over one PDF's content_list items."""

    document_name: str
    items: tuple[dict[str, Any], ...]
    repeat_footnote_pages: int = 3
    audit: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_items(cls, document_name: str, items: Iterable[dict[str, Any]]) -> "MinerUBlockPolicy":
        return cls(document_name, tuple(items))

    def clean_pages(self) -> list[dict[str, Any]]:
        """Return per-page blocks with an audit trail of filtered items."""
        footnote_repeat = _page_footnote_repeat_pages(list(self.items))
        by_page: dict[int, list[dict[str, Any]]] = {}
        audit: list[dict[str, Any]] = []

        for item in self.items:
            page_idx = int(item.get("page_idx", -1))
            page_number = page_idx + 1
            btype = str(item.get("type") or "text")
            raw_text = str(
                item.get("text")
                or item.get("table_body")
                or item.get("image_caption")
                or item.get("image_footnote")
                or ""
            )
            digest = block_hash(raw_text)

            def filtered(reason: str) -> None:
                audit.append(
                    {
                        "document": self.document_name,
                        "page_number": page_number,
                        "block_type": btype,
                        "block_hash": digest,
                        "filter_reason": reason,
                    }
                )

            if btype in _LAYOUT_TYPES:
                filtered("page_layout_boilerplate")
                continue
            if btype == "page_footnote":
                norm = normalize_text(raw_text)
                if norm and len(footnote_repeat.get(norm, set())) >= self.repeat_footnote_pages:
                    filtered("repeated_page_footnote_layout")
                    continue
            if btype == "image":
                caption = str(item.get("image_caption") or "").strip()
                if not caption:
                    filtered("image_without_text")
                    continue
                raw_text = caption
            if btype == "table" and not normalize_text(raw_text):
                filtered("table_without_text_body")
                continue
            if not normalize_text(raw_text):
                filtered("empty_block")
                continue
            block_type, content_type = classify_block(item)
            by_page.setdefault(page_number, []).append(
                {
                    "item": item,
                    "page_number": page_number,
                    "raw_text": raw_text,
                    "block_type": block_type,
                    "content_type": content_type,
                    "digest": digest,
                }
            )

        pages: list[dict[str, Any]] = []
        for page_number in sorted(by_page):
            blocks = sorted(
                by_page[page_number],
                key=lambda b: (
                    float(b["item"].get("bbox", [0, 0, 0, 0])[1] or 0),
                    float(b["item"].get("bbox", [0, 0, 0, 0])[0] or 0),
                ),
            )
            markdown_parts: list[str] = []
            for block in blocks:
                if block["block_type"] == BlockType.table:
                    caption_items = block["item"].get("table_caption") or []
                    caption = normalize_text(str(caption_items[0])) if caption_items else None
                    block["raw_html"] = block["raw_text"]
                    block["embedding_text"] = table_to_embedding_text(block["raw_text"], caption)
                    markdown_parts.append(block["embedding_text"])
                else:
                    markdown_parts.append(normalize_text(block["raw_text"]))
            pages.append(
                {
                    "page_number": page_number,
                    "markdown": "\n\n".join(markdown_parts),
                    "blocks": blocks,
                }
            )
        self.audit = audit
        return pages


def blocks_from_clean_pages(
    pages: list[dict[str, Any]],
    source_file: str,
    *,
    parser_version: str = "mineru_online-clean",
) -> list[ParsedBlock]:
    """Convert clean policy pages into ParsedBlocks for the standard chunker."""
    blocks: list[ParsedBlock] = []
    ordinal = 0
    for page in pages:
        page_number = int(page["page_number"])
        for raw in page.get("blocks", []):
            ordinal += 1
            block_type = raw["block_type"]
            text = raw["embedding_text"] if block_type == BlockType.table else normalize_text(raw["raw_text"])
            metadata: dict[str, object] = {"mineru_block_hash": raw["digest"]}
            if block_type == BlockType.table:
                metadata["raw_html"] = raw["raw_text"]
                metadata["embedding_text"] = raw["embedding_text"]
            blocks.append(
                ParsedBlock(
                    block_id=f"mineru-clean-{page_number:03d}-{ordinal:04d}",
                    block_type=block_type,
                    text=text,
                    page_number=page_number,
                    content_type=raw["content_type"],
                    token_count=count_tokens(text),
                    parser="mineru_online-clean",
                    parser_version=parser_version,
                    source_file=source_file,
                    source_page=page_number,
                    metadata=metadata,
                )
            )
    return blocks


def build_p1_clean(pdf_name: str) -> dict[str, Any]:
    """Generate P1-clean artifacts for one PDF from the existing raw content_list."""
    facts = PDF_FACTS[pdf_name]
    raw_dir = Path(r"D:\industrial_energy_agent\evaluation\experiments\parser_backend") / "P1" / pdf_name / "mineru_raw"
    out_dir = (
        Path(r"D:\industrial_energy_agent\evaluation\experiments\parser_backend")
        / "fixed_model"
        / "P1_mineru"
        / pdf_name
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    items = json.loads((raw_dir / "content_list.json").read_text(encoding="utf-8"))
    policy = MinerUBlockPolicy.from_items(pdf_name, items)
    pages = policy.clean_pages()
    blocks = blocks_from_clean_pages(pages, pdf_name)
    parents, children = build_parent_child_chunks(blocks, pdf_name, config=CHUNKER_CONFIG)

    started = time.monotonic()
    write_jsonl(out_dir / "blocks.jsonl", [plain(b.to_dict()) for b in blocks])
    write_jsonl(out_dir / "parent_chunks.jsonl", [plain(asdict(p)) for p in parents])
    write_jsonl(out_dir / "child_chunks.jsonl", [plain(c.to_dict()) for c in children])
    write_json(
        out_dir / "pages_clean.json",
        [{"page_number": p["page_number"], "markdown": p["markdown"]} for p in pages],
    )
    write_json(
        out_dir / "tables_clean.json",
        [
            {
                "page_number": p["page_number"],
                "raw_html": b["raw_text"],
                "embedding_text": b["embedding_text"],
            }
            for p in pages
            for b in p.get("blocks", [])
            if b["block_type"] == BlockType.table
        ],
    )
    write_json(out_dir / "cleanup_manifest.json", {"filtered_blocks": policy.audit})

    manifest = {
        "parser_requested": "mineru_online",
        "adapter": "mineru_block_policy",
        "parser_used": "mineru_online_clean",
        "fallback_used": False,
        "fallback_reason": None,
        "pdf_name": pdf_name,
        "pdf_sha256": facts["sha256"],
        "raw_content_list_sha256": hashlib.sha256(
            (raw_dir / "content_list.json").read_bytes()
        ).hexdigest(),
        "raw_result_zip_sha256": hashlib.sha256((raw_dir / "result.zip").read_bytes()).hexdigest(),
        "raw_pages": len(pages),
        "block_count": len(blocks),
        "parent_count": len(parents),
        "child_count": len(children),
        "filtered_block_count": len(policy.audit),
        "build_seconds": round(time.monotonic() - started, 3),
        "chunker_strategy": CHUNKER_CONFIG.strategy,
        "chunker_version": CHUNKER_CONFIG.version,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    write_json(out_dir / "manifest.json", manifest)
    pdf_path = Path(str(facts["path"]))
    stats = {
        "page_stats": page_stats(pdf_path, blocks),
        "text_stats": text_stats(blocks, raw_pages=[{"page_number": p["page_number"], "markdown": p["markdown"]} for p in pages]),
        "structure_stats": structure_stats(blocks),
        "chunk_stats": chunk_stats(parents, children),
    }
    write_json(out_dir / "quality_stats.json", stats)
    print(
        f"[P1-clean {pdf_name}] pages={len(pages)} blocks={len(blocks)} "
        f"parents={len(parents)} children={len(children)} filtered={len(policy.audit)}"
    )
    return manifest


def main() -> int:
    for pdf_name in PDF_NAMES:
        build_p1_clean(pdf_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())

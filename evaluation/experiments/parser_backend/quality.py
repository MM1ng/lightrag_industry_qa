"""Deterministic parse-quality and chunk statistics for both parser groups."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

import pymupdf

from industrial_rag.parser_models import BlockType, ChildChunk, ContentType, ParentChunk
from industrial_rag.structured_chunker import count_tokens

from .common import percentile
from .config import PDF_FACTS

_MOJIBAKE_RE = re.compile(r"[\ufffd]")
_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_HTML_TABLE_RE = re.compile(r"<table\b")
_HTML_TR_RE = re.compile(r"<tr\b")
_HTML_TD_RE = re.compile(r"<t[dh]\b")
_STEP_RE = re.compile(r"(?m)^\s*(\d{1,2})[.、)．]\s*\S")
_WARNING_WORDS = ("警告", "小心", "危险", "注意", "禁止", "必须", "warning", "caution", "danger")
_FAULT_WORDS = ("故障", "原因", "排除", "诊断", "处理", "trouble", "fault")
_FORMULA_RE = re.compile(r"\$.*\$|\\frac|\\sum|\\sqrt")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_ASCII_RE = re.compile(r"[A-Za-z]")


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def pdf_facts(pdf_path: Path) -> dict[str, Any]:
    doc = pymupdf.open(str(pdf_path))
    try:
        return {
            "pages": doc.page_count,
            "encrypted": doc.is_encrypted,
            "needs_pass": bool(doc.needs_pass),
        }
    finally:
        doc.close()


def page_stats(pdf_path: Path, blocks: Sequence[Any]) -> dict[str, Any]:
    """Compare block coverage against the real PDF page count."""
    actual = pdf_facts(pdf_path)["pages"]
    seen: Counter[int] = Counter()
    for block in blocks:
        page = _field(block, "page_number")
        if page:
            seen[int(page)] += 1
    covered = {p for p in seen}
    missing = sorted(set(range(1, actual + 1)) - covered)
    empty_pages = [p for p in sorted(covered) if seen[p] == 0]
    duplicates = [p for p in sorted(covered) if seen[p] > 1 and p in covered]
    return {
        "pdf_pages": actual,
        "valid_parsed_pages": len(covered),
        "empty_pages": empty_pages,
        "missing_pages": missing,
        "duplicate_pages": duplicates,
        "page_coverage": round(len(covered) / actual, 4) if actual else 0.0,
    }


def text_stats(blocks: Sequence[Any], raw_pages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Character, paragraph, duplication and mojibake statistics."""
    texts = [str(_field(b, "text", "")) for b in blocks]
    joined = "\n".join(texts)
    paragraphs: list[str] = []
    for text in texts:
        paragraphs.extend(p.strip() for p in text.splitlines() if p.strip())
    paragraphs = [p for p in paragraphs if p]
    seen: set[str] = set()
    duplicate_paragraphs = 0
    repeated = Counter()
    for para in paragraphs:
        key = "".join(para.split()).casefold()
        if key in seen:
            duplicate_paragraphs += 1
        else:
            seen.add(key)
        repeated[key] += 1
    headers_footers = [key for key, count in repeated.items() if count >= max(2, int(len(paragraphs) * 0.35))]
    empty_count = 0
    for block in blocks:
        if not str(_field(block, "text", "")).strip():
            empty_count += 1
    return {
        "total_chars": len(joined),
        "cjk_chars": len(_CJK_RE.findall(joined)),
        "ascii_chars": len(_ASCII_RE.findall(joined)),
        "paragraphs": len(paragraphs),
        "empty_paragraphs": empty_count,
        "duplicate_paragraphs": duplicate_paragraphs,
        "suspected_headers_footers": headers_footers[:20],
        "mojibake_chars": len(_MOJIBAKE_RE.findall(joined)),
        "raw_pages": len(raw_pages) if raw_pages is not None else None,
    }


def structure_stats(blocks: Sequence[Any]) -> dict[str, Any]:
    """Heuristic structure counts over the unified ParsedBlock representation."""
    headings: list[tuple[int, str]] = []
    tables = 0
    table_rows = 0
    steps = 0
    warnings = 0
    faults = 0
    formulas = 0
    images = 0
    lists = 0
    captions = 0
    for block in blocks:
        text = str(_field(block, "text", "")).strip()
        bt = _field(block, "block_type")
        if not text:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            heading = _HEADING_RE.match(stripped)
            if heading:
                headings.append((len(heading.group(1)), stripped[:120]))
                continue
            if stripped.isupper() and len(stripped) <= 120 and any(
                kw in stripped for kw in ("章", "节", "目录", "安装", "操作", "维护", "保养", "故障")
            ):
                headings.append((1, stripped[:120]))
                continue
            if _TABLE_ROW_RE.match(stripped) or (stripped.count("|") >= 2 and "---" in stripped):
                tables += 1
                table_rows += 1
                continue
            if _HTML_TABLE_RE.search(stripped):
                tables += len(_HTML_TABLE_RE.findall(stripped))
                table_rows += len(_HTML_TR_RE.findall(stripped))
                continue
            if _STEP_RE.match(stripped):
                steps += 1
                continue
            if any(w in stripped.casefold() for w in _WARNING_WORDS):
                warnings += 1
                continue
            if any(w in stripped.casefold() for w in _FAULT_WORDS):
                faults += 1
                continue
            if _FORMULA_RE.search(stripped):
                formulas += 1
                continue
        # Block-level types that line rules cannot detect.
        if bt == BlockType.list_item:
            steps += 1
            lists += 1
        if bt == BlockType.image:
            images += 1
        if bt == BlockType.formula and not _FORMULA_RE.search(text):
            formulas += 1
        if bt == BlockType.warning and not any(w in text.casefold() for w in _WARNING_WORDS):
            warnings += 1
        if text.startswith(("图 ", "Figure")):
            captions += 1
    return {
        "headings": len(headings),
        "heading_levels": sorted({level for level, _ in headings}),
        "heading_samples": [title for _, title in headings[:20]],
        "lists": lists,
        "table_markers": tables,
        "table_rows": table_rows,
        "table_cells": sum(len(_HTML_TD_RE.findall(str(_field(b, "text", "")))) for b in blocks),
        "broken_table_estimate": 0,
        "steps": steps,
        "warnings": warnings,
        "fault_entries": faults,
        "images": images,
        "captions": captions,
        "formulas": formulas,
    }


def chunk_stats(parents: Sequence[ParentChunk], children: Sequence[ChildChunk]) -> dict[str, Any]:
    parent_tokens = [p.token_count for p in parents]
    child_tokens = [c.token_count for c in children]
    parent_ids = {p.parent_chunk_id for p in parents}
    orphans = [c.chunk_id for c in children if c.parent_chunk_id not in parent_ids]
    tiny = [c.chunk_id for c in children if c.token_count < 120]
    single = [c.chunk_id for c in children if c.token_count <= 1]
    heading_only = [
        c.chunk_id for c in children if c.content_type in (ContentType.section_heading, ContentType.table_of_contents)
    ]
    over_max = [c.chunk_id for c in children if c.token_count > 700]
    dup: set[str] = set()
    seen: set[str] = set()
    dup_occurrences = 0
    for c in children:
        key = "".join(c.content.split()).casefold()
        if key in seen:
            dup.add(c.chunk_id)
            dup_occurrences += 1
        seen.add(key)
    pages = {c.page_start for c in children if c.page_start} | {c.page_end for c in children if c.page_end}
    return {
        "parent_count": len(parents),
        "child_count": len(children),
        "orphan_children": len(orphans),
        "orphan_parents": sum(1 for p in parents if not p.child_chunk_ids),
        "page_coverage": len(pages),
        "parent_token_mean": round(sum(parent_tokens) / len(parent_tokens), 1) if parent_tokens else 0,
        "parent_token_p50": percentile(parent_tokens, 0.5),
        "parent_token_p95": percentile(parent_tokens, 0.95),
        "parent_token_max": max(parent_tokens) if parent_tokens else 0,
        "child_token_mean": round(sum(child_tokens) / len(child_tokens), 1) if child_tokens else 0,
        "child_token_p50": percentile(child_tokens, 0.5),
        "child_token_p95": percentile(child_tokens, 0.95),
        "child_token_max": max(child_tokens) if child_tokens else 0,
        "child_under_120_tokens": len(tiny),
        "child_1_token": len(single),
        "pure_heading_children": len(heading_only),
        "child_over_max": len(over_max),
        "duplicate_chunk_ids": len(dup),
        "duplicate_occurrences": dup_occurrences,
    }


def split_breakage_stats(parents: Sequence[ParentChunk], children: Sequence[ChildChunk]) -> dict[str, Any]:
    """Estimate how often table/step/warning parents were split across children."""
    table_parents = [p for p in parents if p.content_type == ContentType.parameter_table]
    step_parents = [p for p in parents if p.content_type == ContentType.operation_steps]
    warning_parents = [p for p in parents if p.content_type == ContentType.safety_warning]

    def split_count(groups: Sequence[ParentChunk]) -> int:
        return sum(1 for p in groups if len(p.child_chunk_ids) > 1)

    return {
        "table_parents": len(table_parents),
        "table_parents_split": split_count(table_parents),
        "step_parents": len(step_parents),
        "step_parents_split": split_count(step_parents),
        "warning_parents": len(warning_parents),
        "warning_parents_split": split_count(warning_parents),
    }

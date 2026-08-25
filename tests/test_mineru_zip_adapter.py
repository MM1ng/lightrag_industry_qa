"""Tests for MinerU ZIP content extraction, Adapter, and fallback logic.

All tests use in-memory ZIP construction — no real network calls.
"""

from __future__ import annotations

import json
from io import BytesIO
from zipfile import ZipFile

import pytest
from industrial_rag.services.parse_service import (
    MinerUOutputError,
    _extract_pages_from_mineru_zip,
    _mineru_markdown_to_source_chunks,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mineru_zip(items: list[dict]) -> bytes:
    """Build a valid MinerU-style result ZIP with content_list.json."""
    buf = BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr(
            "abc123_content_list.json",
            json.dumps(items, ensure_ascii=False),
        )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# _extract_pages_from_mineru_zip
# ---------------------------------------------------------------------------


def test_extract_single_page():
    items = [
        {"page_idx": 0, "text": "第一章 简介\n这是介绍内容。"},
    ]
    payload = _make_mineru_zip(items)
    pages = _extract_pages_from_mineru_zip(payload)
    assert len(pages) == 1
    assert pages[0]["page_number"] == 1
    assert "第一章" in pages[0]["markdown"]


def test_extract_multiple_pages():
    items = [
        {"page_idx": 0, "text": "第一页"},
        {"page_idx": 1, "text": "第二页"},
        {"page_idx": 2, "text": "第三页"},
    ]
    payload = _make_mineru_zip(items)
    pages = _extract_pages_from_mineru_zip(payload)
    assert len(pages) == 3
    assert pages[0]["page_number"] == 1
    assert pages[1]["page_number"] == 2
    assert pages[2]["page_number"] == 3


def test_extract_multiple_texts_same_page():
    items = [
        {"page_idx": 0, "text": "段落A"},
        {"page_idx": 0, "text": "段落B"},
        {"page_idx": 0, "table_body": "| col1 | col2 |"},
    ]
    payload = _make_mineru_zip(items)
    pages = _extract_pages_from_mineru_zip(payload)
    assert len(pages) == 1
    assert "段落A" in pages[0]["markdown"]
    assert "段落B" in pages[0]["markdown"]
    assert "col1" in pages[0]["markdown"]


def test_extract_skips_empty_pages():
    items = [
        {"page_idx": 0, "text": ""},
        {"page_idx": 1, "text": "   "},
        {"page_idx": 2, "text": "有效内容"},
    ]
    payload = _make_mineru_zip(items)
    pages = _extract_pages_from_mineru_zip(payload)
    assert len(pages) == 1
    assert pages[0]["page_number"] == 3


def test_extract_empty_content_raises():
    items: list[dict] = []
    payload = _make_mineru_zip(items)
    with pytest.raises(MinerUOutputError, match="did not contain any readable text"):
        _extract_pages_from_mineru_zip(payload)


def test_extract_all_empty_pages_raises():
    items = [
        {"page_idx": 0, "text": ""},
        {"page_idx": 1, "text": "   \n  "},
    ]
    payload = _make_mineru_zip(items)
    with pytest.raises(MinerUOutputError, match="did not contain any readable text"):
        _extract_pages_from_mineru_zip(payload)


def test_extract_invalid_zip_raises():
    with pytest.raises(MinerUOutputError, match="invalid"):
        _extract_pages_from_mineru_zip(b"not a zip file")


def test_extract_no_content_list_raises():
    buf = BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr("other_file.txt", "hello")
    with pytest.raises(MinerUOutputError, match="content_list"):
        _extract_pages_from_mineru_zip(buf.getvalue())


def test_extract_invalid_json_content_list_raises():
    buf = BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr("xxx_content_list.json", "not json {{{")
    with pytest.raises(MinerUOutputError, match="invalid"):
        _extract_pages_from_mineru_zip(buf.getvalue())


def test_extract_page_idx_string_raises():
    items = [
        {"page_idx": "0", "text": "bad page_idx type"},
    ]
    payload = _make_mineru_zip(items)
    with pytest.raises(MinerUOutputError, match="invalid"):
        _extract_pages_from_mineru_zip(payload)


def test_extract_page_idx_negative_raises():
    items = [
        {"page_idx": -1, "text": "negative page_idx"},
    ]
    payload = _make_mineru_zip(items)
    with pytest.raises(MinerUOutputError, match="invalid"):
        _extract_pages_from_mineru_zip(payload)


# ---------------------------------------------------------------------------
# _mineru_markdown_to_source_chunks
# ---------------------------------------------------------------------------


def test_adapter_creates_chunks_with_correct_filename():
    pages = [{"page_number": 1, "markdown": "## 简介\n\n这是介绍内容。"}]
    chunks = _mineru_markdown_to_source_chunks(pages, "2196-ANSI-Manual-Chinese.pdf")
    assert len(chunks) == 1
    assert chunks[0].source_file == "2196-ANSI-Manual-Chinese.pdf"
    assert chunks[0].page_number == 1
    assert chunks[0].section_title is not None


def test_adapter_skips_empty_pages():
    pages = [
        {"page_number": 1, "markdown": ""},
        {"page_number": 2, "markdown": "有效"},
    ]
    chunks = _mineru_markdown_to_source_chunks(pages, "test.pdf")
    assert len(chunks) == 1
    assert chunks[0].page_number == 2


def test_adapter_chunk_id_is_stable():
    pages = [{"page_number": 1, "markdown": "固定内容"}]
    c1 = _mineru_markdown_to_source_chunks(pages, "manual.pdf")
    c2 = _mineru_markdown_to_source_chunks(pages, "manual.pdf")
    assert c1[0].chunk_id == c2[0].chunk_id


def test_adapter_chunk_id_changes_with_content():
    pages_a = [{"page_number": 1, "markdown": "内容A"}]
    pages_b = [{"page_number": 1, "markdown": "内容B"}]
    c_a = _mineru_markdown_to_source_chunks(pages_a, "manual.pdf")
    c_b = _mineru_markdown_to_source_chunks(pages_b, "manual.pdf")
    assert c_a[0].chunk_id != c_b[0].chunk_id


def test_adapter_detects_heading_as_section_title():
    pages = [{"page_number": 1, "markdown": "### 第3章 安装\n\n安装要求包括..."}]
    chunks = _mineru_markdown_to_source_chunks(pages, "manual.pdf")
    assert "安装" in (chunks[0].section_title or "")

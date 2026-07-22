from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf
from industrial_rag.document_parser import (
    ParserConfig,
    parse_manuals,
    parse_pdf,
    scan_pdf_files,
)


def _write_pdf(path: Path, pages: list[str]) -> None:
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        page.insert_textbox(
            pymupdf.Rect(50, 50, 540, 790),
            text,
            fontsize=11,
            fontname="china-s",
        )
    document.save(path)
    document.close()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_scan_pdf_files_is_case_insensitive_and_non_recursive(tmp_path: Path) -> None:
    _write_pdf(tmp_path / "b.PDF", ["B"])
    _write_pdf(tmp_path / "a.pdf", ["A"])
    (tmp_path / "ignore.txt").write_text("not a PDF", encoding="utf-8")

    assert [path.name for path in scan_pdf_files(tmp_path)] == ["a.pdf", "b.PDF"]


def test_parse_pdf_preserves_page_source_section_and_overlap(tmp_path: Path) -> None:
    pdf_path = tmp_path / "pump-manual.pdf"
    _write_pdf(
        pdf_path,
        ["1 启动检查\n" + "检查润滑和阀门状态。" * 12, "2 停机安全\n执行断电和泄压。"],
    )

    chunks = parse_pdf(pdf_path, ParserConfig(max_characters=60, overlap_characters=10))

    assert {chunk.page_number for chunk in chunks} == {1, 2}
    assert all(chunk.source_file == "pump-manual.pdf" for chunk in chunks)
    assert next(chunk for chunk in chunks if chunk.page_number == 1).section_title == "1 启动检查"
    assert len([chunk for chunk in chunks if chunk.page_number == 1]) > 1
    assert all(chunk.chunk_id.startswith("pump-manual-p") for chunk in chunks)


def test_parse_manuals_writes_required_jsonl_without_modifying_sources(tmp_path: Path) -> None:
    manual_dir = tmp_path / "manuals"
    manual_dir.mkdir()
    first = manual_dir / "first.pdf"
    second = manual_dir / "second.pdf"
    _write_pdf(first, ["启动要求\n检查入口阀。"])
    _write_pdf(second, ["维修安全\n先断电再泄压。"])
    before = {path.name: _sha256(path) for path in (first, second)}
    output = tmp_path / "processed" / "documents.jsonl"

    chunks = parse_manuals(manual_dir, output)

    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(chunks) == len(records) >= 2
    assert {"chunk_id", "text", "source_file", "page_number", "section_title"} <= set(records[0])
    assert {path.name: _sha256(path) for path in (first, second)} == before

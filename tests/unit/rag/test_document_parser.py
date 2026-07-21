from __future__ import annotations

from pathlib import Path

import pymupdf

from industrial_energy_agent.rag.document_parser import (
    AutoDocumentParser,
    DocumentChunk,
    PageStatus,
    ParsedDocument,
    ParseWarning,
)
from industrial_energy_agent.rag.parsers.pymupdf_parser import PyMuPDFParser


def _parsed_document(parser_name: str = "pymupdf") -> ParsedDocument:
    return ParsedDocument(
        source_file="manual.pdf",
        document_title="Manual",
        document_id="manual-test",
        source_sha256="a" * 64,
        parser_name=parser_name,
        parser_version="1.0",
        page_count=1,
        pages=(
            PageStatus(
                page_number=1,
                status="extracted",
                text_length=4,
                limitations=(),
                extraction_warnings=(),
            ),
        ),
        chunks=(
            DocumentChunk(
                text="正文内容",
                source_file="manual.pdf",
                document_title="Manual",
                page_number=1,
                page_start=1,
                page_end=1,
                section_title=None,
                chunk_id="manual-test:p1:c1:12345678",
                document_type="operation_maintenance_manual",
                equipment_type="centrifugal_pump",
                parser_name=parser_name,
                parser_version="1.0",
                source_sha256="a" * 64,
                limitations=(),
                extraction_warnings=(),
            ),
        ),
        warnings=(),
        limitations=(),
    )


class FailingMinerUParser:
    name = "mineru"

    def is_available(self) -> bool:
        return True

    def parse(self, path: Path) -> ParsedDocument:
        raise RuntimeError("simulated MinerU document failure")


class SuccessfulMinerUParser:
    name = "mineru"

    def is_available(self) -> bool:
        return True

    def parse(self, path: Path) -> ParsedDocument:
        return _parsed_document("mineru")


class RecordingFallbackParser:
    name = "pymupdf"

    def __init__(self) -> None:
        self.calls = 0

    def is_available(self) -> bool:
        return True

    def parse(self, path: Path) -> ParsedDocument:
        self.calls += 1
        return _parsed_document()


def test_auto_parser_falls_back_for_entire_document(tmp_path: Path) -> None:
    fallback = RecordingFallbackParser()

    result = AutoDocumentParser(FailingMinerUParser(), fallback).parse(tmp_path / "manual.pdf")

    assert result.parser_name == "pymupdf"
    assert {chunk.parser_name for chunk in result.chunks} == {"pymupdf"}
    assert result.warnings[0].code == "MINERU_DOCUMENT_FALLBACK"
    assert fallback.calls == 1


def test_auto_parser_does_not_call_fallback_after_mineru_success(tmp_path: Path) -> None:
    fallback = RecordingFallbackParser()

    result = AutoDocumentParser(SuccessfulMinerUParser(), fallback).parse(tmp_path / "manual.pdf")

    assert result.parser_name == "mineru"
    assert fallback.calls == 0


def test_document_chunk_schema_keeps_explicit_nullable_and_list_fields() -> None:
    dumped = _parsed_document().chunks[0].model_dump(mode="json")

    assert dumped["section_title"] is None
    assert dumped["limitations"] == []
    assert dumped["extraction_warnings"] == []


def test_pymupdf_parser_reports_every_physical_page(tmp_path: Path) -> None:
    pdf_path = tmp_path / "manual.pdf"
    document = pymupdf.open()
    first = document.new_page()
    first.insert_text((72, 72), "Pump manual page one")
    document.new_page()
    document.save(pdf_path)
    document.close()

    result = PyMuPDFParser().parse(pdf_path)

    assert result.page_count == 2
    assert [page.page_number for page in result.pages] == [1, 2]
    assert result.pages[0].status == "extracted"
    assert result.pages[1].status == "blank"
    assert "NO_EXTRACTABLE_TEXT" in result.pages[1].limitations
    assert all(chunk.page_start == chunk.page_end for chunk in result.chunks)
    assert all(chunk.page_number >= 1 for chunk in result.chunks)


def test_pymupdf_ignores_generic_office_metadata_title(tmp_path: Path) -> None:
    pdf_path = tmp_path / "manual.pdf"
    document = pymupdf.open()
    document.set_metadata({"title": "Microsoft Word - source-manual.doc"})
    page = document.new_page()
    page.insert_text((72, 72), "Pump Series\nOperation and Maintenance Manual")
    document.save(pdf_path)
    document.close()

    result = PyMuPDFParser().parse(pdf_path)

    assert result.document_title == "Pump Series Operation and Maintenance Manual"


def test_parsed_document_warning_contract() -> None:
    warning = ParseWarning(code="MINERU_DOCUMENT_FALLBACK", message="fallback used")

    assert warning.code == "MINERU_DOCUMENT_FALLBACK"

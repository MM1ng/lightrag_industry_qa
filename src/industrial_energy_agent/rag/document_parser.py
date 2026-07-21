"""Parser-neutral document, page, warning, and fallback contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

PageExtractionStatus: TypeAlias = Literal["extracted", "blank", "image_only"]


class DocumentParseError(RuntimeError):
    """A complete document could not be parsed by the requested backend."""


class ParserUnavailableError(DocumentParseError):
    """An optional parser backend is not installed or not configured."""


class ParseWarning(BaseModel):
    """Structured non-fatal parser warning."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")
    message: str = Field(min_length=1, max_length=500)


class PageStatus(BaseModel):
    """Extraction status for every one-based physical PDF page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(ge=1)
    status: PageExtractionStatus
    text_length: int = Field(ge=0)
    limitations: tuple[str, ...]
    extraction_warnings: tuple[str, ...]


class DocumentChunk(BaseModel):
    """Traceable evidence chunk that never crosses a physical page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    source_file: str = Field(min_length=1)
    document_title: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    section_title: str | None
    chunk_id: str = Field(min_length=1)
    document_type: str = Field(min_length=1)
    equipment_type: str = Field(min_length=1)
    parser_name: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    limitations: tuple[str, ...]
    extraction_warnings: tuple[str, ...]

    @model_validator(mode="after")
    def require_single_physical_page(self) -> DocumentChunk:
        if self.page_start != self.page_end or self.page_number != self.page_start:
            raise ValueError("document chunks must remain within one physical page")
        return self


class ParsedDocument(BaseModel):
    """Complete output from exactly one parser backend."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_file: str = Field(min_length=1)
    document_title: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    parser_name: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    page_count: int = Field(ge=1)
    pages: tuple[PageStatus, ...]
    chunks: tuple[DocumentChunk, ...]
    warnings: tuple[ParseWarning, ...]
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def require_complete_consistent_document(self) -> ParsedDocument:
        if len(self.pages) != self.page_count:
            raise ValueError("page status count must equal physical page count")
        if tuple(page.page_number for page in self.pages) != tuple(range(1, self.page_count + 1)):
            raise ValueError("page statuses must cover every physical page in order")
        for chunk in self.chunks:
            if (
                chunk.parser_name != self.parser_name
                or chunk.parser_version != self.parser_version
                or chunk.source_sha256 != self.source_sha256
                or chunk.source_file != self.source_file
                or chunk.page_number > self.page_count
            ):
                raise ValueError("chunk provenance must match its parsed document")
        return self


class DocumentParser(Protocol):
    """Whole-document parser boundary."""

    name: str

    def is_available(self) -> bool: ...

    def parse(self, path: Path) -> ParsedDocument: ...


class AutoDocumentParser:
    """Use the primary parser only when the entire document succeeds."""

    def __init__(self, primary: DocumentParser, fallback: DocumentParser) -> None:
        self._primary = primary
        self._fallback = fallback

    def parse(self, path: Path) -> ParsedDocument:
        primary_available = self._primary.is_available()
        if primary_available:
            try:
                return self._primary.parse(path)
            except Exception:
                pass
        result = self._fallback.parse(path)
        reason = "failed" if primary_available else "unavailable"
        warning = ParseWarning(
            code="MINERU_DOCUMENT_FALLBACK",
            message=f"MinerU was {reason}; the complete document used PyMuPDF.",
        )
        return result.model_copy(update={"warnings": (warning, *result.warnings)})

"""PyMuPDF whole-document parser with physical-page provenance."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import re
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from industrial_energy_agent.rag.chunking import ChunkingConfig, chunk_page_text, normalize_text
from industrial_energy_agent.rag.document_parser import (
    DocumentChunk,
    DocumentParseError,
    PageExtractionStatus,
    PageStatus,
    ParsedDocument,
    ParserUnavailableError,
)

_SAFE_ID = re.compile(r"[^a-z0-9]+")
_GENERIC_TITLE_PREFIXES = ("microsoft word", "microsoft powerpoint", "untitled")
_ORGANIZATION_MARKERS = (
    "有限公司",
    "股份有限公司",
    "地址\N{FULLWIDTH COLON}",
    "电话\N{FULLWIDTH COLON}",
    "copyright",
    "©",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _document_id(path: Path, source_sha256: str) -> str:
    stem = _SAFE_ID.sub("-", path.stem.casefold()).strip("-")
    return stem or f"manual-{source_sha256[:12]}"


def _section_title(text: str) -> str | None:
    for line in normalize_text(text).splitlines():
        candidate = line.strip()
        if candidate and len(candidate) <= 120 and not candidate.isdecimal():
            return candidate
    return None


def _document_title(path: Path, metadata_title: str, first_page_text: str) -> str:
    normalized_metadata = metadata_title.strip()
    lowered = normalized_metadata.casefold()
    if (
        normalized_metadata
        and not lowered.startswith(_GENERIC_TITLE_PREFIXES)
        and not lowered.endswith((".doc", ".docx", ".pdf"))
        and lowered not in {path.name.casefold(), path.stem.casefold()}
    ):
        return normalized_metadata

    title_lines: list[str] = []
    for raw_line in normalize_text(first_page_text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if any(marker.casefold() in line.casefold() for marker in _ORGANIZATION_MARKERS):
            break
        if len(line) <= 120 and not line.isdecimal():
            title_lines.append(line)
        if len(title_lines) == 4:
            break
    derived = " ".join(title_lines).strip()
    return derived[:240] if derived else path.stem


def _page_limitations(page: Any, *, has_text: bool) -> tuple[tuple[str, ...], tuple[str, ...]]:
    limitations: list[str] = []
    warnings: list[str] = []
    try:
        has_images = bool(page.get_images(full=True))
    except Exception:
        has_images = False
        warnings.append("IMAGE_DETECTION_FAILED")
    if has_images:
        limitations.append("EMBEDDED_IMAGES_NOT_INTERPRETED")
    try:
        with redirect_stdout(io.StringIO()):
            has_tables = bool(page.find_tables().tables)
    except Exception:
        has_tables = False
        warnings.append("TABLE_DETECTION_FAILED")
    if has_tables:
        limitations.append("TABLE_STRUCTURE_NOT_PRESERVED")
    if not has_text:
        limitations.append("NO_EXTRACTABLE_TEXT")
    return tuple(limitations), tuple(warnings)


class PyMuPDFParser:
    """Extract all pages once and preserve explicit limitations."""

    name = "pymupdf"

    def __init__(
        self,
        *,
        chunking_config: ChunkingConfig | None = None,
        document_type: str = "operation_maintenance_manual",
        equipment_type: str = "centrifugal_pump",
    ) -> None:
        self._chunking_config = chunking_config or ChunkingConfig()
        self._document_type = document_type
        self._equipment_type = equipment_type

    def is_available(self) -> bool:
        return importlib.util.find_spec("pymupdf") is not None

    def parse(self, path: Path) -> ParsedDocument:
        if not self.is_available():
            raise ParserUnavailableError("PyMuPDF is unavailable")
        resolved = path.resolve()
        if not resolved.is_file() or resolved.suffix.casefold() != ".pdf":
            raise DocumentParseError("Parser input must be an existing PDF file")

        import pymupdf

        source_sha256 = _sha256_file(resolved)
        document_id = _document_id(resolved, source_sha256)
        pages: list[PageStatus] = []
        chunks: list[DocumentChunk] = []
        all_limitations: set[str] = set()
        try:
            with pymupdf.open(resolved) as document:  # type: ignore[no-untyped-call]
                if document.page_count < 1:
                    raise DocumentParseError("PDF has no physical pages")
                raw_title = str(document.metadata.get("title") or "").strip()
                first_page_text = str(document.load_page(0).get_text("text", sort=True))
                document_title = _document_title(resolved, raw_title, first_page_text)
                parser_version = str(pymupdf.__version__)
                for page_index in range(document.page_count):
                    page = document.load_page(page_index)
                    page_number = page_index + 1
                    text = str(page.get_text("text", sort=True))
                    normalized = normalize_text(text)
                    limitations, warnings = _page_limitations(page, has_text=bool(normalized))
                    all_limitations.update(limitations)
                    if normalized:
                        status: PageExtractionStatus = "extracted"
                    elif "EMBEDDED_IMAGES_NOT_INTERPRETED" in limitations:
                        status = "image_only"
                    else:
                        status = "blank"
                    pages.append(
                        PageStatus(
                            page_number=page_number,
                            status=status,
                            text_length=len(normalized),
                            limitations=limitations,
                            extraction_warnings=warnings,
                        )
                    )
                    section_title = _section_title(normalized)
                    for page_chunk in chunk_page_text(
                        normalized,
                        doc_id=document_id,
                        page_number=page_number,
                        config=self._chunking_config,
                    ):
                        chunks.append(
                            DocumentChunk(
                                text=page_chunk.text,
                                source_file=resolved.name,
                                document_title=document_title,
                                page_number=page_number,
                                page_start=page_number,
                                page_end=page_number,
                                section_title=section_title,
                                chunk_id=page_chunk.chunk_id,
                                document_type=self._document_type,
                                equipment_type=self._equipment_type,
                                parser_name=self.name,
                                parser_version=parser_version,
                                source_sha256=source_sha256,
                                limitations=limitations,
                                extraction_warnings=warnings,
                            )
                        )
                page_count = document.page_count
        except DocumentParseError:
            raise
        except Exception as error:
            raise DocumentParseError("PyMuPDF failed to parse the complete document") from error

        return ParsedDocument(
            source_file=resolved.name,
            document_title=document_title,
            document_id=document_id,
            source_sha256=source_sha256,
            parser_name=self.name,
            parser_version=parser_version,
            page_count=page_count,
            pages=tuple(pages),
            chunks=tuple(chunks),
            warnings=(),
            limitations=tuple(sorted(all_limitations)),
        )

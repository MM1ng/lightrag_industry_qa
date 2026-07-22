"""Simple PyMuPDF extraction with page-local chunks and stable provenance."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import pymupdf

_WHITESPACE = re.compile(r"[^\S\r\n]+")
_SAFE_ID = re.compile(r"[^a-z0-9]+")
_BOUNDARIES = ("\n\n", "\n", "。", "！", "？", ".", "!", "?")


@dataclass(frozen=True, slots=True)
class ParserConfig:
    max_characters: int = 1800
    overlap_characters: int = 180

    def __post_init__(self) -> None:
        if self.max_characters < 32:
            raise ValueError("max_characters must be at least 32")
        if not 0 <= self.overlap_characters < self.max_characters:
            raise ValueError("overlap_characters must be smaller than max_characters")


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    chunk_id: str
    text: str
    source_file: str
    page_number: int
    section_title: str | None

    def __post_init__(self) -> None:
        if not self.chunk_id or not self.text.strip() or not self.source_file:
            raise ValueError("chunk_id, text and source_file are required")
        if self.page_number < 1:
            raise ValueError("page_number must be one-based")

    def to_dict(self) -> dict[str, str | int | None]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> DocumentChunk:
        return cls(
            chunk_id=str(value["chunk_id"]),
            text=str(value["text"]),
            source_file=str(value["source_file"]),
            page_number=int(value["page_number"]),
            section_title=(
                str(value["section_title"]) if value.get("section_title") is not None else None
            ),
        )


def scan_pdf_files(manual_dir: Path) -> list[Path]:
    if not manual_dir.is_dir():
        raise FileNotFoundError(f"手册目录不存在: {manual_dir}")
    return sorted(
        (
            path
            for path in manual_dir.iterdir()
            if path.is_file() and path.suffix.casefold() == ".pdf"
        ),
        key=lambda path: path.name.casefold(),
    )


def _normalize_text(value: str) -> str:
    lines: list[str] = []
    previous_blank = False
    for raw_line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _WHITESPACE.sub(" ", raw_line).strip()
        if line:
            lines.append(line)
            previous_blank = False
        elif lines and not previous_blank:
            lines.append("")
            previous_blank = True
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _section_title(text: str) -> str | None:
    for line in text.splitlines():
        candidate = line.strip()
        if candidate and len(candidate) <= 120 and not candidate.isdecimal():
            return candidate
    return None


def _chunk_page(text: str, config: ParserConfig) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + config.max_characters, len(text))
        if end < len(text):
            minimum = start + config.max_characters // 2
            candidates = [text.rfind(boundary, minimum, end) for boundary in _BOUNDARIES]
            boundary = max(candidates)
            if boundary >= minimum:
                end = boundary + 1
        content = text[start:end].strip()
        if content:
            chunks.append(content)
        if end >= len(text):
            break
        start = max(start + 1, end - config.overlap_characters)
    return chunks


def parse_pdf(path: Path, config: ParserConfig | None = None) -> list[DocumentChunk]:
    if not path.is_file() or path.suffix.casefold() != ".pdf":
        raise ValueError(f"不是有效 PDF 文件: {path}")
    resolved_config = config or ParserConfig()
    safe_stem = _SAFE_ID.sub("-", path.stem.casefold()).strip("-") or "manual"
    chunks: list[DocumentChunk] = []
    with pymupdf.open(path) as document:
        for page_index in range(document.page_count):
            page_number = page_index + 1
            normalized = _normalize_text(document.load_page(page_index).get_text("text", sort=True))
            section = _section_title(normalized)
            for ordinal, text in enumerate(_chunk_page(normalized, resolved_config), start=1):
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
                chunks.append(
                    DocumentChunk(
                        chunk_id=f"{safe_stem}-p{page_number}-c{ordinal}-{digest}",
                        text=text,
                        source_file=path.name,
                        page_number=page_number,
                        section_title=section,
                    )
                )
    return chunks


def parse_manuals(
    manual_dir: Path,
    output_path: Path,
    config: ParserConfig | None = None,
) -> list[DocumentChunk]:
    pdf_files = scan_pdf_files(manual_dir)
    if not pdf_files:
        raise RuntimeError(f"未在 {manual_dir} 找到 PDF 手册")
    chunks = [chunk for path in pdf_files for chunk in parse_pdf(path, config)]
    if not chunks:
        raise RuntimeError("PDF 中未提取到可用文本")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(".tmp")
    temporary.write_text(
        "".join(
            json.dumps(chunk.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            for chunk in chunks
        ),
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(output_path)
    return chunks


def load_documents(path: Path) -> list[DocumentChunk]:
    if not path.is_file():
        raise FileNotFoundError(f"解析结果不存在: {path}")
    chunks: list[DocumentChunk] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            chunks.append(DocumentChunk.from_dict(value))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(f"documents.jsonl 第 {line_number} 行无效") from error
    if not chunks:
        raise ValueError("documents.jsonl 不包含文档块")
    return chunks

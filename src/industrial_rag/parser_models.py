"""Unified parser-agnostic block model for Parent-Child chunking.

Every parser (PyMuPDF, MinerU, future) produces these blocks, which the
structured chunker then consumes to build ParentChunk / ChildChunk trees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class BlockType(Enum):
    """Semantic block classification."""

    heading = "heading"
    paragraph = "paragraph"
    list_item = "list_item"
    table = "table"
    table_row = "table_row"
    table_cell = "table_cell"
    image = "image"
    warning = "warning"
    note = "note"
    code = "code"
    formula = "formula"
    page_header = "page_header"
    page_footer = "page_footer"
    unknown = "unknown"


class WarningType(Enum):
    pressure = "pressure"
    electrical = "electrical"
    mechanical = "mechanical"
    chemical = "chemical"
    thermal = "thermal"
    general = "general"


class ContentType(Enum):
    """Classification used by the chunker to decide splitting rules."""

    normal_text = "normal_text"
    operation_steps = "operation_steps"
    fault_diagnosis = "fault_diagnosis"
    parameter_table = "parameter_table"
    safety_warning = "safety_warning"
    table_of_contents = "table_of_contents"
    section_heading = "section_heading"
    image_caption = "image_caption"
    appendix = "appendix"
    unknown = "unknown"


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    """One atom of parsed content from any parser.

    This is the *universal intermediate representation* — PyMuPDF pages,
    MinerU markdown blocks, and any future parser all produce lists of
    these.  The structured chunker then groups them.
    """

    block_id: str
    block_type: BlockType = BlockType.paragraph
    text: str = ""
    page_number: int | None = None
    section_path: tuple[str, ...] = ()
    section_title: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    content_type: ContentType = ContentType.normal_text
    token_count: int = 0

    # Warning-specific
    risk_level: str | None = None
    warning_type: str | None = None

    # Table-specific
    table_headers: tuple[str, ...] = ()
    table_data: tuple[dict[str, str], ...] = ()
    table_caption: str | None = None

    # Image-specific
    image_ref: str | None = None

    # Provenance
    parser: str = "unknown"
    parser_version: str | None = None
    source_file: str = ""
    source_page: int | None = None

    # Arbitrary extra data (parser-specific)
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        from dataclasses import asdict

        return asdict(self)


# ---------------------------------------------------------------------------
# Parent-Child chunk models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParentChunk:
    """Large semantic unit (section, fault group, table group).

    NOT embedded.  Serves as context expansion when a child is hit.
    """

    parent_chunk_id: str
    document_id: str
    document_name: str
    document_version: str = "1"
    page_start: int | None = None
    page_end: int | None = None
    section_path: tuple[str, ...] = ()
    section_title: str | None = None
    content_type: ContentType = ContentType.normal_text
    content: str = ""
    token_count: int = 0
    source_hash: str = ""
    parser: str = "unknown"
    parser_version: str | None = None
    child_chunk_ids: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChildChunk:
    """The unit sent to LightRAG for embedding + entity extraction.

    Exactly one child = one tracked LightRAG chunk (no silent fission).
    """

    chunk_id: str
    parent_chunk_id: str
    document_id: str
    document_name: str
    document_version: str = "1"
    page_start: int | None = None
    page_end: int | None = None
    section_path: tuple[str, ...] = ()
    section_title: str | None = None
    content_type: ContentType = ContentType.normal_text
    content: str = ""                          # user-visible text
    embedding_content: str = ""                # text sent to embedding (may include heading breadcrumbs)
    token_count: int = 0
    source_hash: str = ""
    parent_source_hash: str = ""
    parser: str = "unknown"
    parser_version: str | None = None
    chunking_strategy: str = ""
    chunking_version: str = "1"
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        from dataclasses import asdict

        d = asdict(self)
        d["section_path"] = list(self.section_path)
        # Serialize enum members (e.g. ContentType) as their string values so
        # json.dumps(default=str) round-trips via from_dict/load_child_chunks.
        for key, value in d.items():
            if hasattr(value, "value"):
                d[key] = value.value
        return d

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> ChildChunk:
        sp = value.get("section_path", ())
        if isinstance(sp, list):
            sp = tuple(sp)
        return cls(
            chunk_id=str(value["chunk_id"]),
            parent_chunk_id=str(value["parent_chunk_id"]),
            document_id=str(value["document_id"]),
            document_name=str(value["document_name"]),
            document_version=str(value.get("document_version", "1")),
            page_start=int(value["page_start"]) if value.get("page_start") is not None else None,
            page_end=int(value["page_end"]) if value.get("page_end") is not None else None,
            section_path=sp,
            section_title=str(value["section_title"]) if value.get("section_title") else None,
            content_type=ContentType(str(value.get("content_type", "normal_text"))),
            content=str(value.get("content", "")),
            embedding_content=str(value.get("embedding_content", "")),
            token_count=int(value.get("token_count", 0)),
            source_hash=str(value.get("source_hash", "")),
            parent_source_hash=str(value.get("parent_source_hash", "")),
            parser=str(value.get("parser", "unknown")),
            parser_version=str(value["parser_version"]) if value.get("parser_version") else None,
            chunking_strategy=str(value.get("chunking_strategy", "")),
            chunking_version=str(value.get("chunking_version", "1")),
        )

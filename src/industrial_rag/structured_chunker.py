"""Structured chunker: groups ParsedBlocks into ParentChunk / ChildChunk trees.

Strategy: ``"pymupdf-v1"`` (based on current PyMuPDF parser output).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from lightrag.utils import TiktokenTokenizer  # tiktoken-backed (cl100k_base)

from industrial_rag.parser_models import (
    BlockType,
    ChildChunk,
    ContentType,
    ParentChunk,
    ParsedBlock,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChunkerConfig:
    parent_target_tokens: int = 1500
    parent_max_tokens: int = 2200
    child_target_tokens: int = 450
    child_min_tokens: int = 120
    child_max_tokens: int = 700
    child_overlap_tokens: int = 80
    merge_small_children: bool = True
    strategy: str = "pymupdf-v1"
    version: str = "0.1.0"

    # Heading breadcrumbs
    embedding_heading_depth: int = 3  # max heading levels to prepend
    heading_separator: str = " > "


# ---------------------------------------------------------------------------
# Tokenizer (lazy singleton)
# ---------------------------------------------------------------------------

_tokenizer: TiktokenTokenizer | None = None


def _get_tokenizer() -> TiktokenTokenizer:
    global _tokenizer
    if _tokenizer is None:
        _tokenizer = TiktokenTokenizer("gpt-4o-mini")
    return _tokenizer


def count_tokens(text: str) -> int:
    return len(_get_tokenizer().encode(text))


# ---------------------------------------------------------------------------
# ID generation — deterministic, content-based
# ---------------------------------------------------------------------------


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _normalized_text_for_id(text: str) -> str:
    """Collapse whitespace for deterministic ID generation."""
    import re

    return re.sub(r"\s+", " ", text).strip()


def make_parent_chunk_id(
    document_id: str,
    section_path: tuple[str, ...],
    page_start: int | None,
    page_end: int | None,
    content: str,
    *,
    strategy: str = "pymupdf-v1",
    version: str = "0.1.0",
) -> str:
    """Deterministic parent chunk ID."""
    norm = _normalized_text_for_id(content)
    h = _content_hash(norm)
    sp = "-".join(s.replace(" ", "_")[:20] for s in section_path[:3]) or "root"
    pages = f"p{page_start or 0}-{page_end or 0}"
    return f"pchunk-{strategy}-{document_id[:12]}-{pages}-{sp}-{h}"


def make_child_chunk_id(
    parent_chunk_id: str,
    ordinal: int,
    content: str,
    *,
    strategy: str = "pymupdf-v1",
    version: str = "0.1.0",
) -> str:
    """Deterministic child chunk ID."""
    norm = _normalized_text_for_id(content)
    h = _content_hash(norm)
    return f"cchunk-{strategy}-{parent_chunk_id[-16:]}-{ordinal:03d}-{h}"


def make_document_id(source_file: str) -> str:
    """Deterministic document ID from file name."""
    h = _content_hash(source_file.casefold())
    return f"doc-{h}"


def make_document_version(source_file: str) -> str:
    return "1"


# ---------------------------------------------------------------------------
# Helper: build section_path from heading blocks
# ---------------------------------------------------------------------------


def _build_section_path(
    current_headings: list[tuple[int, str]],
    max_depth: int = 5,
) -> tuple[str, ...]:
    if not current_headings:
        return ()
    return tuple(title for _, title in current_headings[:max_depth])


def _build_heading_breadcrumb(
    section_path: tuple[str, ...],
    *,
    max_depth: int = 3,
    separator: str = " > ",
) -> str:
    if not section_path:
        return ""
    return separator.join(section_path[-max_depth:])


# ---------------------------------------------------------------------------
# Content type detection
# ---------------------------------------------------------------------------


_WARNING_KEYWORDS = frozenset(
    {
        "警告", "小心", "危险", "注意安全", "禁止", "必须佩戴",
        "warning", "caution", "danger",
    }
)
_FAULT_KEYWORDS = frozenset(
    {
        "故障", "原因", "排除", "诊断", "处理方法", "可能原因", "检查方法",
        "troubleshooting", "fault",
    }
)
_STEP_KEYWORDS = frozenset(
    {
        "步骤", "操作步骤", "拆卸步骤", "安装步骤", "装配步骤",
        "procedure", "step",
    }
)
_TABLE_LABELS = frozenset(
    {
        "型号", "规格", "参数", "尺寸", "额定", "流量", "扬程",
        "specification", "dimension", "parameter",
    }
)

_BLOCK_ORDER_SCORE: dict[BlockType, int] = {
    BlockType.heading: 0,
    BlockType.warning: 1,
    BlockType.paragraph: 2,
    BlockType.list_item: 3,
    BlockType.table: 4,
    BlockType.table_row: 5,
    BlockType.note: 6,
    BlockType.formula: 7,
    BlockType.image: 8,
    BlockType.page_header: -1,
    BlockType.page_footer: -1,
    BlockType.unknown: 99,
}


def _score_block_order(bt: BlockType) -> int:
    return _BLOCK_ORDER_SCORE.get(bt, 99)


def _detect_content_type(blocks: Sequence[ParsedBlock]) -> ContentType:
    """Heuristic content-type detection from a sequence of blocks."""
    text = " ".join(b.text for b in blocks).casefold()
    block_types = {b.block_type for b in blocks}
    if BlockType.heading in block_types and len(blocks) <= 3:
        return ContentType.section_heading
    if BlockType.warning in block_types or any(
        kw in text for kw in _WARNING_KEYWORDS
    ):
        return ContentType.safety_warning
    if BlockType.table in block_types or any(
        kw in text for kw in _TABLE_LABELS
    ):
        return ContentType.parameter_table
    if any(kw in text for kw in _FAULT_KEYWORDS):
        return ContentType.fault_diagnosis
    if any(kw in text for kw in _STEP_KEYWORDS):
        return ContentType.operation_steps
    return ContentType.normal_text


# ---------------------------------------------------------------------------
# Splitting logic
# ---------------------------------------------------------------------------


def _sentences(text: str) -> list[str]:
    """Split text into sentence-ish segments at natural boundaries."""
    import re

    pattern = re.compile(
        r"(?<=[。！？.!?\n])\s*"
    )
    parts = pattern.split(text)
    return [p.strip() for p in parts if p.strip()]


def _split_long_text(
    text: str,
    target_tokens: int,
    max_tokens: int,
    *,
    overlap_tokens: int = 80,
) -> list[str]:
    """Split a single long paragraph into child-sized pieces at sentence boundaries."""
    tokens = _get_tokenizer().encode(text)
    if len(tokens) <= max_tokens:
        return [text]

    sentences_list = _sentences(text)
    if len(sentences_list) <= 1:
        # Can't split nicely — force-chunk by token window
        chunks: list[str] = []
        for start in range(0, len(tokens), target_tokens - overlap_tokens):
            end = min(start + target_tokens, len(tokens))
            chunks.append(_get_tokenizer().decode(tokens[start:end]))
        return chunks

    # Greedy sentence packing
    result: list[str] = []
    buf: list[str] = []
    buf_tokens = 0
    for sent in sentences_list:
        st = count_tokens(sent)
        if buf_tokens + st > target_tokens and buf:
            result.append(" ".join(buf))
            # carry overlap: keep last sentence as context
            if overlap_tokens > 0 and len(buf) >= 1:
                last = buf[-1]
                lt = count_tokens(last)
                buf = [last] if lt < overlap_tokens * 2 else []
                buf_tokens = sum(count_tokens(s) for s in buf)
            else:
                buf = []
                buf_tokens = 0
        buf.append(sent)
        buf_tokens += st
    if buf:
        result.append(" ".join(buf))
    return result


# ---------------------------------------------------------------------------
# Main chunker entry point
# ---------------------------------------------------------------------------


def build_parent_child_chunks(
    blocks: Sequence[ParsedBlock],
    source_file: str,
    *,
    config: ChunkerConfig | None = None,
) -> tuple[list[ParentChunk], list[ChildChunk]]:
    """Group ParsedBlocks into Parent-Chunk / Child-Chunk trees.

    Strategy:
    1. Group contiguous blocks under the same parent (topic/heading).
    2. Split each group's text into child-sized pieces.
    3. Assign ``ParentChunk`` wrapping the full parent text.
    4. Each piece becomes a ``ChildChunk``.
    """
    cfg = config or ChunkerConfig()
    doc_id = make_document_id(source_file)
    doc_ver = make_document_version(source_file)

    # 1. Sort blocks by (page, position)
    sorted_blocks = sorted(
        blocks,
        key=lambda b: (
            b.page_number or 0,
            b.bbox[1] if b.bbox else 0,
            b.bbox[0] if b.bbox else 0,
            _score_block_order(b.block_type),
        ),
    )

    # 2. Group into parent-level segments (split at H1/H2 boundaries)
    heading_stack: list[tuple[int, str]] = []
    groups: list[list[ParsedBlock]] = []
    current_group: list[ParsedBlock] = []

    for block in sorted_blocks:
        if block.block_type == BlockType.page_header or block.block_type == BlockType.page_footer:
            continue  # skip boilerplate

        if block.block_type == BlockType.heading:
            # Determine heading level from text heuristics
            level = _guess_heading_level(block.text)
            # Pop deeper headings off stack
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, block.text[:80].strip()))
            # Flush previous group
            if current_group:
                groups.append(current_group)
                current_group = []
        current_group.append(block)
    if current_group:
        groups.append(current_group)

    # 3. For each group, build parent + children
    parents: list[ParentChunk] = []
    children: list[ChildChunk] = []

    for group_idx, group in enumerate(groups):
        if not group:
            continue
        content_type = _detect_content_type(group)
        # Build section path from headings seen so far
        # (simplified: use block-level section_path if available)
        section_path = group[0].section_path or ()
        section_title = group[0].section_title

        # Collect text from all blocks
        group_text_parts: list[str] = []
        page_start = None
        page_end = None
        for b in group:
            if b.text.strip():
                group_text_parts.append(b.text.strip())
            if b.page_number is not None:
                if page_start is None:
                    page_start = b.page_number
                page_end = b.page_number

        full_text = "\n\n".join(group_text_parts)
        if not full_text.strip():
            continue

        parent_tokens = count_tokens(full_text)
        parent_id = make_parent_chunk_id(
            doc_id, section_path, page_start, page_end,
            full_text, strategy=cfg.strategy, version=cfg.version,
        )
        parent_hash = _content_hash(full_text)

        parent = ParentChunk(
            parent_chunk_id=parent_id,
            document_id=doc_id,
            document_name=source_file,
            document_version=doc_ver,
            page_start=page_start,
            page_end=page_end,
            section_path=section_path,
            section_title=section_title,
            content_type=content_type,
            content=full_text,
            token_count=parent_tokens,
            source_hash=parent_hash,
            parser="PyMuPDF",
            parser_version="1.28.0",
            child_chunk_ids=(),
            metadata={"group_index": group_idx},
        )

        # Split into child-sized pieces
        child_pieces = _split_long_text(
            full_text,
            target_tokens=cfg.child_target_tokens,
            max_tokens=cfg.child_max_tokens,
            overlap_tokens=cfg.child_overlap_tokens,
        )
        child_ids: list[str] = []
        for child_ord, piece in enumerate(child_pieces):
            pt = count_tokens(piece)
            # Build embedding text: heading breadcrumb + content
            breadcrumb = _build_heading_breadcrumb(
                section_path,
                max_depth=cfg.embedding_heading_depth,
                separator=cfg.heading_separator,
            )
            emb_content = f"{breadcrumb}\n{piece}" if breadcrumb else piece
            (
                int(parent.page_start) if parent.page_start is not None else 1
            )

            child_id = make_child_chunk_id(
                parent_id, child_ord, piece,
                strategy=cfg.strategy, version=cfg.version,
            )
            child_ids.append(child_id)

            child = ChildChunk(
                chunk_id=child_id,
                parent_chunk_id=parent_id,
                document_id=doc_id,
                document_name=source_file,
                document_version=doc_ver,
                page_start=page_start,
                page_end=page_end,
                section_path=section_path,
                section_title=section_title,
                content_type=content_type,
                content=piece,
                embedding_content=emb_content,
                token_count=pt,
                source_hash=_content_hash(piece),
                parent_source_hash=parent_hash,
                parser="PyMuPDF",
                parser_version="1.28.0",
                chunking_strategy=cfg.strategy,
                chunking_version=cfg.version,
            )
            children.append(child)

        # Update parent with child IDs
        parents.append(
            ParentChunk(
                parent_chunk_id=parent.parent_chunk_id,
                document_id=parent.document_id,
                document_name=parent.document_name,
                document_version=parent.document_version,
                page_start=parent.page_start,
                page_end=parent.page_end,
                section_path=parent.section_path,
                section_title=parent.section_title,
                content_type=parent.content_type,
                content=parent.content,
                token_count=parent.token_count,
                source_hash=parent.source_hash,
                parser=parent.parser,
                parser_version=parent.parser_version,
                child_chunk_ids=tuple(child_ids),
                metadata=parent.metadata,
            )
        )

    return parents, children


def _guess_heading_level(text: str) -> int:
    """Heuristic: count leading digits/dots or estimate from content."""
    import re

    cleaned = text.strip()
    # "第5章" or "第 5 章" style
    m = re.match(r"第\s*(\d+)\s*[章节篇]", cleaned)
    if m:
        return min(int(m.group(1)), 6)
    m = re.match(r"^(\d+)[\.\s)]", cleaned)
    if m:
        return min(int(m.group(1)), 6)
    if m := re.match(r"^([一二三四五六七八九十]+)[、.]", cleaned):
        digits = {
            "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
        }
        level = digits.get(m.group(1), 1)
        return min(level, 6)
    # Appendix / letters
    if re.match(r"^附录\s*[A-Z]", cleaned):
        return 2
    return 1  # default top-level


# ---------------------------------------------------------------------------
# PyMuPDF → ParsedBlocks converter
# ---------------------------------------------------------------------------


def pymupdf_chunks_to_blocks(
    chunks: Sequence[Any],  # DocumentChunk
    source_file: str,
) -> list[ParsedBlock]:
    """Convert existing DocumentChunk objects into ParsedBlocks.

    Each DocumentChunk becomes one or more ParsedBlocks after light
    paragraph-level splitting.
    """
    blocks: list[ParsedBlock] = []
    for idx, chunk in enumerate(chunks):
        text = chunk.text
        page = chunk.page_number
        section = chunk.section_title
        sp = (section,) if section else ()

        # Simple paragraph detection: split on double-newline
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [text]

        for para_idx, para in enumerate(paragraphs):
            bt = BlockType.paragraph
            ct = ContentType.normal_text

            # Heading heuristic
            stripped = para.strip()
            if len(stripped) <= 120 and (
                stripped.isupper()
                or any(
                    kw in stripped
                    for kw in [
                        "章", "节", "附录", "简介", "安全", "目录",
                        "安装", "操作", "维护", "保养", "故障",
                    ]
                )
            ):
                bt = BlockType.heading
                ct = ContentType.section_heading

            # Warning
            if any(w in stripped for w in _WARNING_KEYWORDS):
                bt = BlockType.warning
                ct = ContentType.safety_warning

            blocks.append(
                ParsedBlock(
                    block_id=f"{chunk.chunk_id}-p{para_idx}",
                    block_type=bt,
                    text=stripped,
                    page_number=page,
                    section_path=sp,
                    section_title=section,
                    content_type=ct,
                    token_count=count_tokens(stripped),
                    parser="PyMuPDF",
                    parser_version="1.28.0",
                    source_file=source_file,
                    source_page=page,
                    metadata={"source_chunk_id": chunk.chunk_id},
                )
            )
    return blocks

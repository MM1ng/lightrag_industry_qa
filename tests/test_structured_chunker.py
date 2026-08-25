"""Unit tests for structured chunker (Parent-Child generation)."""

from __future__ import annotations

import pytest
from industrial_rag.parser_models import (
    BlockType,
    ContentType,
    ParsedBlock,
)
from industrial_rag.structured_chunker import (
    _detect_content_type,
    _guess_heading_level,
    _split_long_text,
    build_parent_child_chunks,
    count_tokens,
    make_child_chunk_id,
    make_document_id,
    make_parent_chunk_id,
    pymupdf_chunks_to_blocks,
)

# ---------------------------------------------------------------------------
# ID determinism
# ---------------------------------------------------------------------------


def test_parent_chunk_id_deterministic() -> None:
    id1 = make_parent_chunk_id(
        "doc-abc123",
        ("第1章", "安装"),
        5,
        7,
        "泵的安装要求包括：地基应平整牢固。",
    )
    id2 = make_parent_chunk_id(
        "doc-abc123",
        ("第1章", "安装"),
        5,
        7,
        "泵的安装要求包括：地基应平整牢固。",
    )
    assert id1 == id2


def test_parent_chunk_id_changes_with_content() -> None:
    id1 = make_parent_chunk_id("doc-abc123", (), 1, 1, "content A")
    id2 = make_parent_chunk_id("doc-abc123", (), 1, 1, "content B")
    assert id1 != id2


def test_child_chunk_id_deterministic() -> None:
    c1 = make_child_chunk_id("pchunk-test-abc123", 0, "启动前检查阀门。")
    c2 = make_child_chunk_id("pchunk-test-abc123", 0, "启动前检查阀门。")
    assert c1 == c2


def test_child_chunk_id_differs_by_ordinal() -> None:
    c1 = make_child_chunk_id("pchunk-test-abc123", 0, "same content")
    c2 = make_child_chunk_id("pchunk-test-abc123", 1, "same content")
    assert c1 != c2


def test_child_chunk_id_differs_when_parent_position_differs() -> None:
    c1 = make_child_chunk_id("pchunk-doc-p1-1", 0, "same content")
    c2 = make_child_chunk_id("pchunk-doc-p2-2", 0, "same content")
    assert c1 != c2


def test_document_id_deterministic() -> None:
    id1 = make_document_id("2196-ANSI-Manual-Chinese.pdf")
    id2 = make_document_id("2196-ANSI-Manual-Chinese.pdf")
    assert id1 == id2


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------


def test_count_tokens_returns_int() -> None:
    n = count_tokens("Hello world")
    assert isinstance(n, int)
    assert n > 0


def test_count_tokens_chinese() -> None:
    n = count_tokens("离心泵启动前需要检查阀门状态。")
    assert n > 5


# ---------------------------------------------------------------------------
# Heading level heuristics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 简介", 1),
        ("3.2 安装", 3),
        ("附录 A - 叶轮间隙设置", 2),
        ("安全", 1),
        ("第5章 故障排除", 5),
    ],
)
def test_guess_heading_level(text: str, expected: int) -> None:
    assert _guess_heading_level(text) == expected


# ---------------------------------------------------------------------------
# Content type detection
# ---------------------------------------------------------------------------


def test_detect_safety_warning() -> None:
    blocks = [
        ParsedBlock(
            block_id="w1",
            block_type=BlockType.warning,
            text="警告！未遵循本手册中的警告事项会导致人员受伤或死亡。",
            page_number=7,
        )
    ]
    assert _detect_content_type(blocks) == ContentType.safety_warning


def test_detect_fault_diagnosis() -> None:
    blocks = [
        ParsedBlock(
            block_id="f1",
            block_type=BlockType.paragraph,
            text="故障现象：泵不出水。可能原因：吸入管路漏气。排查方法：检查管路连接。",
            page_number=17,
        )
    ]
    assert _detect_content_type(blocks) == ContentType.fault_diagnosis


def test_detect_parameter_table() -> None:
    blocks = [
        ParsedBlock(
            block_id="t1",
            block_type=BlockType.table,
            text="型号 | 额定功率 | 额定流量 | 额定扬程\n2196-STO | 15 kW | 100 m³/h",
            page_number=25,
        )
    ]
    assert _detect_content_type(blocks) == ContentType.parameter_table


# ---------------------------------------------------------------------------
# Split long text
# ---------------------------------------------------------------------------


def test_split_short_text_stays_intact() -> None:
    text = "轴承温度过高时应检查润滑状态。"
    pieces = _split_long_text(text, target_tokens=450, max_tokens=700)
    assert len(pieces) == 1
    assert pieces[0] == text


def test_split_long_text_produces_multiple_pieces() -> None:
    # Generate enough text to exceed 700 tokens
    sentences = [f"这是第{i}句测试文本。" for i in range(200)]
    text = " ".join(sentences)
    pieces = _split_long_text(text, target_tokens=450, max_tokens=700)
    assert len(pieces) >= 2
    for piece in pieces:
        assert count_tokens(piece) <= 720  # ~max_tokens + small slop


# ---------------------------------------------------------------------------
# Parent-Child generation
# ---------------------------------------------------------------------------


def test_build_parent_child_produces_no_orphans() -> None:
    blocks = [
        ParsedBlock(
            block_id="b1",
            block_type=BlockType.heading,
            text="1 启动检查",
            page_number=1,
        ),
        ParsedBlock(
            block_id="b2",
            block_type=BlockType.paragraph,
            text="启动前应检查阀门状态、润滑情况和联轴器对中。确认所有安全装置已就位。",
            page_number=1,
        ),
        ParsedBlock(
            block_id="b3",
            block_type=BlockType.heading,
            text="2 停机操作",
            page_number=2,
        ),
        ParsedBlock(
            block_id="b4",
            block_type=BlockType.paragraph,
            text="停机时应首先关闭出口阀门，然后切断电源。等待泵完全停止后再进行维护。",
            page_number=2,
        ),
    ]

    parents, children = build_parent_child_chunks(blocks, "test-manual.pdf")
    assert len(parents) >= 1
    assert len(children) >= 1

    # Every child has a valid parent
    parent_ids = {p.parent_chunk_id for p in parents}
    for child in children:
        assert child.parent_chunk_id in parent_ids, (
            f"Child {child.chunk_id} has orphan parent {child.parent_chunk_id}"
        )

    # Every parent has at least one child
    child_ids_by_parent: dict[str, list[str]] = {}
    for child in children:
        child_ids_by_parent.setdefault(child.parent_chunk_id, []).append(child.chunk_id)
    for parent in parents:
        assert len(child_ids_by_parent.get(parent.parent_chunk_id, [])) > 0, (
            f"Parent {parent.parent_chunk_id} has no children"
        )


def test_page_numbers_preserved() -> None:
    blocks = [
        ParsedBlock(
            block_id="b1",
            block_type=BlockType.paragraph,
            text="第3页的内容。",
            page_number=3,
        ),
        ParsedBlock(
            block_id="b2",
            block_type=BlockType.paragraph,
            text="还是第3页。",
            page_number=3,
        ),
    ]
    parents, children = build_parent_child_chunks(blocks, "pagetest.pdf")
    for parent in parents:
        assert parent.page_start == 3
        assert parent.page_end == 3
    for child in children:
        assert child.page_start == 3


def test_id_stable_across_runs() -> None:
    """Same input → same parent/child IDs."""
    blocks = [
        ParsedBlock(
            block_id="b1",
            block_type=BlockType.heading,
            text="故障诊断",
            page_number=10,
        ),
        ParsedBlock(
            block_id="b2",
            block_type=BlockType.paragraph,
            text="泵不出水的原因：1.吸入管路漏气 2.叶轮堵塞 3.电机反转。检查方法：逐项排查。",
            page_number=10,
        ),
    ]

    parents1, children1 = build_parent_child_chunks(blocks, "test.pdf")
    parents2, children2 = build_parent_child_chunks(blocks, "test.pdf")

    assert len(parents1) == len(parents2)
    for p1, p2 in zip(parents1, parents2, strict=False):
        assert p1.parent_chunk_id == p2.parent_chunk_id
    for c1, c2 in zip(children1, children2, strict=False):
        assert c1.chunk_id == c2.chunk_id


def test_document_name_in_every_chunk() -> None:
    blocks = [
        ParsedBlock(
            block_id="b1",
            block_type=BlockType.paragraph,
            text="内容。",
            page_number=1,
        ),
    ]
    _, children = build_parent_child_chunks(blocks, "my-manual.pdf")
    for child in children:
        assert child.document_name == "my-manual.pdf"


# ---------------------------------------------------------------------------
# PyMuPDF → Blocks adapter
# ---------------------------------------------------------------------------


class _FakeDocChunk:
    def __init__(self, chunk_id: str, text: str, source_file: str, page: int, section: str | None = None) -> None:
        self.chunk_id = chunk_id
        self.text = text
        self.source_file = source_file
        self.page_number = page
        self.section_title = section


def test_pymupdf_adapter_produces_valid_blocks() -> None:
    chunks = [
        _FakeDocChunk("c1", "警告：高压危险\n\n维修前必须断电。", "manual.pdf", 1, "安全"),
    ]
    blocks = pymupdf_chunks_to_blocks(chunks, "manual.pdf")
    assert len(blocks) >= 1
    for b in blocks:
        assert b.parser == "PyMuPDF"
        assert b.source_file == "manual.pdf"
        assert b.metadata.get("source_chunk_id") is not None


def test_pymupdf_adapter_heading_detection() -> None:
    chunks = [
        _FakeDocChunk("c1", "附录 C - 保养及维修", "manual.pdf", 19, None),
    ]
    blocks = pymupdf_chunks_to_blocks(chunks, "manual.pdf")
    assert any(b.block_type == BlockType.heading for b in blocks)

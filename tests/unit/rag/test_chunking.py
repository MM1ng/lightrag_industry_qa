from __future__ import annotations

from industrial_energy_agent.rag.chunking import ChunkingConfig, chunk_page_text, normalize_text


def test_normalize_text_is_deterministic_and_preserves_paragraph_boundaries() -> None:
    raw = "  第一行  内容\r\n第二行\t内容\r\n\r\n  新段落  "

    normalized = normalize_text(raw)

    assert normalized == "第一行 内容\n第二行 内容\n\n新段落"
    assert normalize_text(normalized) == normalized


def test_chunks_never_cross_physical_pages_and_have_stable_ids() -> None:
    config = ChunkingConfig(max_characters=24, overlap_characters=4)

    page_one = chunk_page_text(
        "第一段内容足够长,需要被切分。第二句继续提供上下文。",
        doc_id="manual-test",
        page_number=1,
        config=config,
    )
    page_two = chunk_page_text(
        "第二页只有自己的文本。",
        doc_id="manual-test",
        page_number=2,
        config=config,
    )

    assert len(page_one) > 1
    assert {chunk.page_number for chunk in page_one} == {1}
    assert {chunk.page_number for chunk in page_two} == {2}
    assert page_one == chunk_page_text(
        "第一段内容足够长,需要被切分。第二句继续提供上下文。",
        doc_id="manual-test",
        page_number=1,
        config=config,
    )
    assert page_one[0].chunk_id.startswith("manual-test:p1:c1:")


def test_blank_page_produces_no_chunks() -> None:
    assert chunk_page_text(" \n\t ", doc_id="manual-test", page_number=3) == ()

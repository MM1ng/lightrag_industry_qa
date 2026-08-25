from __future__ import annotations

import pytest
from industrial_rag.citation_formatter import (
    Citation,
    collect_citations,
    decode_source_ref,
    encode_chunk_header,
    encode_source_ref,
    format_citation,
    is_provenance_only_fragment,
    strip_provenance_metadata,
)


def test_source_reference_round_trip_and_format() -> None:
    citation = Citation(source_file="泵 手册.pdf", page_number=12, chunk_id="manual:p12:c2")

    decoded = decode_source_ref(encode_source_ref(citation))

    assert decoded == citation
    assert format_citation(decoded) == "[泵 手册.pdf，第12页]"


def test_collect_citations_uses_structured_metadata_and_deduplicates() -> None:
    first = encode_source_ref(Citation("manual-a.pdf", 3, "a-3"))
    duplicate = encode_source_ref(Citation("manual-a.pdf", 3, "a-3-other"))
    second = encode_source_ref(Citation("manual-b.pdf", 8, "b-8"))
    payload = {
        "data": {
            "references": [{"file_path": first}, {"file_path": duplicate}],
            "chunks": [{"file_path": second}, {"file_path": "untrusted free text"}],
        }
    }

    citations = collect_citations(payload)

    assert [(item.source_file, item.page_number) for item in citations] == [
        ("manual-a.pdf", 3),
        ("manual-b.pdf", 8),
    ]


def test_collect_citations_reads_parser_owned_header_from_chunk_content() -> None:
    citation = Citation("manual.pdf", 9, "manual-p9-c1")
    payload = {
        "data": {
            "references": [{"file_path": "manual.pdf"}],
            "chunks": [
                {
                    "file_path": "manual.pdf",
                    "content": f"{encode_chunk_header(citation)}\n泵启动前检查阀门。",
                }
            ],
        }
    }

    assert collect_citations(payload) == (citation,)


@pytest.mark.parametrize("value", ["manual.pdf::page=0::chunk=x", "manual.pdf", ""])
def test_decode_source_ref_rejects_untrusted_or_invalid_pages(value: str) -> None:
    with pytest.raises(ValueError):
        decode_source_ref(value)


@pytest.mark.parametrize(
    "value",
    [
        "证据来源：",
        "（证据来源：2196-ANSI-Manual-Chinese.pdf，第9页）",
        "(依据来源: manual.pdf page=9 chunk=c1)",
        "[[INDUSTRIAL_RAG_SOURCE file=manual.pdf page=9 chunk=c1]]",
        "file=manual.pdf page=9 chunk=c1",
    ],
)
def test_provenance_only_fragments_are_metadata(value: str) -> None:
    assert is_provenance_only_fragment(value)


def test_factual_sentence_with_page_reference_is_not_provenance_only() -> None:
    value = "根据手册第11页，平行和角度对正误差应控制在0.005英寸以内。"

    assert not is_provenance_only_fragment(value)
    assert strip_provenance_metadata(value) == value


def test_internal_source_marker_is_removed_without_removing_fact() -> None:
    value = "泵轴至少每周旋转一次。[[INDUSTRIAL_RAG_SOURCE file=manual.pdf page=9 chunk=c1]]"

    assert strip_provenance_metadata(value) == "泵轴至少每周旋转一次。"

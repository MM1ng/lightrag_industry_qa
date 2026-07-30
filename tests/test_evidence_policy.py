from __future__ import annotations

import pytest
from industrial_rag.citation_formatter import Citation, encode_chunk_header, encode_source_ref
from industrial_rag.evidence_policy import EvidenceDecision, select_evidence

SUMMIT_MANUAL = "2196-ANSI-Manual-Chinese.pdf"
DESMI_MANUAL = "t1739cn.pdf"


def _path_candidate(source_file: str, page: int, chunk_id: str, text: str) -> dict[str, str]:
    citation = Citation(source_file, page, chunk_id)
    return {"file_path": encode_source_ref(citation), "content": text}


def _payload(*chunks: dict[str, str]) -> dict[str, object]:
    return {"data": {"references": [], "chunks": list(chunks)}}


def _header_candidate(
    source_file: str,
    page: int,
    chunk_id: str,
    text: str,
) -> dict[str, str]:
    citation = Citation(source_file, page, chunk_id)
    return {
        "file_path": "untrusted raw path",
        "content": f"{encode_chunk_header(citation)}\n{text}",
    }


def test_unique_summit_alias_routes_and_returns_only_three_best_chunks() -> None:
    chunks = (
        _path_candidate(SUMMIT_MANUAL, 1, "summit-1", "SUMMIT 2196 长期 存放 要求"),
        _path_candidate(SUMMIT_MANUAL, 2, "summit-2", "SUMMIT 2196 长期 存放"),
        _path_candidate(SUMMIT_MANUAL, 3, "summit-3", "SUMMIT 2196 存放 要求"),
        _path_candidate(SUMMIT_MANUAL, 4, "summit-4", "SUMMIT 2196 长期 要求"),
        _path_candidate(DESMI_MANUAL, 5, "desmi-1", "SUMMIT 2196 长期 存放 要求"),
    )

    decision = select_evidence("SUMMIT 2196 长期存放要求？", _payload(*chunks), limit=99)

    assert decision.allowed is True
    assert decision.routed_document == SUMMIT_MANUAL
    assert [item.citation.chunk_id for item in decision.selected] == [
        "summit-1",
        "summit-2",
        "summit-3",
    ]
    assert {item.citation.source_file for item in decision.selected} == {SUMMIT_MANUAL}


@pytest.mark.parametrize(
    ("alias", "expected_document"),
    [
        ("2196", SUMMIT_MANUAL),
        ("SuMmIt", SUMMIT_MANUAL),
        ("DESMI", DESMI_MANUAL),
        ("t1739", DESMI_MANUAL),
    ],
)
def test_each_exact_alias_routes_to_its_manual(alias: str, expected_document: str) -> None:
    candidate = _path_candidate(
        expected_document,
        8,
        f"{alias.casefold()}-chunk",
        f"{alias} 轴承 温度",
    )

    decision = select_evidence(f"{alias} 轴承 温度？", _payload(candidate))

    assert decision.allowed is True
    assert decision.routed_document == expected_document
    assert decision.selected[0].citation.source_file == expected_document


@pytest.mark.parametrize(
    ("filename", "expected_document", "other_document"),
    [
        ("2196-ansi-manual-chinese.pdf", SUMMIT_MANUAL, DESMI_MANUAL),
        ("t1739cn.pdf", DESMI_MANUAL, SUMMIT_MANUAL),
    ],
)
def test_explicit_filename_routes_and_excludes_the_other_manual(
    filename: str,
    expected_document: str,
    other_document: str,
) -> None:
    other = _path_candidate(other_document, 1, "other", f"{filename} 轴承 温度")
    expected = _path_candidate(expected_document, 2, "expected", "轴承 温度")

    decision = select_evidence(f"{filename} 轴承 温度", _payload(other, expected))

    assert decision.routed_document == expected_document
    assert {item.citation.source_file for item in decision.selected} == {expected_document}


def test_explicit_filename_takes_precedence_over_a_competing_generic_alias() -> None:
    summit = _path_candidate(SUMMIT_MANUAL, 1, "summit", "轴承 温度")
    desmi = _path_candidate(DESMI_MANUAL, 2, "desmi", "DESMI 轴承 温度")

    decision = select_evidence(
        "2196-ansi-manual-chinese.pdf DESMI 轴承 温度", _payload(desmi, summit)
    )

    assert decision.routed_document == SUMMIT_MANUAL
    assert {item.citation.source_file for item in decision.selected} == {SUMMIT_MANUAL}


def test_aliases_for_both_manuals_keep_cross_document_candidates() -> None:
    summit = _path_candidate(SUMMIT_MANUAL, 2, "summit", "SUMMIT 轴承 温度")
    desmi = _path_candidate(DESMI_MANUAL, 3, "desmi", "DESMI 轴承 温度")

    decision = select_evidence("SUMMIT DESMI 轴承 温度？", _payload(summit, desmi))

    assert decision.allowed is True
    assert decision.routed_document is None
    assert {item.citation.source_file for item in decision.selected} == {
        SUMMIT_MANUAL,
        DESMI_MANUAL,
    }


def test_ambiguous_aliases_exclude_unmatched_documents() -> None:
    summit = _path_candidate(SUMMIT_MANUAL, 2, "summit", "SUMMIT 轴承 温度")
    desmi = _path_candidate(DESMI_MANUAL, 3, "desmi", "DESMI 轴承 温度")
    unrelated = _path_candidate(
        "other-manual.pdf",
        4,
        "unrelated",
        "SUMMIT DESMI 轴承 温度",
    )

    decision = select_evidence("SUMMIT DESMI 轴承 温度？", _payload(summit, desmi, unrelated))

    assert decision.routed_document is None
    assert {item.citation.source_file for item in decision.selected} == {
        SUMMIT_MANUAL,
        DESMI_MANUAL,
    }


def test_chunk_header_is_a_trusted_metadata_decoder_path() -> None:
    candidate = _header_candidate(
        DESMI_MANUAL,
        11,
        "desmi-header",
        "DESMI 机械 密封 检查",
    )

    decision = select_evidence("DESMI 机械 密封 如何检查？", _payload(candidate))

    assert decision.allowed is True
    assert decision.selected[0].citation == Citation(DESMI_MANUAL, 11, "desmi-header")


def test_provenance_metadata_cannot_satisfy_overlap() -> None:
    candidate = _header_candidate(SUMMIT_MANUAL, 11, "unrelated", "无关内容")

    decision = select_evidence("火星基地 page 11", _payload(candidate))

    assert decision == EvidenceDecision(False, None, ())


def test_conflicting_chunk_header_cannot_satisfy_overlap() -> None:
    selected_citation = Citation(SUMMIT_MANUAL, 3, "selected")
    conflicting_header = encode_chunk_header(Citation(DESMI_MANUAL, 11, "conflicting"))
    candidate = {
        "file_path": encode_source_ref(selected_citation),
        "content": f"{conflicting_header}\n无关内容",
    }

    decision = select_evidence("火星基地 page 11", _payload(candidate))

    assert decision == EvidenceDecision(False, None, ())


def test_normalized_tokens_rank_by_overlap_then_original_rank() -> None:
    lower_rank = _path_candidate(
        DESMI_MANUAL,
        1,
        "lower-rank",
        "t1739 机械密封 50hz",
    )
    best = _path_candidate(
        DESMI_MANUAL,
        2,
        "best",
        "T1739 机械密封 50HZ MODEL-X",
    )
    tied_later = _path_candidate(
        DESMI_MANUAL,
        3,
        "tied-later",
        "T1739 机械密封 50HZ",
    )

    full_width_question = (
        "\uff34\uff11\uff17\uff13\uff19 机械密封 \uff15\uff10\uff28\uff5a "
        "\uff2d\uff2f\uff24\uff25\uff2c\uff0d\uff38"
    )
    decision = select_evidence(full_width_question, _payload(lower_rank, best, tied_later))

    assert [item.citation.chunk_id for item in decision.selected] == [
        "best",
        "lower-rank",
        "tied-later",
    ]


def test_reference_and_chunk_duplicates_merge_text_and_keep_first_rank() -> None:
    first = Citation(SUMMIT_MANUAL, 9, "same-page-first")
    second = Citation(SUMMIT_MANUAL, 9, "same-page-second")
    payload = {
        "data": {
            "references": [
                {"file_path": encode_source_ref(first)},
                {"file_path": encode_source_ref(second)},
            ],
            "chunks": [
                {
                    "file_path": encode_source_ref(second),
                    "content": "SUMMIT 轴承 温度",
                },
                {
                    "file_path": encode_source_ref(first),
                    "content": "SUMMIT 轴承 温度",
                },
            ],
        }
    }

    decision = select_evidence("SUMMIT 轴承 温度？", payload)

    assert [item.citation.chunk_id for item in decision.selected] == [
        "same-page-first",
        "same-page-second",
    ]
    assert [item.rank for item in decision.selected] == [0, 1]
    assert all(item.text == "SUMMIT 轴承 温度" for item in decision.selected)


def test_stopword_only_overlap_and_one_alias_refuses_without_a_route() -> None:
    candidate = _path_candidate(SUMMIT_MANUAL, 4, "stopwords", "SUMMIT 如何 什么 的")

    decision = select_evidence("SUMMIT 如何 什么 的？", _payload(candidate))

    assert decision == EvidenceDecision(False, None, ())


def test_unknown_question_with_unshared_terms_refuses() -> None:
    candidate = _path_candidate(SUMMIT_MANUAL, 5, "storage", "长期 存放 轴承 防腐")

    decision = select_evidence("火星基地零重力维护周期？", _payload(candidate))

    assert decision == EvidenceDecision(False, None, ())


def test_generic_chinese_bigrams_do_not_satisfy_the_evidence_gate() -> None:
    candidate = _path_candidate(
        SUMMIT_MANUAL,
        6,
        "generic-maintenance",
        "设备维护周期要求。",
    )

    decision = select_evidence("设备维护周期如何确定？", _payload(candidate))

    assert decision == EvidenceDecision(False, None, ())


def test_substantive_unspaced_chinese_phrase_passes_the_evidence_gate() -> None:
    candidate = _path_candidate(
        SUMMIT_MANUAL,
        7,
        "bearing-temperature",
        "轴承温度过高时检查润滑。",
    )

    decision = select_evidence("轴承温度过高怎么办？", _payload(candidate))

    assert decision.allowed is True
    assert decision.selected[0].citation.chunk_id == "bearing-temperature"


def test_generic_unspaced_chinese_instruction_does_not_pass_the_evidence_gate() -> None:
    candidate = _path_candidate(
        SUMMIT_MANUAL,
        8,
        "generic-instruction",
        "如何进行更换密封",
    )

    decision = select_evidence("如何进行检查轴承", _payload(candidate))

    assert decision == EvidenceDecision(False, None, ())


def test_generic_cjk_instruction_prefix_does_not_satisfy_overlap() -> None:
    candidate = _path_candidate(
        SUMMIT_MANUAL,
        9,
        "generic-prefix",
        "具体操作步骤更换密封",
    )

    decision = select_evidence("具体操作步骤检查轴承", _payload(candidate))

    assert decision == EvidenceDecision(False, None, ())


def test_domain_tokens_normalize_high_and_overhigh_for_near_chinese_phrasing() -> None:
    candidate = _path_candidate(
        SUMMIT_MANUAL,
        10,
        "bearing-high-temperature",
        "轴承温度过高时检查润滑。",
    )

    decision = select_evidence("轴承温度高怎么办？", _payload(candidate))

    assert decision.allowed is True
    assert decision.selected[0].citation.chunk_id == "bearing-high-temperature"

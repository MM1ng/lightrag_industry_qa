from __future__ import annotations

from industrial_rag.semantic_judge import SemanticSupport
from industrial_rag.semantic_judge_contract import (
    build_compact_semantic_judge_prompt,
    parse_compact_batch_judgement,
)


def _valid_response() -> str:
    return (
        '{"claims":[{"claim_id":"c1","supported":["e1"],'
        '"partially_supported":["e2"],"not_supported":["e3"],"uncertain":[]},'
        '{"claim_id":"c2","supported":[],"partially_supported":[],'
        '"not_supported":["e1"],"uncertain":["e2","e3"]}]}'
    )


def test_compact_contract_accepts_complete_disjoint_evidence_sets() -> None:
    result = parse_compact_batch_judgement(_valid_response(), claim_ids=("c1", "c2"), evidence_ids=("e1", "e2", "e3"))

    assert result.valid is True
    assert result.judgements[("c1", "e1")] is SemanticSupport.SUPPORTED
    assert result.judgements[("c1", "e2")] is SemanticSupport.PARTIALLY_SUPPORTED
    assert result.judgements[("c1", "e3")] is SemanticSupport.NOT_SUPPORTED
    assert result.judgements[("c2", "e2")] is SemanticSupport.UNCERTAIN
    assert result.returned_pair_count == 6


def test_compact_contract_rejects_missing_claim() -> None:
    raw = '{"claims":[{"claim_id":"c1","supported":["e1"],"partially_supported":[],"not_supported":[],"uncertain":["e2"]}]}'
    result = parse_compact_batch_judgement(raw, claim_ids=("c1", "c2"), evidence_ids=("e1", "e2"))
    assert result.valid is False
    assert "missing_claim" in result.subtypes


def test_compact_contract_rejects_missing_evidence() -> None:
    raw = '{"claims":[{"claim_id":"c1","supported":["e1"],"partially_supported":[],"not_supported":[],"uncertain":[]}]}'
    result = parse_compact_batch_judgement(raw, claim_ids=("c1",), evidence_ids=("e1", "e2"))
    assert result.valid is False
    assert "missing_evidence" in result.subtypes


def test_compact_contract_rejects_duplicate_claim() -> None:
    claim = '{"claim_id":"c1","supported":["e1"],"partially_supported":["e2"],"not_supported":["e3"],"uncertain":[]}'
    raw = '{"claims":[' + claim + ',' + claim + ']}'
    result = parse_compact_batch_judgement(raw, claim_ids=("c1",), evidence_ids=("e1", "e2", "e3"))
    assert result.valid is False
    assert "duplicate_claim" in result.subtypes


def test_compact_contract_rejects_evidence_in_two_sets() -> None:
    raw = '{"claims":[{"claim_id":"c1","supported":["e1"],"partially_supported":["e1"],"not_supported":[],"uncertain":[]}]}'
    result = parse_compact_batch_judgement(raw, claim_ids=("c1",), evidence_ids=("e1",))
    assert result.valid is False
    assert "duplicate_evidence" in result.subtypes


def test_compact_contract_rejects_unknown_ids_and_illegal_json() -> None:
    unknown = '{"claims":[{"claim_id":"c9","supported":["e9"],"partially_supported":[],"not_supported":[],"uncertain":[]}]}'
    unknown_result = parse_compact_batch_judgement(unknown, claim_ids=("c1",), evidence_ids=("e1",))
    assert unknown_result.valid is False
    assert "unknown_claim_id" in unknown_result.subtypes

    illegal_result = parse_compact_batch_judgement("{", claim_ids=("c1",), evidence_ids=("e1",))
    assert illegal_result.valid is False
    assert "invalid_json" in illegal_result.subtypes


def test_compact_contract_rejects_unknown_support_set() -> None:
    raw = '{"claims":[{"claim_id":"c1","supported":[],"partially_supported":[],"not_supported":[],"uncertain":[],"unsupported": ["e1"]}]}'
    result = parse_compact_batch_judgement(raw, claim_ids=("c1",), evidence_ids=("e1",))
    assert result.valid is False
    assert "invalid_support_enum" in result.subtypes


def test_compact_contract_does_not_require_explanation_or_read_labels() -> None:
    prompt = build_compact_semantic_judge_prompt(
        {
            "claims": [{"claim_id": "c1", "claim_text": "结论"}],
            "evidence": [{"evidence_id": "e1", "excerpt": "证据"}],
        }
    )
    assert "explanation" not in prompt
    assert "supporting_actual_chunk_ids" not in prompt


def test_invalid_compact_response_is_not_silently_completed() -> None:
    result = parse_compact_batch_judgement(
        '{"claims":[{"claim_id":"c1","supported":[],"partially_supported":[],"not_supported":[],"uncertain":[]}]}'
        , claim_ids=("c1",), evidence_ids=("e1",)
    )
    assert result.valid is False
    assert result.judgements == {("c1", "e1"): SemanticSupport.UNCERTAIN}

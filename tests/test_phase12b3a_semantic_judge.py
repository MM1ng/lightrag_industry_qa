from __future__ import annotations

import json
from pathlib import Path

import pytest

from industrial_rag.semantic_judge import (
    SemanticSupport,
    build_batch_judge_input,
    parse_batch_judgement,
    select_supported_evidence,
)


def _claim(*, claim_id: str = "c1", text: str = "泵轴每周旋转一次") -> dict[str, object]:
    return {"claim_id": claim_id, "text": text}


def _evidence(*, evidence_id: str = "e1", excerpt: str = "至少每周用手旋转泵轴一次") -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "citation_id": f"cite_{evidence_id}",
        "document_name": "manual.pdf",
        "page": 9,
        "chunk_id": evidence_id,
        "excerpt": excerpt,
    }


def test_batch_input_contains_only_runtime_claim_and_evidence_fields() -> None:
    payload = build_batch_judge_input(
        claims=[_claim()],
        candidate_evidence=[_evidence()],
    )

    encoded = json.dumps(payload, ensure_ascii=False)
    assert payload["claims"] == [{"claim_id": "c1", "claim_text": "泵轴每周旋转一次"}]
    assert payload["evidence"][0]["evidence_id"] == "e1"
    assert "supporting_actual_chunk_ids" not in encoded
    assert "expected_evidence" not in encoded
    assert "golden" not in encoded


def test_evaluation_label_in_runtime_input_is_rejected() -> None:
    evidence = _evidence()
    evidence["supporting_actual_chunk_ids"] = ["e1"]

    with pytest.raises(ValueError, match="evaluation label"):
        build_batch_judge_input(claims=[_claim()], candidate_evidence=[evidence])


def test_valid_batch_judgement_preserves_supported_label() -> None:
    result = parse_batch_judgement(
        json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "c1",
                        "evidence": [{"evidence_id": "e1", "support": "supported"}],
                    }
                ]
            }
        ),
        claim_ids=("c1",),
        evidence_ids=("e1",),
    )

    assert result.valid is True
    assert result.judgements[("c1", "e1")] == SemanticSupport.SUPPORTED


def test_parser_accepts_all_required_support_labels() -> None:
    result = parse_batch_judgement(
        json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "c1",
                        "evidence": [
                            {"evidence_id": "e1", "support": "supported"},
                            {"evidence_id": "e2", "support": "partially_supported"},
                            {"evidence_id": "e3", "support": "not_supported"},
                            {"evidence_id": "e4", "support": "uncertain"},
                        ],
                    }
                ]
            }
        ),
        claim_ids=("c1",),
        evidence_ids=("e1", "e2", "e3", "e4"),
    )

    assert result.valid is True
    assert set(result.judgements.values()) == set(SemanticSupport)


def test_invalid_json_fails_closed_as_uncertain() -> None:
    result = parse_batch_judgement(
        "not-json",
        claim_ids=("c1",),
        evidence_ids=("e1",),
    )

    assert result.valid is False
    assert result.judgements[("c1", "e1")] == SemanticSupport.UNCERTAIN


def test_only_supported_evidence_is_selected_and_partial_is_not_inferred() -> None:
    judgements = {
        ("c1", "e1"): SemanticSupport.SUPPORTED,
        ("c1", "e2"): SemanticSupport.PARTIALLY_SUPPORTED,
        ("c1", "e3"): SemanticSupport.NOT_SUPPORTED,
        ("c1", "e4"): SemanticSupport.UNCERTAIN,
    }

    assert select_supported_evidence(judgements) == {"c1": ("e1",)}


def test_missing_runtime_excerpt_is_rejected_before_judge_call() -> None:
    evidence = _evidence(excerpt="")

    with pytest.raises(ValueError, match="runtime evidence text"):
        build_batch_judge_input(claims=[_claim()], candidate_evidence=[evidence])


def test_semantic_judge_module_has_no_evaluation_dependency() -> None:
    source = Path("src/industrial_rag/semantic_judge.py").read_text(encoding="utf-8")

    assert "evaluation/" not in source
    assert "supporting_actual_chunk_ids" in source  # forbidden-key guard only

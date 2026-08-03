import json
from pathlib import Path

from industrial_rag.phase10b_citation_binding import check_citation_binding


def test_citation_binding_detects_wrong_chunk():
    case = {
        "question_id": "q1",
        "golden": {
            "split": "development",
            "answerable": True,
            "expected_evidence": [{"evidence_id": "e1", "chunk_id": "expected", "document_name": "d", "page_number": 1}],
            "expected_answer_points": [{"point_id": "p1", "supported_by": ["e1"]}],
        },
        "response": {"citations": [{"chunk_id": "wrong", "document_name": "d", "page": 1}]},
    }
    result = check_citation_binding(case)
    assert result["wrong_chunk"] is True
    assert result["all_answer_points_supported"] is False


def test_citation_binding_artifact_has_no_claim_level_inference():
    payload = json.loads(Path("evaluation/phase10/citation_binding_results.json").read_text(encoding="utf-8"))
    assert payload["holdout_used_for_tuning"] is False
    assert payload["claim_level_accuracy_available"] is False

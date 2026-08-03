from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from industrial_rag.services.golden_set_policy import CANONICAL_QUESTION_IDS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = PROJECT_ROOT / "evaluation" / "phase10" / "expanded_golden_set.jsonl"
MANIFEST_PATH = PROJECT_ROOT / "evaluation" / "phase10" / "golden_set_manifest.json"
CHILD_PATHS = (
    "evaluation/experiments/parser_backend/fixed_model/P1_mineru/"
    "2196-ANSI-Manual-Chinese.pdf/child_chunks.jsonl",
    "evaluation/experiments/parser_backend/fixed_model/P1_mineru/"
    "t1739cn.pdf/child_chunks.jsonl",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_chunks() -> dict[tuple[str, str], dict]:
    chunks: dict[tuple[str, str], dict] = {}
    for relative_path in CHILD_PATHS:
        for line in (PROJECT_ROOT / relative_path).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            chunks[(row["document_name"], row["chunk_id"])] = row
    return chunks


def test_phase10_golden_set_is_multi_evidence_and_provenance_backed() -> None:
    """Catches stale chunk IDs, representative-only cross-page labels, or weak negatives."""
    rows = [
        json.loads(line)
        for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    chunks = _load_chunks()
    assert len(rows) == 64
    assert Counter(row["split"] for row in rows) == {
        "development": 36,
        "validation": 16,
        "holdout": 12,
    }
    assert len({row["question_id"] for row in rows}) == 64
    assert set(CANONICAL_QUESTION_IDS) <= {row["question_id"] for row in rows}
    assert sum(not row["answerable"] for row in rows) >= 4

    for row in rows:
        evidence_ids = {item["evidence_id"] for item in row["expected_evidence"]}
        assert len(evidence_ids) == len(row["expected_evidence"])
        if row["answerable"]:
            assert row["expected_evidence"]
            assert any(item["role"] == "primary" for item in row["expected_evidence"])
            assert row["expected_answer_points"]
            for evidence in row["expected_evidence"]:
                assert evidence["role"] in {"primary", "supporting"}
                assert evidence["relevance_grade"] == (
                    2 if evidence["role"] == "primary" else 1
                )
                chunk = chunks[(evidence["document_name"], evidence["chunk_id"])]
                assert chunk["page_start"] <= evidence["page_number"] <= chunk["page_end"]
                assert evidence["evidence_text"] in chunk["content"]
            for point in row["expected_answer_points"]:
                assert point["point_id"]
                assert point["text"]
                assert point["supported_by"]
                assert set(point["supported_by"]) <= evidence_ids
        else:
            assert row["expected_evidence"] == []
            assert row["expected_answer_points"] == []
            assert row["negative_reason"]

        if row["question_type"] in {"cross_page", "multi_evidence"}:
            assert len({item["chunk_id"] for item in row["expected_evidence"]}) >= 2


def test_golden_manifest_freezes_exact_sources_hashes_and_metric_policy() -> None:
    """Catches wildcard-order provenance or post-freeze metric denominator drift."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["dataset_sha256"] == _sha256(GOLDEN_PATH)
    assert manifest["record_count"] == 64
    assert [item["path"] for item in manifest["child_chunk_artifacts"]] == list(
        CHILD_PATHS
    )
    for item in manifest["child_chunk_artifacts"]:
        assert item["sha256"] == _sha256(PROJECT_ROOT / item["path"])
    policy = manifest["metric_policy"]
    assert policy["retrieval_denominator"] == "answerable_positive_questions_only"
    assert policy["negative_questions_in_retrieval_denominator"] is False
    assert policy["empty_denominator_value"] is None
    assert policy["rate_shape"] == ["numerator", "denominator", "value"]
    assert policy["claim_level_citation_accuracy"]["available"] is False
    assert {
        "chunk_recall_at_k",
        "any_evidence_recall_at_k",
        "complete_evidence_recall_at_k",
        "document_recall_at_k",
        "page_recall_at_k",
        "mrr",
        "graded_ndcg_at_10",
        "false_rejection_rate",
        "negative_rejection_rate",
        "unsupported_answer_rate",
        "question_level_citation_accuracy",
    } <= set(policy["metrics"])


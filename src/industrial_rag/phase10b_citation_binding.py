"""Deterministic citation binding checks against the frozen multi-evidence set."""

from __future__ import annotations

from typing import Any


def check_citation_binding(case: dict[str, Any]) -> dict[str, Any]:
    expected = case["golden"].get("expected_evidence", [])
    citations = case.get("response", {}).get("citations", [])
    expected_by_id = {item["chunk_id"]: item for item in expected}
    cited_ids = {item.get("chunk_id") for item in citations}
    expected_docs = {item["document_name"] for item in expected}
    cited_docs = {item.get("document_name") for item in citations}
    expected_pages = {(item["document_name"], item["page_number"]) for item in expected}
    cited_pages = {(item.get("document_name"), item.get("page")) for item in citations}
    point_results = []
    for point in case["golden"].get("expected_answer_points", []):
        supporting_chunks = {expected_by_id[eid]["chunk_id"] for eid in point["supported_by"] if eid in expected_by_id}
        point_results.append({"point_id": point["point_id"], "supported": bool(supporting_chunks & cited_ids)})
    return {
        "question_id": case["question_id"],
        "split": case["golden"]["split"],
        "answerable": case["golden"]["answerable"],
        "wrong_document": bool(cited_ids) and not bool(expected_docs & cited_docs),
        "wrong_page": bool(cited_ids) and not bool(expected_pages & cited_pages),
        "wrong_chunk": bool(cited_ids) and not bool(expected_by_id.keys() & cited_ids),
        "expected_evidence_count": len(expected),
        "cited_expected_count": len(expected_by_id.keys() & cited_ids),
        "answer_point_results": point_results,
        "all_answer_points_supported": all(item["supported"] for item in point_results),
        "claim_level_accuracy_available": False,
    }

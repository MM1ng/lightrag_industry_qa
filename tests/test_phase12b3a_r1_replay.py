from __future__ import annotations

import json

from industrial_rag.runtime_chunk_hydration import RuntimeChunkHydrator
from scripts.phase12b3a_r1_hydrate_and_replay import (
    hydrate_runtime_rows,
    recover_runtime_candidate_matrix,
)


def test_recover_matrix_from_existing_trace_without_retrieval() -> None:
    row = {
        "split": "development",
        "question_id": "S007",
        "response": {"claims": [], "evidence": [], "citations": [], "status": "insufficient_evidence"},
        "trace": {
            "final_selected_chunks": [
                {
                    "chunk_id": "c1",
                    "document_name": "manual.pdf",
                    "page_number": 2,
                    "initial_rank": 1,
                    "reranked_rank": None,
                }
            ]
        },
    }

    candidates, status = recover_runtime_candidate_matrix(row)

    assert status == "recovered_from_trace"
    assert [item["chunk_id"] for item in candidates] == ["c1"]
    assert candidates[0]["evidence_id"] == "trace:c1"


def test_hydration_does_not_change_candidate_chunk_set(tmp_path) -> None:
    path = tmp_path / "chunks.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({"chunk_id": chunk_id, "content": text})
            for chunk_id, text in (("c1", "文本一"), ("c2", "文本二"))
        )
        + "\n",
        encoding="utf-8",
    )
    row = {
        "split": "development",
        "question_id": "S001",
        "response": {
            "status": "success",
            "claims": [{"claim_id": "P1", "text": "结论", "evidence_ids": ["E1", "E2"]}],
            "evidence": [
                {"evidence_id": "E1", "citation_id": "cite_1", "chunk_id": "c1", "excerpt": ""},
                {"evidence_id": "E2", "citation_id": "cite_2", "chunk_id": "c2", "excerpt": ""},
            ],
            "citations": [],
        },
        "trace": {},
    }

    hydrated_rows, records, summary = hydrate_runtime_rows(
        [row], RuntimeChunkHydrator.from_jsonl((path,))
    )

    assert [item["chunk_id"] for item in hydrated_rows[0]["response"]["evidence"]] == ["c1", "c2"]
    assert [item["excerpt"] for item in hydrated_rows[0]["response"]["evidence"]] == ["文本一", "文本二"]
    assert summary["hydration_missing"] == 0
    assert records[0]["hydration_status"] == "hydrated"


def test_missing_hydration_is_not_replaced_by_other_text(tmp_path) -> None:
    path = tmp_path / "chunks.jsonl"
    path.write_text(json.dumps({"chunk_id": "other", "content": "不相关"}) + "\n", encoding="utf-8")
    row = {
        "split": "development",
        "question_id": "S001",
        "response": {
            "status": "success",
            "claims": [{"claim_id": "P1", "text": "结论"}],
            "evidence": [{"evidence_id": "E1", "citation_id": "cite_1", "chunk_id": "missing", "excerpt": ""}],
            "citations": [],
        },
        "trace": {},
    }

    hydrated_rows, records, summary = hydrate_runtime_rows(
        [row], RuntimeChunkHydrator.from_jsonl((path,))
    )

    assert hydrated_rows[0]["response"]["evidence"][0]["excerpt"] == ""
    assert records[0]["hydration_status"] == "missing"
    assert summary["hydration_missing"] == 1


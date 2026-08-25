from __future__ import annotations

import json

import pytest

from industrial_rag.runtime_chunk_hydration import RuntimeChunkHydrator


def test_hydration_reads_exact_chunk_id_and_preserves_full_text(tmp_path) -> None:
    path = tmp_path / "chunks.jsonl"
    path.write_text(
        json.dumps(
            {
                "chunk_id": "c1",
                "generation_id": "g1",
                "content": "数值 10 mm，必须保持一致。",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = RuntimeChunkHydrator.from_jsonl((path,)).hydrate(("c1",))

    assert result["c1"].hydration_status == "hydrated"
    assert result["c1"].text == "数值 10 mm，必须保持一致。"
    assert result["c1"].original_text_length == result["c1"].hydrated_text_length
    assert result["c1"].truncated is False
    assert result["c1"].hydration_source == str(path)


def test_hydration_fails_closed_for_missing_chunk(tmp_path) -> None:
    path = tmp_path / "chunks.jsonl"
    path.write_text("", encoding="utf-8")

    result = RuntimeChunkHydrator.from_jsonl((path,)).hydrate(("missing",))

    assert result["missing"].hydration_status == "missing"
    assert result["missing"].text == ""


def test_hydration_uses_exact_id_not_similarity_or_question(tmp_path) -> None:
    path = tmp_path / "chunks.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({"chunk_id": chunk_id, "content": content})
            for chunk_id, content in (("c1", "alpha"), ("c2", "alpha question overlap"))
        )
        + "\n",
        encoding="utf-8",
    )

    result = RuntimeChunkHydrator.from_jsonl((path,)).hydrate(("c1",))

    assert tuple(result) == ("c1",)
    assert result["c1"].text == "alpha"


def test_hydration_rejects_evaluation_labels(tmp_path) -> None:
    path = tmp_path / "chunks.jsonl"
    path.write_text(
        json.dumps(
            {
                "chunk_id": "c1",
                "content": "runtime text",
                "supporting_actual_chunk_ids": ["c1"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="evaluation label"):
        RuntimeChunkHydrator.from_jsonl((path,))


def test_hydration_can_bound_text_and_records_truncation(tmp_path) -> None:
    path = tmp_path / "chunks.jsonl"
    path.write_text(
        json.dumps({"chunk_id": "c1", "content": "abcdef"}) + "\n",
        encoding="utf-8",
    )

    item = RuntimeChunkHydrator.from_jsonl((path,)).hydrate(("c1",), max_text_chars=4)["c1"]

    assert item.text == "abcd"
    assert item.original_text_length == 6
    assert item.hydrated_text_length == 4
    assert item.truncated is True


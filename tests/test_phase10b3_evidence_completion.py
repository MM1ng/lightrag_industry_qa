from industrial_rag.evidence_completion import ContextRecord, complete_evidence
from industrial_rag.evidence_policy import select_evidence


def _record(chunk_id: str, *, document_id: str = "d1", generation_id: str = "g1", **kwargs):
    return ContextRecord("kb", generation_id, document_id, "manual.pdf", chunk_id, "text", 1, **kwargs)


def test_completion_is_bounded_and_same_document_generation():
    selected = [_record("c2", previous_chunk_id="c1", next_chunk_id="c3", parent_chunk_id="p1")]
    registry = {item.chunk_id: item for item in [
        _record("c1"), _record("c3"), _record("p1"), _record("other", document_id="d2")
    ]}
    result = complete_evidence(selected, registry, max_completion=2)
    assert [item.chunk_id for item in result] == ["p1", "c1"]


def test_completion_rejects_cross_generation_context():
    selected = [_record("c2", next_chunk_id="c3")]
    registry = {"c3": _record("c3", generation_id="old")}
    assert complete_evidence(selected, registry) == ()


def test_diversified_selection_is_opt_in_and_keeps_original_default():
    payload = {
        "data": {
            "chunks": [
                {"file_path": "rag-source::manual.pdf::page=1::chunk=c1", "content": "设备 温度 压力"},
                {"file_path": "rag-source::manual.pdf::page=2::chunk=c2", "content": "设备 温度 压力"},
                {"file_path": "rag-source::manual.pdf::page=3::chunk=c3", "content": "设备 压力 流量"},
            ]
        }
    }
    default = select_evidence("设备 温度 压力 流量", payload)
    diversified = select_evidence("设备 温度 压力 流量", payload, diversify=True)
    assert [item.citation.chunk_id for item in default.selected] == ["c1", "c2", "c3"]
    assert [item.citation.chunk_id for item in diversified.selected] == ["c1", "c3"]

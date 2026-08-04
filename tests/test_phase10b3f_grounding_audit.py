from industrial_rag.answer_grounding import build_answer_plan, build_non_generation_audit
from industrial_rag.citation_formatter import Citation
from industrial_rag.evidence_policy import EvidenceCandidate
from industrial_rag.retrieval_trace import (
    GROUNDING_AUDIT_TRACE_VERSION,
    RetrievalExecutionTrace,
)


def _candidate(chunk_id: str, text: str) -> EvidenceCandidate:
    return EvidenceCandidate(Citation("manual.pdf", 1, chunk_id), text, 1)


def test_raw_answer_is_captured_before_grounding_and_not_overwritten():
    result = build_answer_plan(
        "压力为 10 bar。颜色为蓝色。",
        [_candidate("c1", "压力为 10 bar。")],
        [Citation("manual.pdf", 1, "c1")],
    )
    audit = result.grounding_audit
    assert audit is not None
    assert audit.pre_grounding_answer == "压力为 10 bar。颜色为蓝色。"
    assert audit.grounding_output_answer == "压力为 10 bar。"
    assert audit.removed_answer_points[0]["removal_reason"] == "unsupported_generation_claim"


def test_all_unsupported_keeps_fragments_and_is_replay_eligible():
    result = build_answer_plan(
        "颜色为蓝色。",
        [_candidate("c1", "压力为 10 bar。")],
        [Citation("manual.pdf", 1, "c1")],
    )
    audit = result.grounding_audit
    assert audit is not None
    assert audit.input_fragments
    assert audit.removed_answer_points
    assert audit.replay_eligible is True
    assert result.answer.startswith("手册中未检索到充分依据")


def test_generation_refusal_is_not_grounding_rejection():
    audit = build_non_generation_audit(
        answer="手册中未检索到充分依据，无法可靠回答该问题。",
        generation_invoked=True,
    )
    assert audit.generation_returned_refusal is True
    assert audit.replay_eligible is False
    assert audit.replay_ineligible_reason == "generation_returned_refusal"


def test_evidence_gate_refusal_records_generation_not_invoked():
    audit = build_non_generation_audit(generation_invoked=False)
    assert audit.generation_invoked is False
    assert audit.replay_eligible is False
    assert audit.replay_ineligible_reason == "generation_not_invoked"


def test_audit_truncation_disables_replay():
    audit = build_non_generation_audit(answer="x" * 16_001, generation_invoked=True)
    assert audit.pre_grounding_answer_truncated is True
    assert audit.replay_eligible is False
    assert audit.replay_ineligible_reason == "answer_truncated"


def test_trace_contains_audit_only_when_explicitly_attached():
    audit = build_non_generation_audit(generation_invoked=False)
    trace = RetrievalExecutionTrace(
        trace_version=GROUNDING_AUDIT_TRACE_VERSION,
        original_query="q",
        normalized_query="q",
        retrieval_config=(),
        initial_results=(),
        rerank_applied=False,
        reranked_results=(),
        final_selected_chunks=(),
        selected_chunk_ids=(),
        normalization_ms=0,
        retrieval_ms=0,
        rerank_ms=0,
        evidence_selection_ms=0,
        grounding_audit=audit.to_payload(),
    )
    payload = trace.to_payload()
    assert payload["trace_version"] == GROUNDING_AUDIT_TRACE_VERSION
    assert payload["grounding_audit"]["replay_eligible"] is False


def test_secret_redaction_disables_replay_and_keeps_secret_out_of_payload():
    audit = build_non_generation_audit(answer="Bearer super-secret-token-value", generation_invoked=True)
    assert audit.pre_grounding_answer_redacted is True
    assert audit.replay_eligible is False
    assert "super-secret-token-value" not in audit.to_payload()["pre_grounding_answer"]

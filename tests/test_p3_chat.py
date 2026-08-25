"""Behavior tests for mapping the P3 API contract to chat-session state."""

from __future__ import annotations

from app.api_client import ApiCitation, ApiClaim, ApiEvidence, ApiQueryResult
from app.chat_state import add_error_message, add_user_message, create_empty_session
from app.p3_chat import append_p3_answer, build_p3_history


def test_history_uses_the_latest_six_non_error_messages_in_order() -> None:
    """Catch history that includes UI-only errors or drops the most recent context."""
    session = create_empty_session()
    for index in range(4):
        session, _ = add_user_message(session, f"question {index}")
        session, _ = append_p3_answer(
            session,
            ApiQueryResult(
                request_id=f"req_{index}",
                status="success",
                answer=f"answer {index}",
            ),
        )
    session, _ = add_error_message(session, "temporary failure")

    assert build_p3_history(session) == [
        {"role": "user", "content": "question 1"},
        {"role": "assistant", "content": "answer 1"},
        {"role": "user", "content": "question 2"},
        {"role": "assistant", "content": "answer 2"},
        {"role": "user", "content": "question 3"},
        {"role": "assistant", "content": "answer 3"},
    ]


def test_history_limit_cannot_exceed_p3_six_message_contract() -> None:
    """Catch a caller requesting more P3 history than the API accepts."""
    session = create_empty_session()
    for index in range(4):
        session, _ = add_user_message(session, f"question {index}")
        session, _ = append_p3_answer(
            session,
            ApiQueryResult(
                request_id=f"req_{index}",
                status="success",
                answer=f"answer {index}",
            ),
        )

    assert build_p3_history(session, limit=99) == [
        {"role": "user", "content": "question 1"},
        {"role": "assistant", "content": "answer 1"},
        {"role": "user", "content": "question 2"},
        {"role": "assistant", "content": "answer 2"},
        {"role": "user", "content": "question 3"},
        {"role": "assistant", "content": "answer 3"},
    ]
    assert build_p3_history(session, limit=0) == []


def test_p3_insufficient_evidence_preserves_safe_answer_and_citation() -> None:
    """Catch P3 answers being misclassified as failures or losing their sources."""
    result = ApiQueryResult(
        request_id="req_evidence",
        status="insufficient_evidence",
        answer="当前手册证据不足，建议补充设备型号。",
        citations=(ApiCitation("pump.pdf", 12, "chunk_12"),),
        latency_ms=250,
    )

    session, message = append_p3_answer(create_empty_session(), result)

    assert session[-1] is message
    assert message.status == "insufficient_evidence"
    assert message.mode == "mix"
    assert message.latency_seconds == 0.25
    assert message.citations[0].display == "[pump.pdf，第12页]"


def test_p3_failed_status_becomes_a_chat_error_without_internal_metadata() -> None:
    """Catch failed P3 outcomes being rendered as normal successful answers."""
    result = ApiQueryResult(
        request_id="req_failed",
        status="failed",
        answer="安全审查未能可靠完成，请转人工复核。",
        citations=(ApiCitation("pump.pdf", 12, "chunk_12"),),
        latency_ms=250,
    )

    _, message = append_p3_answer(create_empty_session(), result)

    assert message.status == "error"
    assert message.mode is None
    assert message.latency_seconds is None
    assert message.citations == ()
    assert message.content == "安全审查未能可靠完成，请转人工复核。"


def test_p3_partial_answer_preserves_status_and_evidence_metadata() -> None:
    result = ApiQueryResult(
        request_id="req_partial",
        status="partial_answer",
        answer="已确认部分",
        knowledge_base_id="kb-1",
        generation_id="gen-1",
        claims=(ApiClaim("P1", "结论", ("cite_1",), ("E1",)),),
        evidence=(
            ApiEvidence("E1", "cite_1", "手册", None, 2, "c1", "gen-1", excerpt="证据"),
        ),
        partial_reason="缺少条件",
    )
    _, message = append_p3_answer(create_empty_session(), result)
    assert message.status == "partial_answer"
    assert message.knowledge_base_id == "kb-1"
    assert message.generation_id == "gen-1"
    assert message.claims[0].evidence_ids == ("E1",)
    assert message.evidence[0].excerpt == "证据"

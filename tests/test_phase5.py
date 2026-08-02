"""Phase 5: grounded answer contract tests (offline, no external LLM)."""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from evaluation.experiments.phase5.audit import audit_duplicates
from evaluation.experiments.phase5.config import (
    CANDIDATE_POOL_PATH,
    CANDIDATE_POOL_SHA256,
    PHASE5_CONFIG,
    PHASE5_ROOT,
    read_jsonl,
)
from evaluation.experiments.phase5.context_normalization import cn0_rows, cn1_rows
from evaluation.experiments.phase5.grounded_answer.core import (
    apply_safety_gate,
    grounded_answer_call,
    load_prompt_bundle,
    parse_grounded_json,
    schema_validate,
    validate_citations,
)
from evaluation.experiments.phase5.metrics import (
    _citation_metrics,
    _gold_pages_and_mapped,
    _replacement_gates,
    _structural_metrics,
)


def _pool_by_q() -> dict[str, list[dict]]:
    by_q: dict[str, list[dict]] = {}
    for row in read_jsonl(CANDIDATE_POOL_PATH):
        by_q.setdefault(row["question_id"], []).append(row)
    return by_q


def _registry() -> dict[str, dict]:
    return {
        "c1": {"document": "a.pdf", "page": 5, "text_hash": "h1"},
        "c2": {"document": "a.pdf", "page": 9, "text_hash": "h2"},
    }


def _valid_answer() -> dict:
    return {
        "status": "answered",
        "answer": "答案",
        "claims": [
            {
                "claim_id": "C1",
                "text": "结论",
                "claim_type": "fact",
                "citations": [{"chunk_id": "c1", "document_name": "a.pdf", "page": 5}],
            }
        ],
        "refusal_reason": None,
    }


class _FakeLLM:
    """Deterministic fake LLM; records calls; never falls back."""

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict] = []
        self.cache_hits = 0
        self.cache_misses = 0

    async def __call__(self, prompt: str, system_prompt: str | None = None, **kwargs):
        output = self.outputs.pop(0)
        self.calls.append(
            {
                "requested_model": "qwen-plus-2025-07-28",
                "actual_model": "qwen-plus-2025-07-28",
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "latency": 0.1,
                "retry_count": 0,
                "status": "ok",
                "error_code": None,
                "cache_hit": False,
            }
        )
        return output


# ---------------------------------------------------------------------------
# Baseline frozen
# ---------------------------------------------------------------------------


def test_phase5_config_is_frozen() -> None:
    assert PHASE5_CONFIG["parser_pipeline"] == "pymupdf_standard_adapter"
    assert PHASE5_CONFIG["query_mode"] == "mix"
    assert PHASE5_CONFIG["top_k"] == 12
    assert PHASE5_CONFIG["chunk_top_k"] == 20
    assert PHASE5_CONFIG["parent_expansion"] == "none"
    assert PHASE5_CONFIG["rerank_enabled"] is False
    assert PHASE5_CONFIG["answer_model"] == "qwen-plus-2025-07-28"
    assert PHASE5_CONFIG["fallback_enabled"] is False
    assert PHASE5_CONFIG["grounded_answer_enabled"] is False
    assert PHASE5_CONFIG["context_stable_dedup_enabled"] is False


def test_phase5_frozen_pool_sha256() -> None:
    assert (
        hashlib.sha256(CANDIDATE_POOL_PATH.read_bytes()).hexdigest()
        == CANDIDATE_POOL_SHA256
    )


def test_phase5_baseline_manifest_matches_if_present() -> None:
    path = PHASE5_ROOT / "baseline_manifest.json"
    if not path.is_file():
        pytest.skip("baseline manifest absent")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["source_phase"] == "Phase 4D-R2"
    assert manifest["frozen_candidate_pool"]["sha256"] == CANDIDATE_POOL_SHA256
    assert manifest["parser_pipeline"] == "pymupdf_standard_adapter"
    assert manifest["query_mode"] == "mix"
    assert manifest["rerank"] is False
    assert manifest["answer_model"] == "qwen-plus-2025-07-28"


# ---------------------------------------------------------------------------
# Duplicate audit + stable dedup
# ---------------------------------------------------------------------------


def test_duplicate_audit_detects_c003_c004_c007_c008() -> None:
    audit = audit_duplicates(read_jsonl(CANDIDATE_POOL_PATH))
    assert audit["affected_questions"] == ["C003", "C004", "C007", "C008"]
    assert audit["duplicate_row_count"] == 4
    c007 = audit["per_question"]["C007"]
    assert c007["row_count"] == 19
    assert c007["unique_chunk_id_count"] == 18
    assert c007["duplicate_chunk_ids"] == [
        "cchunk-pymupdf-v1-护手册-e05e769c5e5d-000-e05e769c5e5d"
    ]


def test_cn1_stable_unique_fill_rules() -> None:
    by_q = _pool_by_q()
    cn0 = cn0_rows(by_q)
    cn1, skipped = cn1_rows(by_q)
    assert cn0["C007"] != cn1["C007"]  # duplicate row removed
    for question_id, rows in cn1.items():
        ids = [r["child_chunk_id"] for r in rows]
        assert len(ids) == len(set(ids))  # unique per question
        assert len(rows) <= 12
        assert set(ids) <= {r["child_chunk_id"] for r in by_q[question_id]}  # no pool-out
        assert all(r["document_id"] for r in rows)
    assert skipped["C007"] == [5]
    # first occurrence keeps original rank
    first = cn1["C007"][0]
    assert first["rank"] == 1
    # deterministic
    cn1b, skipped_b = cn1_rows(by_q)
    assert cn1b == cn1
    assert skipped_b == skipped


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_schema_valid_answered_and_refusal() -> None:
    assert schema_validate(_valid_answer()) == []
    refusal = {
        "status": "insufficient_evidence",
        "answer": "现有资料不足以回答该问题。",
        "claims": [],
        "refusal_reason": "缺少证据",
    }
    assert schema_validate(refusal) == []


def test_schema_rejects_invalid_shapes() -> None:
    invalid_status = dict(_valid_answer(), status="maybe")
    assert schema_validate(invalid_status)
    answered_without_claims = dict(_valid_answer(), claims=[])
    # Schema-level: claims array is present, so schema passes; the semantic
    # contract (answered must carry claims) is enforced by the validator.
    assert schema_validate(answered_without_claims) == []
    assert validate_citations(answered_without_claims, _registry())["answer_without_claims"] == 1
    refusal_with_claims = {
        "status": "insufficient_evidence",
        "answer": "现有资料不足以回答该问题。",
        "claims": [_valid_answer()["claims"][0]],
        "refusal_reason": "x",
    }
    assert schema_validate(refusal_with_claims) == []
    assert validate_citations(refusal_with_claims, _registry())["refusal_with_claims"] == 1
    missing_citation_field = {
        "status": "answered",
        "answer": "答案",
        "claims": [
            {
                "claim_id": "C1",
                "text": "结论",
                "claim_type": "fact",
                "citations": [{"chunk_id": "c1", "document_name": "a.pdf"}],  # no page
            }
        ],
        "refusal_reason": None,
    }
    assert schema_validate(missing_citation_field)


def test_parse_grounded_json_strips_fence() -> None:
    payload = json.dumps(_valid_answer(), ensure_ascii=False)
    parsed, error = parse_grounded_json(f"```json\n{payload}\n```")
    assert error is None
    assert parsed["status"] == "answered"
    parsed, error = parse_grounded_json("not json")
    assert parsed is None
    assert error is not None


# ---------------------------------------------------------------------------
# CitationValidator
# ---------------------------------------------------------------------------


def test_citation_validator_accepts_context_chunk() -> None:
    result = validate_citations(_valid_answer(), _registry())
    assert result["valid"] is True


def test_citation_validator_rejects_pool_out_wrong_page_doc_duplicate() -> None:
    answer = _valid_answer()
    answer["claims"][0]["citations"] = [
        {"chunk_id": "outside", "document_name": "a.pdf", "page": 5}
    ]
    result = validate_citations(answer, _registry())
    assert result["valid"] is False
    assert result["invalid_chunk_reference_count"] == 1

    answer = _valid_answer()
    answer["claims"][0]["citations"] = [
        {"chunk_id": "c1", "document_name": "a.pdf", "page": 99}
    ]
    result = validate_citations(answer, _registry())
    assert result["invalid_page_count"] == 1

    answer = _valid_answer()
    answer["claims"][0]["citations"] = [
        {"chunk_id": "c1", "document_name": "b.pdf", "page": 5}
    ]
    result = validate_citations(answer, _registry())
    assert result["invalid_document_count"] == 1

    answer = _valid_answer()
    answer["claims"][0]["citations"] = [
        {"chunk_id": "c1", "document_name": "a.pdf", "page": 5},
        {"chunk_id": "c1", "document_name": "a.pdf", "page": 5},
    ]
    result = validate_citations(answer, _registry())
    assert result["duplicate_citation_count"] == 1


def test_citation_validator_uncited_claim_and_refusal_conflict() -> None:
    answer = _valid_answer()
    answer["claims"][0]["citations"] = []
    result = validate_citations(answer, _registry())
    assert result["uncited_claim_count"] == 1

    refusal = {
        "status": "insufficient_evidence",
        "answer": "现有资料不足以回答该问题。",
        "claims": [_valid_answer()["claims"][0]],
        "refusal_reason": "x",
    }
    result = validate_citations(refusal, _registry())
    assert result["refusal_with_claims"] == 1


# ---------------------------------------------------------------------------
# Safety gate
# ---------------------------------------------------------------------------


def test_safety_gate_drops_uncited_and_bypass_claims() -> None:
    answer = _valid_answer()
    safety_uncited = {
        "claim_id": "S1",
        "text": "禁止绕过联锁",
        "claim_type": "safety",
        "citations": [],
    }
    safety_bypass = {
        "claim_id": "S2",
        "text": "可以通过旁路短接解除联锁",
        "claim_type": "safety",
        "citations": [{"chunk_id": "c1", "document_name": "a.pdf", "page": 5}],
    }
    good_safety = {
        "claim_id": "S3",
        "text": "维修前必须切断电源",
        "claim_type": "safety",
        "citations": [{"chunk_id": "c1", "document_name": "a.pdf", "page": 5}],
    }
    answer["claims"] = [safety_uncited, safety_bypass, good_safety]
    result, _errors, dropped = apply_safety_gate(answer, _registry(), {})
    assert dropped == 2
    assert len(result["claims"]) == 1
    assert result["claims"][0]["claim_id"] == "S3"
    assert any("safety" in error for error in _errors)


def test_safety_gate_safe_fallback_when_all_claims_dropped() -> None:
    answer = _valid_answer()
    answer["claims"] = [
        {
            "claim_id": "S1",
            "text": "必须佩戴防护装备",
            "claim_type": "safety",
            "citations": [],
        }
    ]
    result, _errors, dropped = apply_safety_gate(answer, _registry(), {})
    assert dropped == 1
    assert result["status"] == "insufficient_evidence"
    assert result["refusal_reason"] == "safety_gate_rejected_all_claims"


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------


def test_grounded_answer_repair_max_once_and_success() -> None:
    bundle = load_prompt_bundle()
    invalid = '{"status": "answered", "answer": "x", "claims": [{"claim_id": "C1", "text": "结论", "claim_type": "fact", "citations": [{"chunk_id": "outside", "document_name": "a.pdf", "page": 5}]}], "refusal_reason": null}'
    valid = json.dumps(_valid_answer(), ensure_ascii=False)
    llm = _FakeLLM([invalid, valid])

    async def run() -> dict:
        return await grounded_answer_call(
            llm,
            question="问题",
            context_text="证据",
            registry=_registry(),
            bundle=bundle,
            max_repair_attempts=1,
        )

    result = asyncio.run(run())
    assert result["initial_valid"] is False
    assert result["repair_attempted"] is True
    assert result["repair_valid"] is True
    assert result["final_valid"] is True
    assert result["structured_answer"]["status"] == "answered"
    assert len(llm.calls) == 2  # at most one repair


def test_grounded_answer_safe_fallback_after_failed_repair() -> None:
    bundle = load_prompt_bundle()
    invalid = "not json at all"
    llm = _FakeLLM([invalid, invalid])

    async def run() -> dict:
        return await grounded_answer_call(
            llm,
            question="问题",
            context_text="证据",
            registry=_registry(),
            bundle=bundle,
            max_repair_attempts=1,
        )

    result = asyncio.run(run())
    assert result["repair_attempted"] is True
    assert result["repair_valid"] is False
    assert result["structured_answer"]["status"] == "insufficient_evidence"
    assert result["structured_answer"]["refusal_reason"] == "grounded_answer_invalid_after_repair"


def test_grounded_answer_no_repair_when_initial_valid() -> None:
    bundle = load_prompt_bundle()
    llm = _FakeLLM([json.dumps(_valid_answer(), ensure_ascii=False)])

    async def run() -> dict:
        return await grounded_answer_call(
            llm,
            question="问题",
            context_text="证据",
            registry=_registry(),
            bundle=bundle,
            max_repair_attempts=1,
        )

    result = asyncio.run(run())
    assert result["initial_valid"] is True
    assert result["repair_attempted"] is False
    assert len(llm.calls) == 1


# ---------------------------------------------------------------------------
# Metrics and gates
# ---------------------------------------------------------------------------


def _synthetic_rows() -> tuple[list[dict], list[dict]]:
    gold_pages, _mapped = _gold_pages_and_mapped()
    baseline: list[dict] = []
    grounded: list[dict] = []
    q_ids = [q for q in gold_pages if q not in ("N001", "N002")]
    for q in q_ids:
        expected = gold_pages[q]
        doc, page = next(iter(expected))
        baseline.append(
            {
                "question_id": q,
                "answer": "回答",
                "citations": [{"source_file": doc, "page_number": page, "chunk_id": "x"}],
                "refusal": False,
                "llm_called": True,
                "actual_model": ["qwen-plus-2025-07-28"],
                "claims": [],
                "repair_attempted": False,
                "repair_tokens": {},
                "input_tokens": 100,
                "output_tokens": 10,
                "total_tokens": 110,
                "answer_latency": 1.0,
                "total_latency": 1.0,
                "status": "ok",
                "cache_hit": False,
                "final_validation": {"valid": None},
                "refusal_reason": None,
            }
        )
        grounded.append(
            {
                "question_id": q,
                "answer": "回答",
                "citations": [{"source_file": doc, "page_number": page, "chunk_id": "x"}],
                "refusal": False,
                "llm_called": True,
                "actual_model": ["qwen-plus-2025-07-28"],
                "claims": [
                    {
                        "claim_id": "C1",
                        "text": "结论",
                        "claim_type": "fact",
                        "citations": [{"chunk_id": "x", "document_name": doc, "page": page}],
                    }
                ],
                "repair_attempted": False,
                "repair_tokens": {},
                "input_tokens": 120,
                "output_tokens": 12,
                "total_tokens": 132,
                "answer_latency": 1.2,
                "total_latency": 1.2,
                "status": "ok",
                "cache_hit": False,
                "final_validation": {
                    "valid": True,
                    "invalid_chunk_reference_count": 0,
                    "invalid_page_count": 0,
                    "invalid_document_count": 0,
                    "uncited_claim_count": 0,
                    "duplicate_citation_count": 0,
                    "empty_claim_count": 0,
                    "answer_without_claims": 0,
                    "refusal_with_claims": 0,
                },
                "refusal_reason": None,
            }
        )
    n_rows = [
        {
            "question_id": "N001",
            "answer": "现有资料不足以回答该问题。",
            "citations": [],
            "refusal": True,
            "llm_called": False,
            "actual_model": [],
            "claims": [],
            "repair_attempted": False,
            "repair_tokens": {},
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "answer_latency": 0,
            "total_latency": 0,
            "status": "ok",
            "cache_hit": False,
            "final_validation": {"valid": None},
            "refusal_reason": "evidence_policy_rejected",
        },
        {
            "question_id": "N002",
            "answer": "现有资料不足以回答该问题。",
            "citations": [],
            "refusal": True,
            "llm_called": False,
            "actual_model": [],
            "claims": [],
            "repair_attempted": False,
            "repair_tokens": {},
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "answer_latency": 0,
            "total_latency": 0,
            "status": "ok",
            "cache_hit": False,
            "final_validation": {"valid": None},
            "refusal_reason": "evidence_policy_rejected",
        },
    ]
    baseline.extend(n_rows)
    grounded.extend(n_rows)
    return baseline, grounded


def test_citation_metrics_denominators_and_raw_counts() -> None:
    baseline, grounded = _synthetic_rows()
    gold_pages, mapped = _gold_pages_and_mapped()
    ga0 = _citation_metrics(baseline, gold_pages=gold_pages, mapped=mapped)
    ga1 = _citation_metrics(grounded, gold_pages=gold_pages, mapped=mapped)
    assert ga0["universe"]["answerable_questions"] == 48
    assert ga0["universe"]["negative_questions"] == 2
    assert ga0["answer_citation_accuracy"]["denominator"] == 48
    assert ga0["insufficient_evidence_rejection_rate"]["denominator"] == 2
    assert ga1["answer_citation_accuracy"]["decimal"] == 1.0


def test_structural_metrics_denominators() -> None:
    _, grounded = _synthetic_rows()
    structural = _structural_metrics(grounded)
    assert structural["structural_citation_valid_rate"]["denominator"] == 50
    assert structural["invalid_chunk_reference_rate"]["decimal"] == 0
    assert structural["repair_trigger_rate"]["denominator"] == 50


def test_replacement_gates_fail_without_value_or_violations() -> None:
    ga0 = {
        "answer_citation_accuracy": {"decimal": 0.8},
        "answer_citation_precision": {"decimal": 0.3},
        "answer_citation_recall": {"decimal": 0.7},
        "citation_traceability": {"decimal": 1.0},
        "citation_traceability_emitted": {"decimal": 1.0},
            "non_gold_citation_reference_rate": {"decimal": 0.5},
        "answered_without_evidence_rate": {"decimal": 0.0},
        "insufficient_evidence_rejection_rate": {"decimal": 1.0},
        "negative_unsupported_answer_rate": {"decimal": 0.0},
        "false_rejection_rate": {"decimal": 0.3},
        "categories": {
            "参数查询": {"citation_accuracy": 0.9},
            "安全警告": {"citation_accuracy": 1.0},
        },
        "uncited_claim_rate": {"decimal": None},
    }
    ga1 = dict(ga0)
    structural = {
        "structural_citation_valid_rate": {"decimal": 1.0},
        "invalid_chunk_reference_rate": {"decimal": 0.0},
        "invalid_page_rate": {"decimal": 0.0},
        "invalid_document_rate": {"decimal": 0.0},
        "uncited_claim_rate": {"decimal": 0.05},
    }
    safety = {
        "baseline": {"citation_accuracy": 1.0},
        "grounded": {"citation_accuracy": 1.0, "uncited_safety_claims": 0},
        "safety_error_reduction_questions": 0,
    }
    gates = _replacement_gates(
        ga0=ga0,
        ga1=ga1,
        structural=structural,
        ga0_eng={"total_latency_p95": 4.0},
        ga1_eng={"total_latency_p95": 5.0},
        safety=safety,
        grounded_rows=[],
    )
    assert gates["hard_passed"] is True
    assert gates["value_passed"] is False
    assert gates["replacement_approved"] is False


# ---------------------------------------------------------------------------
# API compatibility
# ---------------------------------------------------------------------------


def test_public_api_contract_fields_preserved() -> None:
    from dataclasses import fields

    from app.api_client import ApiQueryResult

    names = {field.name for field in fields(ApiQueryResult)}
    assert {"request_id", "status", "answer", "citations"} <= names
    assert "claims" in names  # optional new field already present

"""Phase 5B: Grounded Answer Lite tests (offline)."""

from __future__ import annotations

import hashlib
import json

import pytest
from evaluation.experiments.phase5b.config import (
    CANDIDATE_POOL_PATH,
    CANDIDATE_POOL_SHA256,
    PHASE5B_ROOT,
)
from evaluation.experiments.phase5b.lite import (
    apply_claim_guard,
    apply_repair_mapping,
    build_alias_map,
    build_evidence_block,
    build_whitelist_text,
    detect_key_claim,
    parse_markers,
    process_sentences,
    sha256_text,
    validate_repair_output,
)
from evaluation.experiments.phase5b.metrics import (
    citation_metrics,
    marker_and_coverage_metrics,
    replacement_gates,
)


def _registry() -> dict:
    return {
        "abc123": {"document": "2196-ANSI-Manual-Chinese.pdf", "page": 24, "text": "泵应存放在清洁干燥处。"},
        "def456": {"document": "t1739cn.pdf", "page": 12, "text": "入口管路应单独支撑。"},
        "ghi789": {"document": "2196-ANSI-Manual-Chinese.pdf", "page": 9, "text": "每周旋转泵轴一次。"},
    }


def _gold_pages_and_mapped() -> tuple[dict, dict]:
    from evaluation.experiments.phase5b.config import EVIDENCE_MAPPING_PATH
    from evaluation.experiments.phase5b.diagnostics import _gold_pages

    mapping = json.loads(EVIDENCE_MAPPING_PATH.read_text(encoding="utf-8"))
    mapped: dict[str, set[str]] = {}
    for entry in mapping["entries"]:
        if entry["mapped"]:
            mapped.setdefault(entry["case_id"], set()).update(entry["mapped_child_ids"])
    return _gold_pages(), mapped


# ---------------------------------------------------------------------------
# Closeout
# ---------------------------------------------------------------------------


def test_cn1_offline_passed_but_answer_level_not_approved() -> None:
    cn = json.loads(
        (
            PHASE5B_ROOT.parent
            / "phase5"
            / "context_normalization"
            / "metrics.json"
        ).read_text(encoding="utf-8")
    )
    decision = cn["cn1_production_decision"]
    assert decision["offline_gates_passed"] is True
    assert decision["answer_level_approved"] is False
    assert decision["production_enabled"] is False
    assert cn["production_context_strategy"] == "current_rows"


def test_phase5_final_strategy_uses_current_rows() -> None:
    strategy = json.loads(
        (
            PHASE5B_ROOT.parent
            / "phase5"
            / "final_answer_strategy.json"
        ).read_text(encoding="utf-8")
    )
    assert strategy["context_strategy"] == "current_rows"
    assert strategy["closeout"]["cn1"]["answer_level_approved"] is False


def test_metric_rename_non_gold_with_historical_alias_and_complement() -> None:
    definitions = json.loads(
        (
            PHASE5B_ROOT.parent
            / "phase5"
            / "metrics_definition.json"
        ).read_text(encoding="utf-8")
    )["definitions"]
    non_gold = next(d for d in definitions if d["metric_name"] == "non_gold_citation_reference_rate")
    gold = next(d for d in definitions if d["metric_name"] == "gold_citation_reference_rate")
    assert non_gold["historical_name"].endswith("unsupported_citation_reference_rate")
    assert gold["complement_metric"] == "non_gold_citation_reference_rate"
    ng = non_gold["raw_counts"]
    g = gold["raw_counts"]
    assert ng["numerator"] + g["numerator"] == ng["denominator"] == g["denominator"]
    assert round(ng["decimal"] + g["decimal"], 4) == 1.0


# ---------------------------------------------------------------------------
# Frozen baseline
# ---------------------------------------------------------------------------


def test_phase5b_frozen_config() -> None:
    frozen = json.loads(
        (PHASE5B_ROOT / "config" / "frozen_common.json").read_text(encoding="utf-8")
    )
    assert frozen["parser_pipeline"] == "pymupdf_standard_adapter"
    assert frozen["query_mode"] == "mix"
    assert frozen["context_strategy"] == "current_rows"
    assert frozen["parent_expansion"] == "none"
    assert frozen["rerank_enabled"] is False
    assert frozen["answer_model"] == "qwen-plus-2025-07-28"
    assert frozen["fallback_enabled"] is False
    assert frozen["candidate_pool_sha256"] == CANDIDATE_POOL_SHA256


def test_phase5b_pool_sha256() -> None:
    assert (
        hashlib.sha256(CANDIDATE_POOL_PATH.read_bytes()).hexdigest()
        == CANDIDATE_POOL_SHA256
    )


def test_phase5b_baseline_manifest_if_present() -> None:
    path = PHASE5B_ROOT / "baseline_manifest.json"
    if not path.is_file():
        pytest.skip("baseline manifest absent")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["context_strategy"] == "current_rows"
    assert manifest["frozen_candidate_pool"]["sha256"] == CANDIDATE_POOL_SHA256
    assert manifest["prompts"]["inline_citation_prompt"]["sha256"]


# ---------------------------------------------------------------------------
# Marker parsing
# ---------------------------------------------------------------------------


def test_marker_single_and_multi_parse() -> None:
    chunks, malformed = parse_markers("启动前应打开吸入阀。[引用:abc123]")
    assert chunks == ["abc123"]
    assert malformed == []
    chunks, malformed = parse_markers("请确认阀全开。[引用:abc123,def456]")
    assert chunks == ["abc123", "def456"]
    assert malformed == []


def test_marker_malformed_and_invalid_chunks() -> None:
    _chunks, malformed = parse_markers("句子。[引用:]")
    assert malformed == ["[引用:]"]
    processed = process_sentences(
        "压力应保持 5 bar。[引用:outside]",
        whitelist={"abc123"},
        registry=_registry(),
    )
    stats = processed["marker_stats"]
    assert stats["invalid_chunk_markers"] == 1
    assert stats["valid_markers"] == 0


def test_marker_dedupe_and_max_two() -> None:
    processed = process_sentences(
        "压力 5 bar。[引用:abc123,abc123,def456,ghi789]",
        whitelist=set(_registry()),
        registry=_registry(),
    )
    info = processed["sentences"][0]
    assert info["valid_citation_count"] == 2  # dedup + cap


def test_metadata_completed_deterministically() -> None:
    processed = process_sentences(
        "压力应保持 5 bar。[引用:abc123]",
        whitelist=set(_registry()),
        registry=_registry(),
    )
    citation = processed["sentences"][0]["citations"][0]
    assert citation == {
        "chunk_id": "abc123",
        "document_name": "2196-ANSI-Manual-Chinese.pdf",
        "page": 24,
    }


def test_alias_resolution_maps_short_ids_to_real_chunks() -> None:
    registry = _registry()
    alias_map = build_alias_map(registry)
    assert alias_map["E1"] == "abc123"
    processed = process_sentences(
        "压力应保持 5 bar。[引用:E1]",
        whitelist=set(registry),
        registry=registry,
        alias_map=alias_map,
    )
    assert processed["sentences"][0]["citations"][0]["chunk_id"] == "abc123"
    # truncated real ids are still invalid (no fuzzy matching)
    processed = process_sentences(
        "压力应保持 5 bar。[引用:abc123-000]",
        whitelist=set(registry),
        registry=registry,
        alias_map=alias_map,
    )
    assert processed["sentences"][0]["valid_citation_count"] == 0


def test_build_evidence_block_contains_ids_but_model_output_never_needs_pages() -> None:
    block = build_evidence_block(_registry())
    assert "引用别名: E1" in block
    assert "chunk_id: abc123" in block
    assert "页码: 24" in block
    whitelist = build_whitelist_text(_registry())
    assert "abc123" in whitelist


# ---------------------------------------------------------------------------
# KeyClaimDetector
# ---------------------------------------------------------------------------


def test_key_claim_detector_rules() -> None:
    assert detect_key_claim("工作压力不应超过 10 bar。") == (True, ["parameter"])
    assert detect_key_claim("电机功率为 75 kW。") == (True, ["parameter"])
    assert detect_key_claim("首先关闭吸入阀，然后启动泵。")[0] is True
    assert "procedure" in detect_key_claim("安装时按下锁定销。")[1]
    assert "safety" in detect_key_claim("维修前必须切断电源并执行电气隔离。")[1]
    assert "troubleshooting" in detect_key_claim("若泵出现振动异常，应检查联轴器对中。")[1]
    is_key, _types = detect_key_claim("因此，以上内容为相关要求。")
    assert is_key is False
    # determinism
    assert detect_key_claim("工作压力不应超过 10 bar。") == detect_key_claim(
        "工作压力不应超过 10 bar。"
    )


# ---------------------------------------------------------------------------
# Citation-only repair
# ---------------------------------------------------------------------------


def test_repair_output_must_be_mapping_only() -> None:
    mapping, errors = validate_repair_output(
        '{"sentence_citations": [{"sentence_index": 0, "chunk_ids": ["abc123"]}]}'
    )
    assert errors == []
    assert mapping["sentence_citations"][0]["chunk_ids"] == ["abc123"]
    mapping, errors = validate_repair_output(
        '{"sentence_citations": [{"sentence_index": 0, "chunk_ids": ["outside"]}], "answer": "x"}'
    )
    assert errors and "unexpected keys" in errors[0]


def test_repair_pool_out_rejected_and_text_unchanged() -> None:
    processed = process_sentences(
        "压力应保持 5 bar。",
        whitelist={"abc123"},
        registry=_registry(),
    )
    before = sha256_text(processed["clean_answer"])
    mapping = {"sentence_citations": [{"sentence_index": 0, "chunk_ids": ["abc123", "outside"]}]}
    result = apply_repair_mapping(
        processed,
        mapping,
        whitelist={"abc123"},
        registry=_registry(),
        alias_map={"E1": "abc123"},
        answer_text_hash_before=before,
    )
    assert result["repair_errors"] and "pool-out" in result["repair_errors"][0]
    assert result["answer_text_unchanged"] is True
    assert result["answer_text_hash_after"] == before
    assert result["sentences"][0]["citations"][0]["chunk_id"] == "abc123"


# ---------------------------------------------------------------------------
# Claim guard
# ---------------------------------------------------------------------------


def test_claim_guard_prunes_only_uncited_key_claims() -> None:
    processed = process_sentences(
        "压力应保持 5 bar。[引用:abc123] 必须佩戴防护装备。 普通过渡句。",
        whitelist={"abc123"},
        registry=_registry(),
    )
    guard = apply_claim_guard(processed)
    assert guard["removed_claim_count"] == 1  # uncited safety sentence
    assert "压力应保持 5 bar。" in guard["answer"]
    assert "必须佩戴防护装备。" not in guard["answer"]
    assert guard["status"] == "answered"


def test_claim_guard_all_pruned_becomes_refusal() -> None:
    processed = process_sentences(
        "维修前必须切断电源。 启动前必须检查联锁。",
        whitelist=set(),
        registry=_registry(),
    )
    guard = apply_claim_guard(processed)
    assert guard["removed_claim_count"] == 2
    assert guard["status"] == "insufficient_evidence"
    assert guard["empty_after_pruning"] is True


# ---------------------------------------------------------------------------
# Metrics / gates
# ---------------------------------------------------------------------------


def _synthetic_gl_rows() -> tuple[list[dict], list[dict]]:
    gold_pages, _mapped = _gold_pages_and_mapped()
    answerable = [q for q in gold_pages if q not in ("N001", "N002")]
    gl0: list[dict] = []
    gl1: list[dict] = []
    for q in answerable:
        expected = gold_pages[q]
        doc, page = next(iter(expected))
        gl0.append(
            {
                "question_id": q,
                "answer": "回答",
                "citations": [{"chunk_id": "x", "document_name": doc, "page": page}],
                "refusal": False,
                "processed": {
                    "marker_stats": {},
                    "coverage": {
                        "key_claims": 1,
                        "covered_key_claims": 1,
                        "by_type": {"safety": [1, 1]},
                    },
                    "sentences": [],
                },
                "repair_attempted": False,
                "repair_tokens": {},
                "answer_text_unchanged": True,
                "llm_called": True,
                "actual_model": ["qwen-plus-2025-07-28"],
                "input_tokens": 1,
                "output_tokens": 1,
                "total_tokens": 2,
                "answer_latency": 1.0,
                "total_latency": 1.0,
                "status": "ok",
                "cache_hit": False,
            }
        )
        gl1.append(
            {
                "question_id": q,
                "answer": "回答",
                "citations": [{"chunk_id": "x", "document_name": doc, "page": page}],
                "refusal": False,
                "processed": {
                    "marker_stats": {"total_markers": 1, "valid_markers": 1, "invalid_chunk_markers": 0, "malformed_markers": 0},
                    "coverage": {"key_claims": 1, "covered_key_claims": 1, "by_type": {"safety": [1, 1]}},
                    "sentences": [{"detected_types": ["safety"], "valid_citation_count": 1, "clean_sentence": "安全句"}],
                },
                "repair_attempted": False,
                "repair_tokens": {},
                "answer_text_unchanged": True,
                "llm_called": True,
                "actual_model": ["qwen-plus-2025-07-28"],
                "input_tokens": 1,
                "output_tokens": 1,
                "total_tokens": 2,
                "answer_latency": 1.0,
                "total_latency": 1.0,
                "status": "ok",
                "cache_hit": False,
            }
        )
    for q in ("N001", "N002"):
        for group in (gl0, gl1):
            group.append(
                {
                    "question_id": q,
                    "answer": "现有资料不足以回答该问题。",
                    "citations": [],
                    "refusal": True,
                    "processed": {"marker_stats": {}, "coverage": {"key_claims": 0, "covered_key_claims": 0, "by_type": {}}, "sentences": []},
                    "repair_attempted": False,
                    "repair_tokens": {},
                    "answer_text_unchanged": True,
                    "llm_called": False,
                    "actual_model": [],
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "answer_latency": 0,
                    "total_latency": 0,
                    "status": "ok",
                    "cache_hit": False,
                }
            )
    return gl0, gl1


def test_metrics_denominators_48_and_2() -> None:
    gl0, gl1 = _synthetic_gl_rows()
    gold_pages, mapped = _gold_pages_and_mapped()
    m0 = citation_metrics(gl0, gold_pages=gold_pages, mapped=mapped)
    m1 = citation_metrics(gl1, gold_pages=gold_pages, mapped=mapped)
    assert m0["universe"]["answerable_questions"] == 48
    assert m0["universe"]["negative_questions"] == 2
    assert m0["answer_citation_accuracy"]["denominator"] == 48
    assert m0["insufficient_evidence_rejection_rate"]["denominator"] == 2
    assert m1["non_gold_citation_reference_rate"]["decimal"] == 0
    assert m1["gold_citation_reference_rate"]["decimal"] == 1.0


def test_marker_metrics_raw_counts() -> None:
    _, gl1 = _synthetic_gl_rows()
    marker = marker_and_coverage_metrics(gl1)
    assert marker["key_claim_citation_coverage"]["decimal"] == 1.0
    assert marker["safety_claim_citation_coverage"]["decimal"] == 1.0
    assert marker["uncited_safety_claim_count"] == 0


def test_replacement_gates_fail_without_value() -> None:
    gl0, gl1 = _synthetic_gl_rows()
    gold_pages, mapped = _gold_pages_and_mapped()
    g0 = {
        "citation": citation_metrics(gl0, gold_pages=gold_pages, mapped=mapped),
        "marker": marker_and_coverage_metrics(gl0),
        "engineering": {"total_latency_p95": 4.0, "fallback_count": 0},
        "categories": {"参数查询": {"citation_accuracy": 0.9}},
        "safety": {"citation_accuracy": 1.0, "wrong_citation_questions": 0},
        "rows": gl0,
    }
    g1 = {
        "citation": citation_metrics(gl1, gold_pages=gold_pages, mapped=mapped),
        "marker": marker_and_coverage_metrics(gl1),
        "engineering": {"total_latency_p95": 5.0, "fallback_count": 0},
        "categories": {"参数查询": {"citation_accuracy": 0.9}},
        "safety": {"citation_accuracy": 1.0, "wrong_citation_questions": 0},
        "rows": gl1,
    }
    gates = replacement_gates(gl0=g0, candidate=g1, safety0=g0["safety"], safety_c=g1["safety"])
    assert gates["hard_passed"] is True
    assert gates["value_passed"] is False
    assert gates["replacement_approved"] is False


# ---------------------------------------------------------------------------
# API compatibility
# ---------------------------------------------------------------------------


def test_public_api_contract_unchanged() -> None:
    from dataclasses import fields

    from app.api_client import ApiQueryResult

    names = {field.name for field in fields(ApiQueryResult)}
    assert {"request_id", "status", "answer", "citations"} <= names


def test_lite_defaults_all_disabled() -> None:
    frozen = json.loads(
        (PHASE5B_ROOT / "config" / "frozen_common.json").read_text(encoding="utf-8")
    )
    assert frozen["context_strategy"] == "current_rows"
    assert frozen["grounded_answer_lite_enabled"] is False
    assert frozen["inline_chunk_citation_enabled"] is False
    assert frozen["citation_only_repair_enabled"] is False
    assert frozen["claim_level_guard_enabled"] is False

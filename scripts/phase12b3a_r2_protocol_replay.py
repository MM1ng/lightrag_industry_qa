"""Phase 12B-3A-R2 one-call compact Semantic Judge protocol replay."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from industrial_rag.semantic_judge import SemanticSupport, build_batch_judge_input
from industrial_rag.semantic_judge_contract import (
    ContractParseResult,
    build_compact_semantic_judge_prompt,
    parse_compact_batch_judgement,
)
from phase12b3a_run_replay import _load_env_file, load_jsonl


R1_ROWS = ROOT / "evaluation/phase12b3a_r1/hydrated_runtime_rows.jsonl"
OUTPUT = ROOT / "evaluation/phase12b3a_r2"


@dataclass(frozen=True, slots=True)
class RawJudgeResponse:
    content: str
    finish_reason: str | None = None


JudgeCall = Callable[[str], str | RawJudgeResponse]


def matrix_fingerprint(rows: Sequence[Mapping[str, Any]]) -> str:
    matrix: list[dict[str, Any]] = []
    for row in rows:
        response = row.get("response") or {}
        matrix.append(
            {
                "question_id": str(row.get("question_id") or ""),
                "claims": [
                    {"claim_id": item.get("claim_id"), "text": item.get("text")}
                    for item in response.get("claims", []) or []
                ],
                "evidence": [
                    {
                        "evidence_id": item.get("evidence_id"),
                        "citation_id": item.get("citation_id"),
                        "chunk_id": item.get("chunk_id"),
                        "excerpt": item.get("excerpt"),
                        "document_name": item.get("document_name"),
                        "page": item.get("page"),
                    }
                    for item in response.get("evidence", []) or []
                ],
            }
        )
    encoded = json.dumps(matrix, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def classify_protocol_gate(
    *,
    total_calls: int,
    valid_batches: int,
    expected_pairs: int,
    valid_pairs: int,
) -> dict[str, Any]:
    valid_batch_rate = valid_batches / total_calls if total_calls else 0.0
    valid_pair_coverage = valid_pairs / expected_pairs if expected_pairs else 0.0
    return {
        "threshold_valid_batch_rate": 0.95,
        "threshold_valid_pair_coverage": 0.95,
        "valid_batch_rate": valid_batch_rate,
        "valid_pair_coverage": valid_pair_coverage,
        "pass": valid_batch_rate >= 0.95 and valid_pair_coverage >= 0.95,
    }


def _normalize_response(value: str | RawJudgeResponse) -> RawJudgeResponse:
    if isinstance(value, RawJudgeResponse):
        return value
    return RawJudgeResponse(content=str(value))


def _fallback(claim_ids: Sequence[str], evidence_ids: Sequence[str]) -> dict[tuple[str, str], SemanticSupport]:
    return {
        (str(claim_id), str(evidence_id)): SemanticSupport.UNCERTAIN
        for claim_id in claim_ids
        for evidence_id in evidence_ids
    }


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(dict(row), ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def run_protocol_replay(
    *,
    judge: JudgeCall | None = None,
    rows: Sequence[Mapping[str, Any]] | None = None,
    output_dir: Path = OUTPUT,
) -> dict[str, Any]:
    replay_rows = [dict(row) for row in rows] if rows is not None else load_jsonl(R1_ROWS)
    if not all(row.get("split") == "development" for row in replay_rows):
        raise ValueError("R2 accepts Development rows only")
    output_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = matrix_fingerprint(replay_rows)
    if judge is None:
        judge = build_current_r2_judge()

    raw_records: list[dict[str, Any]] = []
    invalid_records: list[dict[str, Any]] = []
    judgements_by_question: dict[str, dict[tuple[str, str], SemanticSupport]] = {}
    total_calls = 0
    valid_batches = 0
    expected_pairs = 0
    valid_pairs = 0
    input_errors = 0

    for row in replay_rows:
        question_id = str(row.get("question_id") or "")
        response = row.get("response") or {}
        claims = list(response.get("claims", []) or [])
        evidence = list(response.get("evidence", []) or [])
        claim_ids = tuple(str(item.get("claim_id")) for item in claims if item.get("claim_id"))
        evidence_ids = tuple(str(item.get("evidence_id")) for item in evidence if item.get("evidence_id"))
        expected = len(claim_ids) * len(evidence_ids)
        expected_pairs += expected
        if not claims or not evidence:
            judgements_by_question[question_id] = {}
            raw_records.append(
                {
                    "source_run": "phase12b3a_r2",
                    "question_id": question_id,
                    "call_made": False,
                    "status": "no_final_claims",
                    "raw_response_available": False,
                    "raw_response": None,
                    "finish_reason": None,
                    "prompt_token_estimate": None,
                    "response_length_chars": None,
                    "expected_pair_count": expected,
                    "returned_pair_count": 0,
                    "subtypes": [],
                    "error": None,
                }
            )
            continue
        try:
            payload = build_batch_judge_input(claims=claims, candidate_evidence=evidence)
        except ValueError as error:
            input_errors += 1
            judgements_by_question[question_id] = _fallback(claim_ids, evidence_ids)
            raw_records.append(
                {
                    "source_run": "phase12b3a_r2",
                    "question_id": question_id,
                    "call_made": False,
                    "status": "input_error",
                    "raw_response_available": False,
                    "raw_response": None,
                    "finish_reason": None,
                    "prompt_token_estimate": None,
                    "response_length_chars": None,
                    "expected_pair_count": expected,
                    "returned_pair_count": 0,
                    "subtypes": ["other"],
                    "error": str(error),
                }
            )
            continue

        prompt = build_compact_semantic_judge_prompt(payload)
        total_calls += 1
        finish_reason: str | None = None
        try:
            if judge is None:
                raise RuntimeError("current LLM is not configured")
            response_value = _normalize_response(judge(prompt))
            raw = response_value.content
            finish_reason = response_value.finish_reason
            parsed = parse_compact_batch_judgement(raw, claim_ids=claim_ids, evidence_ids=evidence_ids)
        except Exception as error:  # noqa: BLE001 - protocol audit records provider failures
            raw = ""
            parsed = ContractParseResult(
                valid=False,
                judgements=_fallback(claim_ids, evidence_ids),
                error=str(error),
                subtypes=("provider_error",),
                returned_pair_count=0,
            )

        status = "valid" if parsed.valid else "invalid"
        if parsed.valid:
            valid_batches += 1
            valid_pairs += expected
        judgements_by_question[question_id] = parsed.judgements
        record = {
            "source_run": "phase12b3a_r2",
            "question_id": question_id,
            "call_made": True,
            "status": status,
            "raw_response_available": bool(raw),
            "raw_response": raw,
            "finish_reason": finish_reason,
            "prompt_token_estimate": max(1, len(prompt) // 4),
            "response_length_chars": len(raw),
            "expected_pair_count": expected,
            "returned_pair_count": parsed.returned_pair_count,
            "subtypes": list(parsed.subtypes),
            "error": parsed.error,
        }
        raw_records.append(record)
        if not parsed.valid:
            invalid_records.append(record)

    gate = classify_protocol_gate(
        total_calls=total_calls,
        valid_batches=valid_batches,
        expected_pairs=expected_pairs,
        valid_pairs=valid_pairs,
    )
    report = {
        "phase": "12B-3A-R2",
        "status": "PROTOCOL_PASS" if gate["pass"] else "OUTPUT_CONTRACT_FAIL",
        "candidate_matrix_fingerprint": fingerprint,
        "question_count": len(replay_rows),
        "candidate_evidence_count": sum(len((row.get("response") or {}).get("evidence", []) or []) for row in replay_rows),
        "expected_pair_count": expected_pairs,
        "total_llm_calls": total_calls,
        "valid_batches": valid_batches,
        "invalid_batches": len(invalid_records),
        "input_errors": input_errors,
        "valid_judged_pairs": valid_pairs,
        "protocol_gate": {
            **gate,
            "total_llm_calls": total_calls,
            "valid_batches": valid_batches,
            "invalid_batches": len(invalid_records),
            "expected_pairs": expected_pairs,
            "valid_judged_pairs": valid_pairs,
        },
        "provider_structured_output": "not used; OpenAI SDK 2.46.0 exposes a generic response_format parameter, but the current Qwen-compatible project call has no existing provider-validated JSON Schema path; R2 uses strict JSON prompt plus deterministic validation",
        "retrieval_executed": False,
        "candidate_set_changed": False,
        "_judgements_by_question": judgements_by_question,
        "_invalid_records": invalid_records,
    }
    _write_jsonl(output_dir / "raw_judge_responses.jsonl", raw_records)
    (output_dir / "protocol_summary.json").write_text(
        json.dumps({key: value for key, value in report.items() if not key.startswith("_")}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def _score_after_protocol_gate(
    replay: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    output_dir: Path,
) -> dict[str, Any]:
    """Score only after the compact output protocol has passed."""

    from phase12b3a_run_replay import (
        B1_DIFF,
        B1_METRICS,
        B2_METRICS,
        FUNNEL,
        _error_type,
        _metrics,
        _oracle_support_by_claim,
        _question_summary,
        load_jsonl as load_replay_jsonl,
        project_semantic_citations,
    )

    funnel = load_replay_jsonl(FUNNEL)
    points_by_question: dict[str, list[dict[str, Any]]] = {}
    for point in funnel:
        points_by_question.setdefault(str(point["question_id"]), []).append(point)
    b1 = json.loads(B1_METRICS.read_text(encoding="utf-8"))
    b2 = json.loads(B2_METRICS.read_text(encoding="utf-8"))
    b1_diff = {str(item["question_id"]): item for item in load_replay_jsonl(B1_DIFF)}
    projected_by_question: dict[str, dict[str, Any]] = {}
    matrix_rows: list[dict[str, Any]] = []
    question_summaries: list[dict[str, Any]] = []
    for row in rows:
        question_id = str(row["question_id"])
        response = row.get("response", {})
        judgements = replay["_judgements_by_question"].get(question_id, {})
        projected = project_semantic_citations(response, judgements)
        projected_by_question[question_id] = projected
        oracle_support = _oracle_support_by_claim(question_id, points_by_question.get(question_id, ()))
        for claim in response.get("claims", []) or []:
            claim_id = str(claim.get("claim_id") or "")
            for evidence_item in response.get("evidence", []) or []:
                evidence_id = str(evidence_item.get("evidence_id") or "")
                judgement = judgements.get((claim_id, evidence_id), SemanticSupport.UNCERTAIN)
                oracle_supported = str(evidence_item.get("chunk_id") or "") in oracle_support.get(claim_id, set())
                correctness, error_type = _error_type(judgement, oracle_supported)
                matrix_rows.append(
                    {
                        "question_id": question_id,
                        "claim_id": claim_id,
                        "evidence_id": evidence_id,
                        "claim_text": claim.get("text", ""),
                        "evidence_excerpt": evidence_item.get("excerpt", ""),
                        "semantic_judgement": judgement.value,
                        "oracle_support_label": "supported" if oracle_supported else "not_supported",
                        "correct_or_error": correctness,
                        "error_type": error_type,
                        "judge_status": "judged",
                        "judge_error": None,
                    }
                )
        question_summaries.append(
            _question_summary(question_id, response, projected, b1_diff, points_by_question.get(question_id, ()))
        )

    semantic = _metrics(rows, points_by_question, projected_by_question)
    baseline = b2["baseline"]
    guardrails = {
        "supporting_recall_not_lower": semantic["supporting_recall"]["value"] >= baseline["supporting_recall"]["value"],
        "question_citation_accuracy_not_lower": semantic["question_citation_accuracy"]["value"] >= baseline["question_citation_accuracy"]["value"],
        "expected_coverage_not_lower": semantic["expected_coverage"]["value"] >= baseline["expected_coverage"]["value"],
        "no_new_missing_citation": semantic["questions_with_missing_citation"] <= baseline["questions_with_missing_citation"],
        "not_delete_all_citations": semantic["average_citations_per_answered_question"] > 0,
    }
    success = {
        "semantic_precision_at_least_0_70": semantic["citation_precision"]["value"] >= 0.70,
        "all_guardrails_pass": all(guardrails.values()),
    }
    _write_jsonl(output_dir / "semantic_judge_diff.jsonl", matrix_rows)
    _write_jsonl(output_dir / "question_citation_summary.jsonl", question_summaries)
    (output_dir / "semantic_judge_metrics.json").write_text(
        json.dumps(
            {
                "baseline": baseline,
                "runtime_lexical": b2["runtime_experiment"],
                "semantic_judge": semantic,
                "oracle_upper_bound": b1["experiment_a"],
                "semantic_to_oracle_gap": b1["experiment_a"]["citation_precision"]["value"] - semantic["citation_precision"]["value"],
                "guardrails": guardrails,
                "success_standard": success,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "semantic_quality_metrics_computed": True,
        "eligible_baseline": baseline,
        "eligible_runtime_lexical": b2["runtime_experiment"],
        "eligible_semantic_judge": semantic,
        "eligible_oracle_upper_bound": b1["experiment_a"],
        "semantic_to_oracle_gap": b1["experiment_a"]["citation_precision"]["value"] - semantic["citation_precision"]["value"],
        "guardrails": guardrails,
        "success_standard": success,
        "semantic_status": "PASS" if all(success.values()) else "SEMANTIC_FAIL",
    }


def _write_r2_report(report: Mapping[str, Any], path: Path) -> None:
    lines = [
        "# Phase 12B-3A-R2 Semantic Judge Output Contract Stabilization",
        "",
        f"## Status: {report['status']}",
        "",
        "R2 只改变 Judge 输出 serialization contract，未改变 Semantic Support 定义、Runtime candidate matrix、模型、检索或线上链路。",
        "",
        "## Protocol Gate",
        "",
        f"- LLM calls：{report['total_llm_calls']}",
        f"- valid batches：{report['valid_batches']}",
        f"- invalid batches：{report['invalid_batches']}",
        f"- expected pairs：{report['expected_pair_count']}",
        f"- valid judged pairs：{report['valid_judged_pairs']}",
        f"- valid batch rate：{report['protocol_gate']['valid_batch_rate']:.4f}",
        f"- valid pair coverage：{report['protocol_gate']['valid_pair_coverage']:.4f}",
        f"- gate：`{report['protocol_gate']['pass']}`",
        "",
        f"Provider structured output：{report['provider_structured_output']}",
        "",
    ]
    if report.get("semantic_quality_metrics_computed"):
        semantic = report["eligible_semantic_judge"]
        lines.extend(
            [
                "## Semantic Metrics",
                "",
                f"- Citation Precision：{semantic['citation_precision']['numerator']}/{semantic['citation_precision']['denominator']} = {semantic['citation_precision']['value']:.4f}",
                f"- Supporting Recall：{semantic['supporting_recall']['numerator']}/{semantic['supporting_recall']['denominator']} = {semantic['supporting_recall']['value']:.4f}",
                f"- Question Citation Accuracy：{semantic['question_citation_accuracy']['numerator']}/{semantic['question_citation_accuracy']['denominator']} = {semantic['question_citation_accuracy']['value']:.4f}",
                f"- Expected Coverage：{semantic['expected_coverage']['numerator']}/{semantic['expected_coverage']['denominator']} = {semantic['expected_coverage']['value']:.4f}",
                f"- Semantic → Oracle gap：{report['semantic_to_oracle_gap']:.4f}",
            ]
        )
    else:
        lines.append("Protocol Gate 未通过，未计算 Semantic Citation Quality 指标。")
    lines.extend(
        [
            "",
            "## R1 Audit Boundary",
            "",
            "R1 未保存 provider 原始响应；R1 的 26 个 invalid batch 只能依据已记录的 deterministic parser error 审计，原始响应相关字段保持 null。",
            "",
            "## Non-target Verification",
            "",
            "- 未执行 Retrieval、Rerank、Context assembly 或 Generation。",
            "- 未接入 Runtime API。",
            "- 未读取 Validation、Holdout 或 Golden label 作为 Judge 输入。",
            "- R2 每个 batch 只调用一次，不执行 retry。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_r2(
    *,
    judge: JudgeCall | None = None,
    rows: Sequence[Mapping[str, Any]] | None = None,
    output_dir: Path = OUTPUT,
    run_historical_audit: bool = True,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if run_historical_audit:
        from phase12b3a_r2_audit_r1_invalid import write_audit

        historical_audit = write_audit(output_dir=output_dir)
    else:
        historical_audit = {}
    replay = run_protocol_replay(judge=judge, rows=rows, output_dir=output_dir)
    historical_rows = load_jsonl(output_dir / "invalid_response_audit.jsonl") if (output_dir / "invalid_response_audit.jsonl").exists() else []
    all_invalid = historical_rows + list(replay.get("_invalid_records", []))
    _write_jsonl(output_dir / "invalid_response_audit.jsonl", all_invalid)
    subtype_distribution: dict[str, int] = {}
    for row in all_invalid:
        for subtype in row.get("subtypes", []) or [row.get("subtype")]:
            if subtype:
                subtype_distribution[str(subtype)] = subtype_distribution.get(str(subtype), 0) + 1
    (output_dir / "invalid_response_summary.json").write_text(
        json.dumps(
            {
                "r1_historical_audit": historical_audit,
                "r2_invalid_batch_count": len(replay.get("_invalid_records", [])),
                "combined_invalid_batch_count": len(all_invalid),
                "subtype_distribution": dict(sorted(subtype_distribution.items())),
                "r1_raw_response_limit": "R1 raw responses were not persisted; null fields are intentional.",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    result = {key: value for key, value in replay.items() if not key.startswith("_")}
    result["r1_historical_audit"] = historical_audit
    result["semantic_quality_metrics_computed"] = False
    if replay["protocol_gate"]["pass"]:
        scored = _score_after_protocol_gate(replay, rows or load_jsonl(R1_ROWS), output_dir)
        result.update(scored)
        result["status"] = scored["semantic_status"]
    else:
        result["status"] = "OUTPUT_CONTRACT_FAIL"
    (output_dir / "r2_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path = output_dir / "phase-12b3a-r2-report.md"
    _write_r2_report(result, report_path)
    if output_dir == OUTPUT:
        (ROOT / "docs/phase-12b3a-r2-semantic-judge-contract-report.md").write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
    return result


def build_current_r2_judge() -> JudgeCall | None:
    """Use the existing OpenAI-compatible client without adding provider features."""

    _load_env_file()
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        return None
    from openai import OpenAI

    client = OpenAI(
        api_key=api_key,
        base_url=os.environ.get(
            "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/"),
    )
    model = os.environ.get("LLM_MODEL", "qwen-plus-2025-07-28").strip()

    def call(prompt: str) -> RawJudgeResponse:
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": "你是严格的 Claim-Evidence 支持判断器，只能依据输入证据并返回紧凑 JSON。",
                },
                {"role": "user", "content": prompt},
            ],
        )
        choice = response.choices[0]
        content = choice.message.content
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("semantic judge returned empty content")
        return RawJudgeResponse(content=content, finish_reason=getattr(choice, "finish_reason", None))

    return call


if __name__ == "__main__":
    result = run_r2()
    print(json.dumps({key: value for key, value in result.items() if not key.startswith("_")}, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "PASS" else 2)

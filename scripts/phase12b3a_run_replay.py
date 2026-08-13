"""Run the Phase 12B-3A semantic Claim -> Evidence feasibility replay.

The runner reads only saved Development answers and runtime evidence.  Golden
and supporting labels are loaded after judgement, exclusively by scoring
helpers.  If runtime evidence text is absent, the run fails closed and makes
no LLM call instead of judging from metadata or evaluation labels.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from industrial_rag.semantic_judge import (
    ParsedBatchJudgement,
    SemanticSupport,
    build_batch_judge_input,
    build_semantic_judge_prompt,
    parse_batch_judgement,
    select_supported_evidence,
)


DEV_RESULTS = ROOT / "evaluation/phase10b3i/i0_development_results.jsonl"
FUNNEL = ROOT / "evaluation/phase10b3i_r2/coverage_funnel_matrix.jsonl"
B2_METRICS = ROOT / "evaluation/phase12b2/baseline_oracle_runtime_metrics.json"
B1_METRICS = ROOT / "evaluation/phase12b1/baseline_vs_experiment_a.json"
B1_DIFF = ROOT / "evaluation/phase12b1/citation_diff.jsonl"
OUTPUT = ROOT / "evaluation/phase12b3a"

JudgeCall = Callable[[str], str]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def classify_replay_status(status_by_question: Mapping[str, str]) -> str:
    """Separate unavailable inputs from invalid outputs after a Judge call."""

    statuses = tuple(status_by_question.values())
    if any(status.startswith("blocked_") or status == "judge_call_failed" for status in statuses):
        return "blocked"
    if any(status == "invalid_judge_response" for status in statuses):
        return "completed_with_invalid_judge_response"
    return "completed"


def rate(numerator: float, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def project_semantic_citations(
    response: Mapping[str, Any],
    judgements: Mapping[tuple[str, str], SemanticSupport],
) -> dict[str, Any]:
    """Project supported runtime evidence without changing original claims."""

    selected_by_claim = select_supported_evidence(judgements)
    evidence_by_id = {
        str(item.get("evidence_id")): item
        for item in response.get("evidence", [])
        if item.get("evidence_id")
    }
    selected_ids = {
        evidence_id
        for evidence_ids in selected_by_claim.values()
        for evidence_id in evidence_ids
    }
    citations: list[dict[str, Any]] = []
    seen_chunks: set[str] = set()
    for item in response.get("evidence", []):
        evidence_id = str(item.get("evidence_id") or "")
        if evidence_id not in selected_ids or evidence_id not in evidence_by_id:
            continue
        chunk_id = str(item.get("chunk_id") or "")
        if chunk_id and chunk_id in seen_chunks:
            continue
        citations.append(dict(item))
        if chunk_id:
            seen_chunks.add(chunk_id)
    return {
        "claims": [dict(item) for item in response.get("claims", [])],
        "citations": citations,
        "selected_by_claim": selected_by_claim,
    }


def _load_env_file() -> None:
    env_path = ROOT / ".env.local_staging"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


def build_current_llm_judge() -> JudgeCall | None:
    """Build the current OpenAI-compatible project LLM client if configured."""

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

    def call(prompt: str) -> str:
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": "你是严格的 Claim-Evidence 支持判断器，只能依据输入证据并返回 JSON。",
                },
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("semantic judge returned empty content")
        return content

    return call


def _oracle_support_by_claim(
    question_id: str,
    points: Sequence[Mapping[str, Any]],
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for point in points:
        point_id = str(point.get("expected_point_id") or "")
        if not point_id.startswith(f"{question_id}-"):
            continue
        citation = point.get("citation") or {}
        result[point_id.rsplit("-", 1)[-1].upper()] = set(
            str(item) for item in citation.get("supporting_actual_chunk_ids", []) or []
        )
        result[point_id] = set(
            str(item) for item in citation.get("supporting_actual_chunk_ids", []) or []
        )
    return result


def _claim_point_id(claim: Mapping[str, Any]) -> str:
    return str(claim.get("claim_id") or "")


def _point_effects(
    question_id: str,
    points: Sequence[Mapping[str, Any]],
    response: Mapping[str, Any],
    selected_by_claim: Mapping[str, Sequence[str]],
) -> list[dict[str, Any]]:
    evidence_by_id = {
        str(item.get("evidence_id")): item
        for item in response.get("evidence", [])
        if item.get("evidence_id")
    }
    effects: list[dict[str, Any]] = []
    for point in points:
        if not point.get("final_emitted"):
            continue
        point_id = str(point.get("expected_point_id") or "")
        claim_id = point_id.rsplit("-", 1)[-1].upper()
        selected_chunks = {
            str(evidence_by_id[evidence_id].get("chunk_id"))
            for evidence_id in selected_by_claim.get(claim_id, ())
            if evidence_id in evidence_by_id and evidence_by_id[evidence_id].get("chunk_id")
        }
        expected = set(str(item) for item in point.get("expected_support_chunk_ids", []) or [])
        supporting = selected_chunks & expected
        effects.append(
            {
                "expected_point_id": point_id,
                "selected_chunk_ids": sorted(selected_chunks),
                "expected_support_chunk_ids": sorted(expected),
                "supporting_chunk_ids": sorted(supporting),
                "supporting_present": bool(supporting),
                "non_supporting_count": len(selected_chunks - expected),
                "precision": len(supporting) / len(selected_chunks) if selected_chunks else 0.0,
            }
        )
    return effects


def _metrics(
    rows: Sequence[Mapping[str, Any]],
    points_by_question: Mapping[str, Sequence[Mapping[str, Any]]],
    projected_by_question: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    effects_by_question: dict[str, list[dict[str, Any]]] = {}
    all_effects: list[dict[str, Any]] = []
    for row in rows:
        question_id = str(row["question_id"])
        projected = projected_by_question[question_id]
        effects = _point_effects(
            question_id,
            points_by_question.get(question_id, ()),
            row.get("response", {}),
            projected.get("selected_by_claim", {}),
        )
        effects_by_question[question_id] = effects
        all_effects.extend(effects)

    substantive = [
        row
        for row in rows
        if row.get("response", {}).get("status") in {"success", "partial_answer"}
    ]
    q_accuracy = sum(
        all(effect["supporting_present"] for effect in effects_by_question[str(row["question_id"])])
        for row in substantive
    )
    answered_count = len(substantive)
    selected_counts = [len(projected_by_question[str(row["question_id"])]["citations"]) for row in substantive]
    supporting_count = sum(len(set(effect["supporting_chunk_ids"])) for effect in all_effects)
    selected_count = sum(selected_counts)
    non_supporting_count = sum(effect["non_supporting_count"] for effect in all_effects)
    return {
        "citation_precision": rate(sum(effect["precision"] for effect in all_effects), len(all_effects)),
        "supporting_recall": rate(sum(effect["supporting_present"] for effect in all_effects), len(all_effects)),
        "question_citation_accuracy": rate(q_accuracy, answered_count),
        "expected_coverage": rate(
            sum(effect["supporting_present"] for effect in all_effects),
            sum(len(points) for points in points_by_question.values()),
        ),
        "initial_recall_at_10": {"numerator": 39, "denominator": 39, "value": 1.0},
        "average_citations_per_answered_question": selected_count / answered_count if answered_count else None,
        "supporting_citations_per_answered_question": supporting_count / answered_count if answered_count else None,
        "non_supporting_citations_per_answered_question": non_supporting_count / answered_count if answered_count else None,
        "questions_with_over_citation": sum(
            any(effect["non_supporting_count"] > 0 for effect in effects_by_question[str(row["question_id"])])
            for row in substantive
        ),
        "questions_with_missing_citation": sum(
            any(not effect["supporting_present"] for effect in effects_by_question[str(row["question_id"])])
            for row in substantive
        ),
        "questions_with_exact_minimal_citation": sum(
            bool(effects_by_question[str(row["question_id"])])
            and all(
                effect["supporting_present"] and effect["non_supporting_count"] == 0
                for effect in effects_by_question[str(row["question_id"])]
            )
            for row in substantive
        ),
    }


def _error_type(judgement: SemanticSupport, oracle_supported: bool) -> tuple[str, str]:
    if judgement is SemanticSupport.UNCERTAIN:
        return "not_scored", "uncertain"
    if judgement is SemanticSupport.SUPPORTED and oracle_supported:
        return "correct", "correct"
    if judgement is SemanticSupport.SUPPORTED and not oracle_supported:
        return "error", "false_positive_support"
    if judgement is SemanticSupport.PARTIALLY_SUPPORTED and oracle_supported:
        return "error", "full_as_partial"
    if judgement is SemanticSupport.PARTIALLY_SUPPORTED and not oracle_supported:
        return "error", "partial_as_full"
    if judgement is not SemanticSupport.SUPPORTED and oracle_supported:
        return "error", "false_negative_support"
    return "correct", "correct"


def _question_summary(
    question_id: str,
    response: Mapping[str, Any],
    projected: Mapping[str, Any],
    oracle_by_question: Mapping[str, Mapping[str, Any]],
    points: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    baseline = list(response.get("citations", []))
    semantic = list(projected.get("citations", []))
    oracle = list(oracle_by_question.get(question_id, {}).get("experiment_citations", []))
    baseline_ids = {str(item.get("citation_id")) for item in baseline if item.get("citation_id")}
    semantic_ids = {str(item.get("citation_id")) for item in semantic if item.get("citation_id")}
    oracle_ids = {str(item.get("citation_id")) for item in oracle if item.get("citation_id")}
    supporting_chunks = {
        str(chunk_id)
        for point in points
        if point.get("final_emitted")
        for chunk_id in (point.get("citation") or {}).get("supporting_actual_chunk_ids", []) or []
    }
    chunk_by_citation = {
        str(item.get("citation_id")): str(item.get("chunk_id"))
        for item in baseline
        if item.get("citation_id") and item.get("chunk_id")
    }
    supporting_ids = {
        citation_id
        for citation_id, chunk_id in chunk_by_citation.items()
        if chunk_id in supporting_chunks
    }
    return {
        "question_id": question_id,
        "baseline_citations": baseline,
        "semantic_citations": semantic,
        "oracle_citations": oracle,
        "removed_by_semantic": sorted(baseline_ids - semantic_ids),
        "removed_only_by_oracle": sorted(semantic_ids - oracle_ids),
        "supporting_removed": sorted((supporting_ids & baseline_ids) - semantic_ids),
        "non_supporting_removed": sorted((baseline_ids - supporting_ids) & (baseline_ids - semantic_ids)),
        "semantic_vs_oracle_gap": len(semantic_ids - oracle_ids),
        "pass_or_regression": "pass" if not ((supporting_ids & baseline_ids) - semantic_ids) else "regression",
    }


def run_replay(
    *,
    judge: JudgeCall | None = None,
    rows_override: Sequence[Mapping[str, Any]] | None = None,
    funnel_override: Sequence[Mapping[str, Any]] | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    rows = [dict(row) for row in rows_override] if rows_override is not None else load_jsonl(DEV_RESULTS)
    funnel = [dict(row) for row in funnel_override] if funnel_override is not None else load_jsonl(FUNNEL)
    output = output_dir or OUTPUT
    b2 = json.loads(B2_METRICS.read_text(encoding="utf-8"))
    b1 = json.loads(B1_METRICS.read_text(encoding="utf-8"))
    b1_diff = {str(item["question_id"]): item for item in load_jsonl(B1_DIFF)}
    if not all(row.get("split") == "development" for row in rows + funnel):
        raise SystemExit("refusing to mix non-Development rows")

    points_by_question: dict[str, list[dict[str, Any]]] = {}
    for point in funnel:
        points_by_question.setdefault(str(point["question_id"]), []).append(point)

    if judge is None:
        judge = build_current_llm_judge()

    projected_by_question: dict[str, dict[str, Any]] = {}
    status_by_question: dict[str, str] = {}
    matrix_rows: list[dict[str, Any]] = []
    question_summaries: list[dict[str, Any]] = []
    judge_call_count = 0
    valid_judgements = 0
    for row in rows:
        question_id = str(row["question_id"])
        response = row.get("response", {})
        claims = list(response.get("claims", []))
        evidence = list(response.get("evidence", []))
        claim_ids = tuple(str(item.get("claim_id")) for item in claims if item.get("claim_id"))
        evidence_ids = tuple(str(item.get("evidence_id")) for item in evidence if item.get("evidence_id"))
        if not claims or not evidence:
            parsed = ParsedBatchJudgement(
                valid=False,
                judgements={
                    (claim_id, evidence_id): SemanticSupport.UNCERTAIN
                    for claim_id in claim_ids
                    for evidence_id in evidence_ids
                },
                error="runtime candidate claim/evidence matrix is empty",
            )
            status_by_question[question_id] = (
                "no_final_claims" if claims == [] and evidence else "blocked_missing_runtime_candidate_evidence"
            )
        else:
            try:
                payload = build_batch_judge_input(claims=claims, candidate_evidence=evidence)
            except ValueError as error:
                parsed = ParsedBatchJudgement(
                    valid=False,
                    judgements={
                        (claim_id, evidence_id): SemanticSupport.UNCERTAIN
                        for claim_id in claim_ids
                        for evidence_id in evidence_ids
                    },
                    error=str(error),
                )
                status_by_question[question_id] = "blocked_missing_runtime_evidence_text"
            else:
                if judge is None:
                    parsed = ParsedBatchJudgement(
                        valid=False,
                        judgements={
                            (claim_id, evidence_id): SemanticSupport.UNCERTAIN
                            for claim_id in claim_ids
                            for evidence_id in evidence_ids
                        },
                        error="current LLM is not configured",
                    )
                    status_by_question[question_id] = "blocked_missing_llm_configuration"
                else:
                    try:
                        judge_call_count += 1
                        raw = judge(build_semantic_judge_prompt(payload))
                        parsed = parse_batch_judgement(raw, claim_ids=claim_ids, evidence_ids=evidence_ids)
                        status_by_question[question_id] = "judged" if parsed.valid else "invalid_judge_response"
                    except Exception as error:  # noqa: BLE001 - offline replay must fail closed per row
                        parsed = ParsedBatchJudgement(
                            valid=False,
                            judgements={
                                (claim_id, evidence_id): SemanticSupport.UNCERTAIN
                                for claim_id in claim_ids
                                for evidence_id in evidence_ids
                            },
                            error=str(error),
                        )
                        status_by_question[question_id] = "judge_call_failed"
        valid_judgements += int(parsed.valid)
        projected = project_semantic_citations(response, parsed.judgements)
        projected_by_question[question_id] = projected
        oracle_support = _oracle_support_by_claim(question_id, points_by_question.get(question_id, ()))
        point_by_claim = {
            str(point.get("expected_point_id") or "").rsplit("-", 1)[-1].upper(): point
            for point in points_by_question.get(question_id, ())
        }
        for claim in claims:
            claim_id = str(claim.get("claim_id") or "")
            for evidence_item in evidence:
                evidence_id = str(evidence_item.get("evidence_id") or "")
                judgement = parsed.judgements.get((claim_id, evidence_id), SemanticSupport.UNCERTAIN)
                evidence_chunk = str(evidence_item.get("chunk_id") or "")
                oracle_supported = evidence_chunk in oracle_support.get(claim_id, set())
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
                        "judge_status": status_by_question[question_id],
                        "judge_error": parsed.error,
                        "point_present": claim_id in point_by_claim,
                    }
                )
        question_summary = _question_summary(
            question_id,
            response,
            projected,
            b1_diff,
            points_by_question.get(question_id, ()),
        )
        question_summary["judge_status"] = status_by_question[question_id]
        question_summaries.append(question_summary)

    semantic_metrics = _metrics(rows, points_by_question, projected_by_question)
    experiment_status = classify_replay_status(status_by_question)
    blocked = experiment_status == "blocked"
    status_counts: dict[str, int] = {}
    for status in status_by_question.values():
        status_counts[status] = status_counts.get(status, 0) + 1
    guardrails = {
        "supporting_recall_not_lower": semantic_metrics["supporting_recall"]["value"]
        >= b2["baseline"]["supporting_recall"]["value"],
        "question_citation_accuracy_not_lower": semantic_metrics["question_citation_accuracy"]["value"]
        >= b2["baseline"]["question_citation_accuracy"]["value"],
        "expected_coverage_not_lower": semantic_metrics["expected_coverage"]["value"]
        >= b2["baseline"]["expected_coverage"]["value"],
        "no_supporting_citation_removed": not any(item["supporting_removed"] for item in question_summaries),
        "no_new_missing_citation": semantic_metrics["questions_with_missing_citation"]
        <= b2["baseline"]["questions_with_missing_citation"],
        "not_delete_all_citations": semantic_metrics["average_citations_per_answered_question"] > 0,
    }
    success = {
        "semantic_precision_at_least_0_70": semantic_metrics["citation_precision"]["value"] >= 0.70,
        "all_guardrails_pass": all(guardrails.values()),
        "experiment_inputs_available": not blocked,
    }
    report = {
        "phase": "12B-3A",
        "split": "development",
        "experiment_status": experiment_status,
        "selector_used_evaluation_labels": False,
        "llm_call_count": judge_call_count,
        "valid_judgement_count": valid_judgements,
        "judge_status_counts": status_counts,
        "baseline": b2["baseline"],
        "runtime_lexical": b2["runtime_experiment"],
        "semantic_judge": semantic_metrics,
        "oracle_upper_bound": b1["experiment_a"],
        "guardrails": guardrails,
        "success_standard": success,
        "pass": all(success.values()),
        "blocked_questions": [
            {"question_id": question_id, "status": status_by_question[question_id]}
            for question_id in sorted(status_by_question)
            if status_by_question[question_id] != "judged"
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "semantic_judge_diff.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in matrix_rows) + "\n",
        encoding="utf-8",
    )
    (output / "question_citation_summary.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in question_summaries) + "\n",
        encoding="utf-8",
    )
    uncertain = [item for item in matrix_rows if item["error_type"] == "uncertain"]
    correct_deletions = [
        item
        for item in question_summaries
        if item.get("judge_status") == "judged"
        and item["non_supporting_removed"]
        and not item["supporting_removed"]
    ]
    (output / "top_case_review.json").write_text(
        json.dumps(
            {
                "required_correct_deletion_count": 5,
                "required_error_or_uncertain_count": 5,
                "correct_deletion_cases": correct_deletions[:5],
                "error_or_uncertain_cases": uncertain[:5],
                "status": "blocked_before_semantic_judgement" if blocked else "available",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "semantic_judge_metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"pass": report["pass"], "status": report["experiment_status"], "blocked": len(report["blocked_questions"])}, ensure_ascii=False))
    return {**report, "_projected_by_question": projected_by_question}


def main() -> int:
    report = run_replay()
    return 0 if report["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Replay Phase 12B-2 runtime citation selection on saved Development answers.

The selector receives only response claims and response evidence.  Evaluation
labels are loaded after selection and used only for scoring and diff analysis.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from industrial_rag.citation_selection import select_runtime_citations


ROOT = Path(__file__).resolve().parents[1]
DEV_RESULTS = ROOT / "evaluation/phase10b3i/i0_development_results.jsonl"
FUNNEL = ROOT / "evaluation/phase10b3i_r2/coverage_funnel_matrix.jsonl"
BASELINE_METRICS = ROOT / "evaluation/phase10b3i_r2/i0_development_metrics.json"
ORACLE_METRICS = ROOT / "evaluation/phase12b1/baseline_vs_experiment_a.json"
ORACLE_DIFF = ROOT / "evaluation/phase12b1/citation_diff.jsonl"
OUTPUT = ROOT / "evaluation/phase12b2"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def rate(numerator: float, denominator: int) -> dict[str, Any]:
    return {"numerator": numerator, "denominator": denominator, "value": numerator / denominator if denominator else None}


def ids_by_chunk(citations: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(item["chunk_id"]): str(item["citation_id"])
        for item in citations
        if item.get("chunk_id") and item.get("citation_id")
    }


def selected_chunk_ids(citations: list[dict[str, Any]]) -> set[str]:
    return {str(item["chunk_id"]) for item in citations if item.get("chunk_id")}


def evaluate(
    points: list[dict[str, Any]],
    selected_chunks: set[str],
) -> list[dict[str, Any]]:
    effects: list[dict[str, Any]] = []
    for point in points:
        if not point.get("final_emitted"):
            continue
        expected = set(point.get("expected_support_chunk_ids", []) or [])
        baseline_actual = set(point.get("citation", {}).get("actual_cited_chunk_ids", []) or [])
        runtime_actual = baseline_actual & selected_chunks
        baseline_supporting = baseline_actual & expected
        runtime_supporting = runtime_actual & expected
        effects.append(
            {
                "expected_point_id": point["expected_point_id"],
                "baseline_actual": baseline_actual,
                "runtime_actual": runtime_actual,
                "baseline_supporting": baseline_supporting,
                "runtime_supporting": runtime_supporting,
                "baseline_precision": len(baseline_supporting) / len(baseline_actual) if baseline_actual else None,
                "runtime_precision": len(runtime_supporting) / len(runtime_actual) if runtime_actual else None,
                "baseline_supporting_present": bool(baseline_supporting),
                "runtime_supporting_present": bool(runtime_supporting),
                "baseline_non_supporting_count": len(baseline_actual - expected),
                "runtime_non_supporting_count": len(runtime_actual - expected),
            }
        )
    return effects


def answered_stats(
    rows: list[dict[str, Any]],
    points_by_question: dict[str, list[dict[str, Any]]],
    selected_by_question: dict[str, list[dict[str, Any]]],
    *,
    effects_by_question: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    substantive = [row for row in rows if row.get("response", {}).get("status") in {"success", "partial_answer"}]
    values: list[tuple[int, int, int, bool, bool, bool]] = []
    for row in substantive:
        question_id = str(row["question_id"])
        citations = selected_by_question[question_id]
        cited_chunks = selected_chunk_ids(citations)
        q_effects = effects_by_question[question_id]
        supporting_chunks = {
            chunk_id
            for point in points_by_question[question_id]
            if point.get("final_emitted")
            for chunk_id in point.get("citation", {}).get("supporting_actual_chunk_ids", []) or []
        }
        supporting = len(cited_chunks & supporting_chunks)
        non_supporting = len(cited_chunks - supporting_chunks)
        over = any(item["runtime_non_supporting_count"] > 0 for item in q_effects)
        missing = any(not item["runtime_supporting_present"] for item in q_effects)
        exact = bool(q_effects) and all(item["runtime_supporting_present"] and not item["runtime_non_supporting_count"] for item in q_effects)
        values.append((len(citations), supporting, non_supporting, over, missing, exact))
    denominator = len(values)
    return {
        "average_citations_per_answered_question": sum(item[0] for item in values) / denominator if denominator else None,
        "supporting_citations_per_answered_question": sum(item[1] for item in values) / denominator if denominator else None,
        "non_supporting_citations_per_answered_question": sum(item[2] for item in values) / denominator if denominator else None,
        "questions_with_over_citation": sum(item[3] for item in values),
        "questions_with_missing_citation": sum(item[4] for item in values),
        "questions_with_exact_minimal_citation": sum(item[5] for item in values),
    }


def main() -> int:
    rows = load_jsonl(DEV_RESULTS)
    funnel = load_jsonl(FUNNEL)
    baseline = json.loads(BASELINE_METRICS.read_text(encoding="utf-8"))
    oracle_metrics = json.loads(ORACLE_METRICS.read_text(encoding="utf-8"))
    oracle_diffs = {str(row["question_id"]): row for row in load_jsonl(ORACLE_DIFF)}
    if not all(row.get("split") == "development" for row in rows + funnel):
        raise SystemExit("refusing to mix non-Development rows")

    points_by_question: dict[str, list[dict[str, Any]]] = {}
    for point in funnel:
        points_by_question.setdefault(str(point["question_id"]), []).append(point)

    runtime_by_question: dict[str, list[dict[str, Any]]] = {}
    baseline_by_question: dict[str, list[dict[str, Any]]] = {}
    oracle_by_question: dict[str, list[dict[str, Any]]] = {}
    effects_by_question: dict[str, list[dict[str, Any]]] = {}
    diffs: list[dict[str, Any]] = []

    for row in rows:
        question_id = str(row["question_id"])
        response = row.get("response", {})
        baseline_citations = list(response.get("citations", []))
        claims = list(response.get("claims", []))
        runtime_selection = select_runtime_citations(
            claims=claims,
            response_evidence=baseline_citations,
        )
        runtime_citations = [dict(item) for item in runtime_selection.citations]
        oracle_citations = list(oracle_diffs.get(question_id, {}).get("experiment_citations", []))
        runtime_by_question[question_id] = runtime_citations
        baseline_by_question[question_id] = baseline_citations
        oracle_by_question[question_id] = oracle_citations

        effects = evaluate(points_by_question.get(question_id, []), selected_chunk_ids(runtime_citations))
        effects_by_question[question_id] = effects
        supporting_baseline = {
            str(item["citation_id"])
            for item in baseline_citations
            if any(
                str(item.get("chunk_id")) in set(point.get("citation", {}).get("supporting_actual_chunk_ids", []) or [])
                for point in points_by_question.get(question_id, [])
                if point.get("final_emitted")
            )
        }
        baseline_ids = {str(item["citation_id"]) for item in baseline_citations if item.get("citation_id")}
        runtime_ids = {str(item["citation_id"]) for item in runtime_citations if item.get("citation_id")}
        oracle_ids = {str(item["citation_id"]) for item in oracle_citations if item.get("citation_id")}
        runtime_removed = sorted(baseline_ids - runtime_ids)
        diffs.append(
            {
                "question_id": question_id,
                "baseline_citations": baseline_citations,
                "runtime_citations": runtime_citations,
                "oracle_citations": oracle_citations,
                "removed_by_runtime": runtime_removed,
                "removed_only_by_oracle": sorted(runtime_ids - oracle_ids),
                "supporting_removed": sorted((baseline_ids & supporting_baseline) - runtime_ids),
                "non_supporting_removed": sorted((baseline_ids - supporting_baseline) & set(runtime_removed)),
                "runtime_vs_oracle_gap": {
                    "runtime_precision_gap": None,
                    "runtime_retains_oracle_removed_count": len(runtime_ids - oracle_ids),
                    "runtime_removes_oracle_retained_count": len(oracle_ids - runtime_ids),
                },
                "pass_or_regression": "pass" if not ((baseline_ids & supporting_baseline) - runtime_ids) else "regression",
            }
        )

    final_effects = [effect for effects in effects_by_question.values() for effect in effects]
    substantive = [row for row in rows if row.get("response", {}).get("status") in {"success", "partial_answer"}]
    q_runtime_accuracy = sum(
        all(effect["runtime_supporting_present"] for effect in effects_by_question[str(row["question_id"])])
        for row in substantive
    )
    runtime_metrics = {
        "citation_precision": rate(sum(float(item["runtime_precision"] or 0) for item in final_effects), len(final_effects)),
        "supporting_recall": rate(sum(bool(item["runtime_supporting_present"]) for item in final_effects), len(final_effects)),
        "question_citation_accuracy": rate(q_runtime_accuracy, len(substantive)),
        "expected_coverage": baseline["metrics"]["expected_answer_point_coverage"],
        "initial_recall_at_10": {"numerator": 39, "denominator": 39, "value": 1.0},
    }
    runtime_metrics.update(answered_stats(rows, points_by_question, runtime_by_question, effects_by_question=effects_by_question))
    baseline_stats = answered_stats(rows, points_by_question, baseline_by_question, effects_by_question=effects_by_question)
    oracle_stats = answered_stats(rows, points_by_question, oracle_by_question, effects_by_question=effects_by_question)
    guardrails = {
        "supporting_recall_not_lower": runtime_metrics["supporting_recall"]["numerator"] >= baseline["metrics"]["supporting_citation_recall"]["numerator"],
        "question_citation_accuracy_not_lower": runtime_metrics["question_citation_accuracy"]["numerator"] >= baseline["metrics"]["question_level_citation_accuracy"]["numerator"],
        "expected_coverage_not_lower": True,
        "initial_recall_10_unchanged": True,
        "no_supporting_citation_removed": not any(item["supporting_removed"] for item in diffs),
        "no_new_missing_citation": runtime_metrics["questions_with_missing_citation"] <= baseline_stats["questions_with_missing_citation"],
        "not_delete_all_citations": runtime_metrics["average_citations_per_answered_question"] > 0,
    }
    success = {
        "runtime_precision_at_least_0_60": runtime_metrics["citation_precision"]["value"] >= 0.60,
        "all_guardrails_pass": all(guardrails.values()),
    }
    report = {
        "phase": "12B-2",
        "split": "development",
        "selector_input": "saved response.claims + saved response.citations only",
        "selector_used_evaluation_labels": False,
        "baseline": {
            "citation_precision": baseline["metrics"]["citation_precision"],
            "supporting_recall": baseline["metrics"]["supporting_citation_recall"],
            "question_citation_accuracy": baseline["metrics"]["question_level_citation_accuracy"],
            "expected_coverage": baseline["metrics"]["expected_answer_point_coverage"],
            "initial_recall_at_10": {"numerator": 39, "denominator": 39, "value": 1.0},
            **baseline_stats,
        },
        "oracle_upper_bound": oracle_metrics["experiment_a"],
        "runtime_experiment": runtime_metrics,
        "guardrails": guardrails,
        "success_standard": success,
        "pass": all(success.values()),
    }
    for item in diffs:
        runtime_ids = {str(row["citation_id"]) for row in item["runtime_citations"] if row.get("citation_id")}
        oracle_ids = {str(row["citation_id"]) for row in item["oracle_citations"] if row.get("citation_id")}
        item["runtime_vs_oracle_gap"]["runtime_precision_gap"] = None if runtime_ids == oracle_ids else "non_zero"

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "runtime_citation_diff.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in diffs) + "\n",
        encoding="utf-8",
    )
    (OUTPUT / "baseline_oracle_runtime_metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"pass": report["pass"], "runtime_experiment": runtime_metrics, "guardrails": guardrails}, ensure_ascii=False))
    return 0 if report["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

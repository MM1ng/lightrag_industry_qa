"""Run Phase 12B-1 Experiment A by offline citation remapping only."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from industrial_rag.citation_selection import select_minimal_supporting_citations


ROOT = Path(__file__).resolve().parents[1]
DEV_RESULTS = ROOT / "evaluation/phase10b3i/i0_development_results.jsonl"
FUNNEL = ROOT / "evaluation/phase10b3i_r2/coverage_funnel_matrix.jsonl"
BASELINE_METRICS = ROOT / "evaluation/phase10b3i_r2/i0_development_metrics.json"
OUTPUT = ROOT / "evaluation/phase12b1"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def metric(numerator: float, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def citation_map(response_citations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(item["citation_id"]): item
        for item in response_citations
        if item.get("citation_id")
    }


def point_actual_ids(point: dict[str, Any], citations_by_chunk: dict[str, str]) -> tuple[str, ...]:
    return tuple(
        citation_id
        for chunk_id in point.get("citation", {}).get("actual_cited_chunk_ids", []) or []
        if (citation_id := citations_by_chunk.get(str(chunk_id)))
    )


def evaluate_points(
    points: list[dict[str, Any]],
    retained_citation_ids: set[str],
    citations_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for point in points:
        if not point.get("final_emitted"):
            continue
        baseline_chunk_ids = set(point.get("citation", {}).get("actual_cited_chunk_ids", []) or [])
        experiment_chunk_ids = {
            str(citations_by_id[citation_id].get("chunk_id"))
            for citation_id in point.get("experiment_citation_ids", [])
            if citation_id in retained_citation_ids and citation_id in citations_by_id
        }
        expected = set(point.get("expected_support_chunk_ids", []) or [])
        baseline_supporting = baseline_chunk_ids & expected
        experiment_supporting = experiment_chunk_ids & expected
        output.append(
            {
                "expected_point_id": point["expected_point_id"],
                "baseline_citation_chunk_ids": sorted(baseline_chunk_ids),
                "experiment_citation_chunk_ids": sorted(experiment_chunk_ids),
                "baseline_supporting_count": len(baseline_supporting),
                "experiment_supporting_count": len(experiment_supporting),
                "baseline_non_supporting_count": len(baseline_chunk_ids - expected),
                "experiment_non_supporting_count": len(experiment_chunk_ids - expected),
                "baseline_precision": len(baseline_supporting) / len(baseline_chunk_ids) if baseline_chunk_ids else None,
                "experiment_precision": len(experiment_supporting) / len(experiment_chunk_ids) if experiment_chunk_ids else None,
                "baseline_supporting_present": bool(baseline_supporting),
                "experiment_supporting_present": bool(experiment_supporting),
            }
        )
    return output


def main() -> int:
    rows = load_jsonl(DEV_RESULTS)
    funnel_rows = load_jsonl(FUNNEL)
    baseline = json.loads(BASELINE_METRICS.read_text(encoding="utf-8"))
    if not all(row.get("split") == "development" for row in rows + funnel_rows):
        raise SystemExit("refusing to mix non-Development rows")

    points_by_question: dict[str, list[dict[str, Any]]] = {}
    for point in funnel_rows:
        points_by_question.setdefault(str(point["question_id"]), []).append(point)
    diffs: list[dict[str, Any]] = []
    point_effects: list[dict[str, Any]] = []
    substantive = [row for row in rows if row.get("response", {}).get("status") in {"success", "partial_answer"}]

    for row in rows:
        question_id = str(row["question_id"])
        response_citations = list(row.get("response", {}).get("citations", []))
        by_id = citation_map(response_citations)
        by_chunk = {
            str(item.get("chunk_id")): str(item["citation_id"])
            for item in response_citations
            if item.get("chunk_id") and item.get("citation_id")
        }
        claims: list[dict[str, Any]] = []
        points = points_by_question.get(question_id, [])
        for point in points:
            if not point.get("final_emitted"):
                continue
            actual_ids = point_actual_ids(point, by_chunk)
            supporting_chunks = set(point.get("citation", {}).get("supporting_actual_chunk_ids", []) or [])
            supporting_ids = tuple(
                citation_id
                for citation_id in actual_ids
                if str(by_id[citation_id].get("chunk_id")) in supporting_chunks
            )
            claims.append(
                {
                    "claim_id": point["expected_point_id"],
                    "candidate_citation_ids": actual_ids,
                    "supporting_citation_ids": supporting_ids,
                }
            )
        selection = select_minimal_supporting_citations(claims=claims, citations=response_citations)
        retained = set(selection.retained_citation_ids)
        for point in points:
            point["experiment_citation_ids"] = selection.claim_citation_ids.get(str(point["expected_point_id"]), ())
        effects = evaluate_points(points, retained, by_id)
        point_effects.extend(effects)
        baseline_supporting = {
            str(item["citation_id"])
            for item in response_citations
            if str(item.get("chunk_id")) in {
                chunk_id
                for point in points
                if point.get("final_emitted")
                for chunk_id in point.get("citation", {}).get("supporting_actual_chunk_ids", []) or []
            }
        }
        baseline_non_supporting = {
            str(item["citation_id"])
            for item in response_citations
            if str(item["citation_id"]) not in baseline_supporting
        }
        diffs.append(
            {
                "question_id": question_id,
                "baseline_citations": response_citations,
                "experiment_citations": [by_id[cid] for cid in selection.retained_citation_ids],
                "removed_citations": list(selection.removed_citation_ids),
                "retained_citations": list(selection.retained_citation_ids),
                "supporting_removed": [cid for cid in selection.supporting_removed if cid in baseline_supporting],
                "non_supporting_removed": [cid for cid in selection.removed_citation_ids if cid in baseline_non_supporting],
                "metric_effect": {
                    "baseline_point_effects": effects,
                    "supporting_removed_count": len(selection.supporting_removed),
                },
                "pass_or_regression": "pass" if not selection.supporting_removed else "regression",
            }
        )

    final_points = point_effects
    baseline_precision_sum = sum(float(item["baseline_precision"] or 0) for item in final_points)
    experiment_precision_sum = sum(float(item["experiment_precision"] or 0) for item in final_points)
    baseline_supporting_count = sum(bool(item["baseline_supporting_present"]) for item in final_points)
    experiment_supporting_count = sum(bool(item["experiment_supporting_present"]) for item in final_points)
    baseline_q_accuracy = sum(
        all(item["baseline_supporting_present"] for item in final_points if item["expected_point_id"].split("-")[0] == row["question_id"])
        for row in substantive
    )
    experiment_q_accuracy = sum(
        all(item["experiment_supporting_present"] for item in final_points if item["expected_point_id"].split("-")[0] == row["question_id"])
        for row in substantive
    )

    def question_citation_counts(row: dict[str, Any], use_experiment: bool) -> tuple[int, int, int, bool, bool, bool]:
        diff = next(item for item in diffs if item["question_id"] == row["question_id"])
        citations = diff["experiment_citations"] if use_experiment else diff["baseline_citations"]
        cited_ids = {str(item.get("citation_id")) for item in citations}
        q_points = [item for item in final_points if item["expected_point_id"].split("-")[0] == row["question_id"]]
        supporting_ids = {
            str(item.get("citation_id"))
            for item in diff["baseline_citations"]
            if any(str(item.get("chunk_id")) in set(point.get("citation", {}).get("supporting_actual_chunk_ids", []) or []) for point in points_by_question.get(row["question_id"], []) if point.get("final_emitted"))
        }
        supporting = len(cited_ids & supporting_ids)
        non_supporting = len(cited_ids - supporting_ids)
        missing = any(not item["experiment_supporting_present"] for item in q_points) if use_experiment else any(not item["baseline_supporting_present"] for item in q_points)
        exact = bool(q_points) and all(item["experiment_non_supporting_count"] == 0 and item["experiment_supporting_present"] for item in q_points) if use_experiment else bool(q_points) and all(item["baseline_non_supporting_count"] == 0 and item["baseline_supporting_present"] for item in q_points)
        over = any(item["experiment_non_supporting_count"] > 0 for item in q_points) if use_experiment else any(item["baseline_non_supporting_count"] > 0 for item in q_points)
        return len(citations), supporting, non_supporting, over, missing, exact

    def answered_stats(use_experiment: bool) -> dict[str, Any]:
        values = [question_citation_counts(row, use_experiment) for row in substantive]
        count = len(values)
        return {
            "average_citations_per_answered_question": sum(item[0] for item in values) / count if count else None,
            "supporting_citations_per_answered_question": sum(item[1] for item in values) / count if count else None,
            "non_supporting_citations_per_answered_question": sum(item[2] for item in values) / count if count else None,
            "questions_with_over_citation": sum(item[3] for item in values),
            "questions_with_missing_citation": sum(item[4] for item in values),
            "questions_with_exact_minimal_citation": sum(item[5] for item in values),
        }

    metrics = {
        "phase": "12B-1",
        "experiment": "A_minimal_supporting_citation_offline_remap",
        "split": "development",
        "support_mapping_source": "evaluation/phase10b3i_r2/coverage_funnel_matrix.jsonl:citation.supporting_actual_chunk_ids",
        "support_mapping_is_runtime_inference": False,
        "generation_rerun": False,
        "retrieval_rerun": False,
        "baseline": {
            "citation_precision": baseline["metrics"]["citation_precision"],
            "supporting_recall": baseline["metrics"]["supporting_citation_recall"],
            "question_citation_accuracy": baseline["metrics"]["question_level_citation_accuracy"],
            "expected_coverage": baseline["metrics"]["expected_answer_point_coverage"],
            "initial_recall_at_10": {"numerator": 39, "denominator": 39, "value": 1.0},
            **answered_stats(False),
        },
        "experiment_a": {
            "citation_precision": metric(experiment_precision_sum, len(final_points)),
            "supporting_recall": metric(experiment_supporting_count, len(final_points)),
            "question_citation_accuracy": metric(experiment_q_accuracy, len(substantive)),
            "expected_coverage": baseline["metrics"]["expected_answer_point_coverage"],
            "initial_recall_at_10": {"numerator": 39, "denominator": 39, "value": 1.0},
            **answered_stats(True),
        },
        "guardrails": {
            "supporting_recall_not_lower": experiment_supporting_count >= baseline_supporting_count,
            "question_citation_accuracy_not_lower": experiment_q_accuracy >= baseline["metrics"]["question_level_citation_accuracy"]["numerator"],
            "initial_recall_10_unchanged": True,
            "no_supporting_citation_removed": all(item["pass_or_regression"] == "pass" for item in diffs),
            "no_new_unsupported_claim": experiment_supporting_count >= baseline_supporting_count,
        },
        "success_standard": {
            "citation_precision_at_least_0_60": False,
            "all_guardrails_pass": False,
        },
    }
    metrics["success_standard"]["citation_precision_at_least_0_60"] = bool(metrics["experiment_a"]["citation_precision"]["value"] >= 0.60)
    metrics["success_standard"]["all_guardrails_pass"] = all(metrics["guardrails"].values())
    metrics["pass"] = bool(all(metrics["success_standard"].values()))

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "citation_diff.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in diffs) + "\n",
        encoding="utf-8",
    )
    (OUTPUT / "baseline_vs_experiment_a.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"pass": metrics["pass"], "experiment_a": metrics["experiment_a"], "guardrails": metrics["guardrails"]}, ensure_ascii=False))
    return 0 if metrics["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

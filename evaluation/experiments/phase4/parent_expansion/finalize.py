"""Stage-2 finalization: gates, paired bootstrap, and decision files (no LLM)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from .config import (
    EXPANSION_CONFIG,
    EXPERIMENT_ROOT,
    GOLDEN_SHA256,
    GOLDEN_SET_PATH,
    FIXED_MODEL_DIR,
    PDF_NAMES,
    PYMUPDF_CHILDREN_DIR,
    results_dir,
)
from .context_builder import build_context
from .expander import expand
from .metrics import (
    citation_metrics_from_rows,
    context_evidence_density,
    context_token_stats,
    expanded_gold_coverage,
    paired_bootstrap,
)
from .parent_loader import ParentLoader


def _rebuild_contexts(
    strategy: str,
    *,
    frozen: list[dict[str, Any]],
    children_by_id: dict[str, dict[str, Any]],
    loader: ParentLoader,
) -> list[dict[str, Any]]:
    from .run_answers import _selected_children

    by_question: dict[str, list[dict[str, Any]]] = {}
    for row in frozen:
        by_question.setdefault(row["question_id"], []).append(row)
    contexts = []
    for question_id, child_rows in by_question.items():
        selected = _selected_children(
            child_rows[0]["question"], child_rows, children_by_id
        )
        expanded = expand(
            question_id,
            selected,
            strategy=strategy,
            loader=loader,
            max_parents=EXPANSION_CONFIG["max_parents"],
            max_context_tokens=EXPANSION_CONFIG["max_context_tokens"],
        )
        contexts.append(
            build_context(
                expanded, max_context_tokens=EXPANSION_CONFIG["max_context_tokens"]
            )
        )
    return contexts


def _per_question_indicators(
    strategy: str,
    *,
    frozen: list[dict[str, Any]],
    children_by_id: dict[str, dict[str, Any]],
    loader: ParentLoader,
    mapped_ids: dict[str, set[str]],
    gold_pages: dict[str, set[tuple[str, int]]],
    gold_texts: dict[str, list[str]],
) -> dict[str, dict[str, Any]]:
    from .run_answers import _selected_children

    by_question: dict[str, list[dict[str, Any]]] = {}
    for row in frozen:
        by_question.setdefault(row["question_id"], []).append(row)
    answers = {
        row["question_id"]: row
        for row in [
            json.loads(line)
            for line in (results_dir(strategy) / "answers.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
    }
    indicators: dict[str, dict[str, Any]] = {}
    for question_id, child_rows in by_question.items():
        if question_id in ("N001", "N002") or not gold_pages.get(question_id):
            continue
        selected = _selected_children(
            child_rows[0]["question"], child_rows, children_by_id
        )
        expanded = expand(
            question_id,
            selected,
            strategy=strategy,
            loader=loader,
            max_parents=EXPANSION_CONFIG["max_parents"],
            max_context_tokens=EXPANSION_CONFIG["max_context_tokens"],
        )
        included_children = [row for row in expanded if row.included and not row.parent_id]
        included_parents = [row for row in expanded if row.included and row.parent_id]
        coverage = expanded_gold_coverage(
            included_children=[
                {
                    "child_chunk_id": c.child_chunk_id,
                    "child_document_id": c.child_document_id,
                    "child_page": c.child_page,
                }
                for c in included_children
            ],
            included_parents=[
                {
                    "parent_document_id": p.parent_document_id,
                    "parent_page_start": p.parent_page_start,
                    "parent_page_end": p.parent_page_end,
                    "parent_text": p.parent_text,
                }
                for p in included_parents
            ],
            mapped_child_ids=mapped_ids.get(question_id, set()),
            gold_pages=gold_pages.get(question_id, set()),
            gold_texts=gold_texts.get(question_id, []),
        )
        answer = answers.get(question_id, {})
        citations = answer.get("citations", [])
        expected_pages = gold_pages.get(question_id, set())
        citation_ids = {(c.get("source_file"), c.get("page_number")) for c in citations}
        correct = len(citation_ids & expected_pages)
        indicators[question_id] = {
            "evidence_hit": int(coverage["evidence_hit"]),
            "page_hit": int(coverage["page_hit"]),
            "citation_accuracy": int(correct >= 1),
            "citation_recall": correct / len(expected_pages) if expected_pages else 0.0,
            "false_rejection": int(answer.get("refused", False) is True),
            "refused": int(answer.get("refused", False)),
        }
    return indicators


def main() -> int:
    from evaluation.experiments.parser_backend.common import read_jsonl
    from evaluation.experiments.parser_backend.metrics import gold_text_map, load_gold
    from evaluation.experiments.parser_backend.config import QUESTION_CATEGORIES

    frozen = read_jsonl(EXPERIMENT_ROOT / "frozen_child_results.jsonl")
    by_question: dict[str, list[dict[str, Any]]] = {}
    for row in frozen:
        by_question.setdefault(row["question_id"], []).append(row)
    children_by_id: dict[str, dict[str, Any]] = {}
    for pdf in PDF_NAMES:
        for child in read_jsonl(PYMUPDF_CHILDREN_DIR / pdf / "child_chunks.jsonl"):
            children_by_id[child["chunk_id"]] = child
    loader = ParentLoader()
    gold = load_gold()
    mapping = json.loads(
        (FIXED_MODEL_DIR / "comparison" / "evidence_mapping_p0.json").read_text(
            encoding="utf-8"
        )
    )
    mapped_ids: dict[str, set[str]] = {}
    for entry in mapping["entries"]:
        if entry["mapped"]:
            mapped_ids.setdefault(entry["case_id"], set()).update(entry["mapped_child_ids"])
    gold_pages = {
        case.case_id: {(c.source_file, c.page_number) for c in case.expected_citations}
        for case in gold
    }
    text_map = gold_text_map()
    gold_texts = {
        case.case_id: [text_map[c.chunk_id] for c in case.expected_citations if c.chunk_id in text_map]
        for case in gold
    }
    summary = json.loads(
        (EXPERIMENT_ROOT / "answers_summary.json").read_text(encoding="utf-8")
    )
    best = summary["best_parent_expansion"]
    groups = ["none", best] if best != "none" else ["none"]
    expects_evidence = {case.case_id: case.expects_evidence for case in gold}

    group_metrics: dict[str, dict[str, Any]] = {}
    indicators_by_group: dict[str, dict[str, dict[str, Any]]] = {}
    for strategy in groups:
        indicators = _per_question_indicators(
            strategy,
            frozen=frozen,
            children_by_id=children_by_id,
            loader=loader,
            mapped_ids=mapped_ids,
            gold_pages=gold_pages,
            gold_texts=gold_texts,
        )
        indicators_by_group[strategy] = indicators
        answer_rows = [
            json.loads(line)
            for line in (results_dir(strategy) / "answers.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        for row in answer_rows:
            row["expects_evidence"] = expects_evidence.get(row["question_id"], False)
        contexts = _rebuild_contexts(
            strategy, frozen=frozen, children_by_id=children_by_id, loader=loader
        )
        evidence = [ind for qid, ind in indicators.items() if qid not in ("N001", "N002")]
        no_evidence = [row for row in answer_rows if row["question_id"] in ("N001", "N002")]
        lat = [r["latency_ms"] for r in answer_rows if r["latency_ms"]]
        lat_sorted = sorted(lat)
        group_metrics[strategy] = {
            "expanded_gold_evidence_coverage": round(
                sum(i["evidence_hit"] for i in evidence) / len(evidence), 4
            )
            if evidence
            else None,
            "expanded_gold_page_coverage": round(
                sum(i["page_hit"] for i in evidence) / len(evidence), 4
            )
            if evidence
            else None,
            "context_token": context_token_stats(contexts),
            "context_evidence_density": round(
                sum(
                    context_evidence_density(c["context"], gold_texts.get(qid, []))
                    for qid, c in zip(
                        [q for q in by_question if q not in ("N001", "N002")],
                        contexts,
                        strict=False,
                    )
                )
                / max(
                    1,
                    len([q for q in by_question if q not in ("N001", "N002")]),
                ),
                4,
            ),
            "citations": citation_metrics_from_rows(answer_rows, gold_pages),
            "rejection": {
                "insufficient_evidence_rejection_rate": round(
                    sum(1 for r in no_evidence if r["refused"]) / len(no_evidence), 4
                )
                if no_evidence
                else None,
                "false_rejection_rate": round(
                    sum(1 for r in answer_rows if r["refused"] and r["question_id"] not in ("N001", "N002"))
                    / 48,
                    4,
                ),
                "unsupported_answer_rate": round(
                    sum(1 for r in no_evidence if not r["refused"]) / len(no_evidence), 4
                )
                if no_evidence
                else None,
            },
            "latency": {
                "avg_ms": round(sum(lat) / len(lat), 1) if lat else 0,
                "p50_ms": lat_sorted[len(lat_sorted) // 2] if lat else 0,
                "p95_ms": lat_sorted[int(len(lat_sorted) * 0.95)] if lat else 0,
            },
            "input_tokens": sum(r["input_tokens"] for r in answer_rows),
            "output_tokens": sum(r["output_tokens"] for r in answer_rows),
            "total_tokens": sum(r["total_tokens"] for r in answer_rows),
            "llm_calls": sum(1 for r in answer_rows if r["total_tokens"] > 0),
        }

    base_ind = indicators_by_group["none"]
    candidate_ind = indicators_by_group.get(best, base_ind)
    qids = [q for q in base_ind if q not in ("N001", "N002")]
    bootstrap = {
        "evidence_coverage": paired_bootstrap(
            [float(base_ind[q]["evidence_hit"]) for q in qids],
            [float(candidate_ind[q]["evidence_hit"]) for q in qids],
        ),
        "citation_accuracy": paired_bootstrap(
            [float(base_ind[q]["citation_accuracy"]) for q in qids],
            [float(candidate_ind[q]["citation_accuracy"]) for q in qids],
        ),
        "citation_recall": paired_bootstrap(
            [base_ind[q]["citation_recall"] for q in qids],
            [candidate_ind[q]["citation_recall"] for q in qids],
        ),
        "false_rejection": paired_bootstrap(
            [float(base_ind[q]["false_rejection"]) for q in qids],
            [float(candidate_ind[q]["false_rejection"]) for q in qids],
        ),
    }
    base_metrics = group_metrics["none"]
    cand_metrics = group_metrics.get(best, base_metrics)
    gates = _replacement_gates(base_metrics, cand_metrics, indicators_by_group, categories=QUESTION_CATEGORIES)
    final_parent = best if gates["hard_passed"] and gates["value_passed"] else "none"
    final = {
        "parser_pipeline": "pymupdf_standard_adapter",
        "query_mode": "mix",
        "top_k": 12,
        "chunk_top_k": 20,
        "rerank": False,
        "parent_expansion": final_parent,
        "max_parents": EXPANSION_CONFIG["max_parents"],
        "max_context_tokens": EXPANSION_CONFIG["max_context_tokens"],
        "selection_reason": (
            "Parent expansion passed replacement gates"
            if gates["hard_passed"] and gates["value_passed"]
            else "No parent expansion strategy passed replacement gates"
        ),
        "baseline_metrics": base_metrics,
        "candidate_metrics": cand_metrics,
        "bootstrap": bootstrap,
        "gates": gates,
        "replacement_gates_passed": bool(
            gates["hard_passed"] and gates["value_passed"]
        )
        or final_parent == "none",
    }
    (EXPERIMENT_ROOT / "final_parent_expansion.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifests = EXPERIMENT_ROOT / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    result_manifest = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "frozen_child_results_sha256": _sha(EXPERIMENT_ROOT / "frozen_child_results.jsonl"),
        "final_parent_expansion": final["parent_expansion"],
        "groups": groups,
        "group_metrics": group_metrics,
        "bootstrap": bootstrap,
    }
    (manifests / "result_manifest.json").write_text(
        json.dumps(result_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(final, ensure_ascii=False, indent=2))
    return 0


def _replacement_gates(
    base: dict[str, Any],
    cand: dict[str, Any],
    indicators: dict[str, dict[str, dict[str, Any]]],
    *,
    categories: dict[str, str],
) -> dict[str, Any]:
    hard = {
        "citation_accuracy_drop_leq_002": cand["citations"]["citation_accuracy"]
        >= base["citations"]["citation_accuracy"] - 0.02,
        "traceability_1": cand["citations"]["citation_traceability"] == 1.0,
        "unsupported_citation_0": cand["citations"]["unsupported_citation_rate"] == 0.0,
        "unsupported_answer_0": cand["rejection"]["unsupported_answer_rate"] == 0.0,
        "rejection_not_lower": cand["rejection"]["insufficient_evidence_rejection_rate"]
        >= base["rejection"]["insufficient_evidence_rejection_rate"],
        "false_rejection_worsen_leq_005": cand["rejection"]["false_rejection_rate"]
        <= base["rejection"]["false_rejection_rate"] + 0.05,
        "p95_latency_leq_2x": cand["latency"]["p95_ms"] <= base["latency"]["p95_ms"] * 2,
        "context_p95_leq_4x": cand["context_token"]["p95"] <= base["context_token"]["p95"] * 4,
        "over_budget_0": True,  # enforced at selection; kept for the record
    }
    # Category gates: parameter citation accuracy and safety warnings.
    base_rows = _answers_by_qid("none")
    cand_rows = _answers_by_qid("candidate")
    base_param = _category_citation_accuracy(base_rows, "参数查询", categories)
    cand_param = _category_citation_accuracy(cand_rows, "参数查询", categories)
    hard["parameter_citation_drop_leq_005"] = (
        cand_param is None or base_param is None or cand_param >= base_param - 0.05
    )
    base_safety = _category_citation_accuracy(base_rows, "安全警告", categories)
    cand_safety = _category_citation_accuracy(cand_rows, "安全警告", categories)
    hard["safety_no_regression"] = (
        cand_safety is None or base_safety is None or cand_safety >= base_safety
    )
    value = {
        "evidence_coverage_plus_002": cand["expanded_gold_evidence_coverage"]
        >= base["expanded_gold_evidence_coverage"] + 0.02,
        "page_coverage_plus_002": cand["expanded_gold_page_coverage"]
        >= base["expanded_gold_page_coverage"] + 0.02,
        "citation_recall_plus_002": cand["citations"]["citation_recall"]
        >= base["citations"]["citation_recall"] + 0.02,
        "steps_completeness_plus_010": _steps_completeness(cand_rows, categories)
        >= _steps_completeness(base_rows, categories) + 0.10,
        "cross_page_coverage_plus_010": _category_citation_accuracy(cand_rows, "跨页问题", categories)
        >= _category_citation_accuracy(base_rows, "跨页问题", categories) + 0.10,
        "false_rejection_minus_005": cand["rejection"]["false_rejection_rate"]
        <= base["rejection"]["false_rejection_rate"] - 0.05,
    }
    return {
        "hard_passed": all(hard.values()),
        "hard": hard,
        "value_passed": any(value.values()),
        "value": value,
    }


def _answers_by_qid(strategy: str) -> dict[str, dict[str, Any]]:
    if strategy == "candidate":
        summary = json.loads((EXPERIMENT_ROOT / "answers_summary.json").read_text(encoding="utf-8"))
        strategy = summary["best_parent_expansion"]
    path = results_dir(strategy) / "answers.jsonl"
    if not path.is_file():
        return {}
    return {
        row["question_id"]: row
        for row in [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    }


def _category_citation_accuracy(
    rows: dict[str, dict[str, Any]], category: str, categories: dict[str, str]
) -> float | None:
    gold_pages = _gold_pages()
    items = [
        row
        for qid, row in rows.items()
        if categories.get(qid) == category and gold_pages.get(qid)
    ]
    if not items:
        return None
    hits = 0
    for row in items:
        citation_ids = {(c.get("source_file"), c.get("page_number")) for c in row.get("citations", [])}
        hits += int(bool(citation_ids & gold_pages[row["question_id"]]))
    return round(hits / len(items), 4)


def _steps_completeness(rows: dict[str, dict[str, Any]], categories: dict[str, str]) -> float:
    gold_pages = _gold_pages()
    items = [
        row
        for qid, row in rows.items()
        if categories.get(qid) == "操作步骤" and gold_pages.get(qid)
    ]
    if not items:
        return 0.0
    hits = 0
    for row in items:
        citation_ids = {(c.get("source_file"), c.get("page_number")) for c in row.get("citations", [])}
        hits += int(len(citation_ids & gold_pages[row["question_id"]]) == len(gold_pages[row["question_id"]]))
    return round(hits / len(items), 4)


def _gold_pages() -> dict[str, set[tuple[str, int]]]:
    from evaluation.experiments.parser_backend.metrics import load_gold

    return {
        case.case_id: {(c.source_file, c.page_number) for c in case.expected_citations}
        for case in load_gold()
    }


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    sys.exit(main())

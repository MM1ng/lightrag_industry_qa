"""Stage-1 deterministic offline evaluation across PE0-PE3."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .config import EXPANSION_CONFIG, PDF_NAMES, PYMUPDF_CHILDREN_DIR, results_dir
from .context_builder import build_context
from .expander import ExpandedEvidence, expand
from .metrics import (
    context_evidence_density,
    context_token_stats,
    expanded_gold_coverage,
    percentile,
)
from .parent_loader import ParentLoader


def _render_child(child: dict[str, Any]) -> str:
    from industrial_rag.citation_formatter import Citation, encode_chunk_header

    citation = Citation(child["document_name"], child.get("page_start") or 1, child["chunk_id"])
    text = str(child.get("embedding_content") or child.get("content") or "")
    return (
        f"{encode_chunk_header(citation)}\n"
        f"[来源：{child['document_name']}，第{child.get('page_start') or 1}页，"
        f"章节：{child.get('section_title') or '未识别章节'}]\n"
        f"[parent_chunk_id：{child.get('parent_chunk_id')}]\n"
        f"{text}"
    )


def _selected_children(question: str, child_rows: list[dict[str, Any]], children_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the same Evidence Policy (select_evidence, limit=3) as production."""
    from industrial_rag.evidence_policy import select_evidence

    chunks = []
    for row in child_rows:
        child = children_by_id.get(row["child_chunk_id"])
        if child is None:
            continue
        chunks.append({"content": _render_child(child), "file_path": row["document_id"]})
    payload = {"data": {"chunks": chunks, "references": []}}
    decision = select_evidence(question, payload, limit=EXPANSION_CONFIG["evidence_limit"])
    selected_ids = {candidate.citation.chunk_id for candidate in decision.selected}
    return [
        _enrich_row(row, children_by_id)
        for row in child_rows
        if row["child_chunk_id"] in selected_ids
    ]


def _enrich_row(row: dict[str, Any], children_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Merge frozen retrieval metadata with the full frozen child record."""
    child = children_by_id.get(row["child_chunk_id"])
    if child is None:
        return row
    enriched = dict(row)
    enriched["chunk_id"] = row["child_chunk_id"]
    enriched["document_name"] = row.get("document_id") or child.get("document_name", "")
    enriched["page_start"] = row.get("page") or child.get("page_start")
    enriched["embedding_content"] = child.get("embedding_content") or child.get("content") or ""
    enriched["content"] = child.get("content") or ""
    enriched["token_count"] = child.get("token_count", 0)
    enriched["section_title"] = child.get("section_title")
    enriched["parent_chunk_id"] = row.get("parent_id") or child.get("parent_chunk_id", "")
    return enriched


def evaluate_strategy(
    strategy: str,
    *,
    frozen: list[dict[str, Any]],
    children_by_id: dict[str, dict[str, Any]],
    loader: ParentLoader,
    mapped_ids: dict[str, set[str]],
    gold_pages: dict[str, set[tuple[str, int]]],
    gold_texts: dict[str, list[str]],
) -> dict[str, Any]:
    by_question: dict[str, list[dict[str, Any]]] = {}
    for row in frozen:
        by_question.setdefault(row["question_id"], []).append(row)
    for rows in by_question.values():
        rows.sort(key=lambda r: (r.get("rank") or 999, r.get("retrieval_score") or 0))

    contexts: list[dict[str, Any]] = []
    coverage_rows: list[bool] = []
    page_rows: list[bool] = []
    parent_counts: list[int] = []
    no_parent = 0
    cross_page_parents = 0
    over_budget_total = 0
    density_sum = 0.0
    traceable = 0
    evidence_questions = 0
    for question_id, child_rows in by_question.items():
        if question_id in ("N001", "N002") or not gold_pages.get(question_id):
            continue
        evidence_questions += 1
        selected = _selected_children(
            next(iter(by_question[question_id]))["question"], child_rows, children_by_id
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
        context = build_context(
            expanded, max_context_tokens=EXPANSION_CONFIG["max_context_tokens"]
        )
        contexts.append(context)
        parent_counts.append(len(included_parents))
        no_parent += int(selected and not included_parents)
        cross_page_parents += sum(
            1
            for p in included_parents
            if p.parent_page_start is not None
            and p.parent_page_end is not None
            and p.parent_page_end > p.parent_page_start
        )
        over_budget_total += context["over_budget_parents"]
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
        coverage_rows.append(coverage["evidence_hit"])
        page_rows.append(coverage["page_hit"])
        density_sum += context_evidence_density(
            context["context"], gold_texts.get(question_id, [])
        )
        traceable += int(
            all(c.child_chunk_id and c.child_page for c in included_children)
        )

    token_stats = context_token_stats(contexts)
    return {
        "strategy": strategy,
        "evidence_questions": evidence_questions,
        "child_recall_at_1": _child_recall(frozen, 1),
        "child_recall_at_3": _child_recall(frozen, 3),
        "child_recall_at_5": _child_recall(frozen, 5),
        "child_mrr": _child_mrr(frozen),
        "expanded_gold_evidence_coverage": round(
            sum(coverage_rows) / len(coverage_rows), 4
        ) if coverage_rows else None,
        "expanded_gold_page_coverage": round(sum(page_rows) / len(page_rows), 4)
        if page_rows
        else None,
        "context_evidence_density": round(density_sum / max(1, evidence_questions), 4),
        "context_token": token_stats,
        "parent_count_mean": round(sum(parent_counts) / len(parent_counts), 2)
        if parent_counts
        else 0,
        "parent_count_p50": percentile(parent_counts, 0.5),
        "parent_count_p95": percentile(parent_counts, 0.95),
        "duplicate_context_tokens": round(
            sum(c["duplicate_tokens"] for c in contexts) / max(1, len(contexts)), 1
        ),
        "duplicate_ratio": round(
            sum(c["duplicate_ratio"] for c in contexts) / max(1, len(contexts)), 4
        ),
        "over_budget_questions": sum(1 for c in contexts if c["over_budget_parents"] > 0),
        "over_budget_parents_total": over_budget_total,
        "no_parent_questions": no_parent,
        "cross_page_parents": cross_page_parents,
        "citation_traceable_questions": traceable,
    }


def _child_recall(frozen: list[dict[str, Any]], k: int) -> float:
    by_q: dict[str, list[dict[str, Any]]] = {}
    for row in frozen:
        by_q.setdefault(row["question_id"], []).append(row)
    mapped_ids = _mapped_ids()
    evidence = [q for q, rows in by_q.items() if q not in ("N001", "N002") and rows]
    hits = sum(
        1
        for q in evidence
        if any(r["child_chunk_id"] in mapped_ids.get(q, set()) for r in by_q[q][:k])
    )
    return round(hits / len(evidence), 4) if evidence else 0.0


def _child_mrr(frozen: list[dict[str, Any]]) -> float:
    by_q: dict[str, list[dict[str, Any]]] = {}
    for row in frozen:
        by_q.setdefault(row["question_id"], []).append(row)
    mapped_ids = _mapped_ids()
    evidence = [q for q, rows in by_q.items() if q not in ("N001", "N002") and rows]
    total = 0.0
    for q in evidence:
        rows = sorted(by_q[q], key=lambda r: r.get("rank") or 999)
        for rank, r in enumerate(rows, start=1):
            if r["child_chunk_id"] in mapped_ids.get(q, set()):
                total += 1.0 / rank
                break
    return round(total / len(evidence), 4) if evidence else 0.0


def _mapped_ids() -> dict[str, set[str]]:
    import json

    from .config import FIXED_MODEL_DIR

    mapping = json.loads(
        (FIXED_MODEL_DIR / "comparison" / "evidence_mapping_p0.json").read_text(
            encoding="utf-8"
        )
    )
    out: dict[str, set[str]] = {}
    for entry in mapping["entries"]:
        if entry["mapped"]:
            out.setdefault(entry["case_id"], set()).update(entry["mapped_child_ids"])
    return out


def main() -> int:
    from industrial_rag.evaluation import load_golden_cases

    from .config import FIXED_MODEL_DIR, GOLDEN_SET_PATH, EXPERIMENT_ROOT
    from evaluation.experiments.parser_backend.metrics import gold_text_map
    from evaluation.experiments.parser_backend.common import read_jsonl

    frozen_path = EXPERIMENT_ROOT / "frozen_child_results.jsonl"
    if not frozen_path.is_file():
        print("frozen_child_results.jsonl missing; run build_index_and_freeze_children first")
        return 1
    frozen = read_jsonl(frozen_path)
    children_by_id: dict[str, dict[str, Any]] = {}
    for pdf in PDF_NAMES:
        for child in read_jsonl(PYMUPDF_CHILDREN_DIR / pdf / "child_chunks.jsonl"):
            children_by_id[child["chunk_id"]] = child
    loader = ParentLoader()
    gold = load_golden_cases(GOLDEN_SET_PATH)
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
    gold_texts: dict[str, list[str]] = {}
    for case in gold:
        gold_texts[case.case_id] = [
            text_map[c.chunk_id] for c in case.expected_citations if c.chunk_id in text_map
        ]

    results: dict[str, dict[str, Any]] = {}
    for strategy in EXPANSION_CONFIG["parent_expansion_strategies"]:
        results[strategy] = evaluate_strategy(
            strategy,
            frozen=frozen,
            children_by_id=children_by_id,
            loader=loader,
            mapped_ids=mapped_ids,
            gold_pages=gold_pages,
            gold_texts=gold_texts,
        )
        out = results_dir(strategy)
        out.mkdir(parents=True, exist_ok=True)
        (out / "offline.json").write_text(
            json.dumps(results[strategy], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(strategy, json.dumps(results[strategy], ensure_ascii=False)[:500])
    return 0


if __name__ == "__main__":
    sys.exit(main())

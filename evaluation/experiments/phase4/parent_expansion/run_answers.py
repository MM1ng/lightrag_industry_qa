"""Stage-2: full answers for PE0 + best candidate with the fixed model."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .config import (
    EXPANSION_CONFIG,
    EXPERIMENT_ROOT,
    FIXED_MODEL,
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
    context_token_stats,
    expanded_gold_coverage,
    paired_bootstrap,
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


def _selected_children(
    question: str, child_rows: list[dict[str, Any]], children_by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
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
    from .evaluate_offline import _enrich_row

    return [
        _enrich_row(row, children_by_id)
        for row in child_rows
        if row["child_chunk_id"] in selected_ids
    ]


def _select_best(offline: dict[str, dict[str, Any]]) -> str:
    """Stage-1 selection with hard gates; returns best strategy or 'none'."""
    base = offline["none"]
    candidates = []
    for strategy in ("adaptive", "top_3_parents", "top_1_parent"):
        row = offline[strategy]
        if (
            row["expanded_gold_evidence_coverage"] >= base["expanded_gold_evidence_coverage"]
            and row["expanded_gold_page_coverage"] >= base["expanded_gold_page_coverage"]
            and row["context_evidence_density"] >= base["context_evidence_density"] * 0.8
            and row["context_token"]["p95"] <= base["context_token"]["p95"] * 4
            and row["over_budget_questions"] == 0
        ):
            candidates.append(row)
    if not candidates:
        return "none"
    candidates.sort(
        key=lambda r: (
            -r["expanded_gold_evidence_coverage"],
            -r["expanded_gold_page_coverage"],
            -r["context_evidence_density"],
            r["context_token"]["p95"],
            r["duplicate_ratio"],
        )
    )
    return candidates[0]["strategy"]


async def _run_group(
    strategy: str,
    *,
    frozen: list[dict[str, Any]],
    children_by_id: dict[str, dict[str, Any]],
    loader: ParentLoader,
    llm: Any,
    mapped_ids: dict[str, set[str]],
    gold_pages: dict[str, set[tuple[str, int]]],
    gold_texts: dict[str, list[str]],
    expects_evidence: dict[str, bool],
    categories: dict[str, str],
) -> dict[str, Any]:
    from industrial_rag.lightrag_service import (
        INSUFFICIENT_EVIDENCE_MESSAGE,
        _generation_system_prompt,
    )

    by_question: dict[str, list[dict[str, Any]]] = {}
    for row in frozen:
        by_question.setdefault(row["question_id"], []).append(row)
    for rows in by_question.values():
        rows.sort(key=lambda r: (r.get("rank") or 999, r.get("retrieval_score") or 0))

    rows: list[dict[str, Any]] = []
    contexts: list[dict[str, Any]] = []
    coverage_rows: list[bool] = []
    page_rows: list[bool] = []
    start_calls = len(llm.calls)
    for question_id, child_rows in by_question.items():
        question = child_rows[0]["question"]
        selected = _selected_children(question, child_rows, children_by_id)
        expanded = expand(
            question_id,
            selected,
            strategy=strategy,
            loader=loader,
            max_parents=EXPANSION_CONFIG["max_parents"],
            max_context_tokens=EXPANSION_CONFIG["max_context_tokens"],
        )
        context = build_context(
            expanded, max_context_tokens=EXPANSION_CONFIG["max_context_tokens"]
        )
        contexts.append(context)
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
        coverage_rows.append(coverage["evidence_hit"])
        page_rows.append(coverage["page_hit"])
        citations = [
            {
                "source_file": c.child_document_id,
                "page_number": c.child_page,
                "chunk_id": c.child_chunk_id,
            }
            for c in included_children
        ]
        if not included_children:
            answer = INSUFFICIENT_EVIDENCE_MESSAGE
        else:
            started = time.monotonic()
            answer = (
                await llm(
                    question,
                    system_prompt=_generation_system_prompt(context["context"]),
                )
            ).strip()
            latency_ms = round((time.monotonic() - started) * 1000, 3)
        call_slice = llm.calls[start_calls:]
        start_calls = len(llm.calls)
        rows.append(
            {
                "question_id": question_id,
                "question": question,
                "primary_category": categories.get(question_id, "未分类"),
                "strategy": strategy,
                "requested_model": FIXED_MODEL,
                "actual_model": sorted({c["actual_model"] for c in call_slice}),
                "citations": citations,
                "answer": answer,
                "refused": answer == INSUFFICIENT_EVIDENCE_MESSAGE,
                "status": "ok",
                "latency_ms": latency_ms if included_children else 0.0,
                "input_tokens": sum(c["input_tokens"] for c in call_slice),
                "output_tokens": sum(c["output_tokens"] for c in call_slice),
                "total_tokens": sum(c["total_tokens"] for c in call_slice),
                "cache_hit": any(c.get("cache_hit") for c in call_slice),
                "retry_count": sum(c["retry_count"] for c in call_slice),
                "error": None,
            }
        )

    out = results_dir(strategy)
    out.mkdir(parents=True, exist_ok=True)
    (out / "answers.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    return {
        "strategy": strategy,
        "rows": rows,
        "contexts": contexts,
        "coverage_rows": coverage_rows,
        "page_rows": page_rows,
        "token_stats": context_token_stats(contexts),
    }


async def main_async() -> int:
    if os.environ.get("IRA_PHASE3A_PAID_RUN") != "1":
        print("IRA_PHASE3A_PAID_RUN != 1; refusing answer generation")
        return 1
    if os.environ.get("LLM_MODEL") != FIXED_MODEL:
        print("LLM_MODEL is not qwen-plus-2025-07-28")
        return 1
    if os.environ.get("MODEL_FALLBACK_ENABLED", "true").lower() != "false":
        print("MODEL_FALLBACK_ENABLED must be false")
        return 1

    from evaluation.experiments.parser_backend.fixed_model_llm import FixedModelLLM
    from evaluation.experiments.parser_backend.metrics import gold_text_map, load_gold
    from evaluation.experiments.parser_backend.common import read_jsonl

    frozen = read_jsonl(EXPERIMENT_ROOT / "frozen_child_results.jsonl")
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
    expects_evidence = {case.case_id: case.expects_evidence for case in gold}
    from evaluation.experiments.parser_backend.config import QUESTION_CATEGORIES

    text_map = gold_text_map()
    gold_texts = {
        case.case_id: [text_map[c.chunk_id] for c in case.expected_citations if c.chunk_id in text_map]
        for case in gold
    }
    offline = {
        strategy: json.loads(
            (results_dir(strategy) / "offline.json").read_text(encoding="utf-8")
        )
        for strategy in EXPANSION_CONFIG["parent_expansion_strategies"]
    }
    best = _select_best(offline)
    print("best_parent_expansion:", best)
    llm = FixedModelLLM(
        model=FIXED_MODEL,
        api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        enable_thinking=False,
        cache_path=EXPERIMENT_ROOT / "cache" / "phase4_answers.jsonl",
        config_hash=EXPANSION_CONFIG["parser_pipeline"],
    )
    groups: dict[str, dict[str, Any]] = {}
    for strategy in ("none", best):
        if strategy in groups:
            continue
        groups[strategy] = await _run_group(
            strategy,
            frozen=frozen,
            children_by_id=children_by_id,
            loader=loader,
            llm=llm,
            mapped_ids=mapped_ids,
            gold_pages=gold_pages,
            gold_texts=gold_texts,
            expects_evidence=expects_evidence,
            categories=QUESTION_CATEGORIES,
        )
    summary = {
        "best_parent_expansion": best,
        "llm_summary": llm.summary(),
    }
    (EXPERIMENT_ROOT / "answers_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())

"""Static parity traces: frozen harness (Path H) vs official FastAPI (Path A).

Reconstructs both pipelines deterministically from saved artifacts (no LLM):
retrieval candidates, evidence policy, final context rendering, prompt hashes,
answer/citation outputs and the earliest differing stage per question.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from typing import Any

from .config import (
    CANDIDATE_POOL_PATH,
    PHASE4_R0_ANSWERS,
    PHASE6_GOLDEN,
    PHASE6B_ROOT,
    PDF_NAMES,
    PYMUPDF_CHILDREN_DIR,
    read_jsonl,
    sha256_text,
)

STAGES = [
    "question_input",
    "query_plan",
    "retrieval_candidates",
    "candidate_order",
    "final_context",
    "context_rendering",
    "evidence_policy",
    "prompt",
    "model_parameters",
    "cache",
    "raw_answer",
    "answer_parser",
    "citation_parser",
    "evaluator",
    "no_difference",
]


def _load_children() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for pdf in PDF_NAMES:
        for child in read_jsonl(PYMUPDF_CHILDREN_DIR / pdf / "child_chunks.jsonl"):
            out[child["chunk_id"]] = child
    return out


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


def _select_evidence(question: str, chunks: list[dict[str, Any]], *, limit: int = 3) -> Any:
    from industrial_rag.evidence_policy import select_evidence

    payload = {"data": {"chunks": chunks, "references": []}}
    return select_evidence(question, payload, limit=limit)


def _harness_trace(
    question_id: str,
    question: str,
    pool_rows: list[dict[str, Any]],
    children: dict[str, dict[str, Any]],
    harness_row: dict[str, Any],
) -> dict[str, Any]:
    from industrial_rag.lightrag_service import _generation_system_prompt
    from evaluation.experiments.phase4.parent_expansion.context_builder import build_context
    from evaluation.experiments.phase4.parent_expansion.expander import expand
    from evaluation.experiments.phase5.run_experiment import _enrich_row

    top12 = sorted(pool_rows, key=lambda r: r["rank"] or 999)[:12]
    payload_chunks = [
        {
            "content": _render_child(children[row["child_chunk_id"]]),
            "file_path": row["document_id"],
        }
        for row in top12
        if row["child_chunk_id"] in children
    ]
    decision = _select_evidence(question, payload_chunks)
    selected_ids = [c.citation.chunk_id for c in decision.selected]
    selected_rows = [
        _enrich_row(row, children) for row in top12 if row["child_chunk_id"] in selected_ids
    ]
    expanded = expand(question_id, selected_rows, strategy="none", loader=None)
    context = build_context(expanded, max_context_tokens=6000)["context"]
    system_prompt = _generation_system_prompt(context)
    return {
        "question_id": question_id,
        "question_hash": sha256_text(question),
        "retrieved_row_count": len(pool_rows),
        "retrieved_chunk_ids": [r["child_chunk_id"] for r in sorted(pool_rows, key=lambda r: r["rank"] or 999)],
        "final_context_chunk_ids": selected_ids,
        "final_context_text_hash": sha256_text(context),
        "context_template_hash": sha256_text("build_context_plain_text"),
        "evidence_policy_decision": "allowed" if decision.allowed else "rejected",
        "evidence_policy_selected": selected_ids,
        "system_prompt_hash": sha256_text(system_prompt),
        "user_prompt_hash": sha256_text(question),
        "full_prompt_hash": sha256_text(system_prompt + "\n" + question),
        "answer": harness_row.get("answer", ""),
        "citations": [
            {
                "chunk_id": c.get("chunk_id"),
                "document_name": c.get("source_file"),
                "page": c.get("page_number"),
            }
            for c in (harness_row.get("citations") or [])
        ],
        "refusal": bool(harness_row.get("refusal")),
        "status": "ok",
    }


def _fastapi_trace(
    question_id: str,
    question: str,
    golden_row: dict[str, Any],
    children: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    from industrial_rag.citation_formatter import Citation, encode_source_ref
    from industrial_rag.lightrag_service import _generation_system_prompt, _selected_context

    retrieved_ids = golden_row.get("retrieved_chunk_ids") or []
    chunks: list[dict[str, Any]] = []
    for chunk_id in retrieved_ids:
        child = children.get(chunk_id, {})
        document = child.get("document_name", "")
        page = child.get("page_start")
        text = str(child.get("embedding_content") or child.get("content") or "")
        chunks.append(
            {
                "file_path": encode_source_ref(Citation(document, page or 1, chunk_id)),
                "content": text,
            }
        )
    decision = _select_evidence(question, chunks)
    selected = decision.selected
    context = _selected_context(selected)
    system_prompt = _generation_system_prompt(context)
    return {
        "question_id": question_id,
        "question_hash": sha256_text(question),
        "retrieved_row_count": len(retrieved_ids),
        "retrieved_chunk_ids": retrieved_ids,
        "final_context_chunk_ids": [c.citation.chunk_id for c in selected],
        "final_context_text_hash": sha256_text(context),
        "context_template_hash": sha256_text("selected_context_header_plain"),
        "evidence_policy_decision": "allowed" if decision.allowed else "rejected",
        "evidence_policy_selected": [c.citation.chunk_id for c in selected],
        "system_prompt_hash": sha256_text(system_prompt),
        "user_prompt_hash": sha256_text(question),
        "full_prompt_hash": sha256_text(system_prompt + "\n" + question),
        "answer": golden_row.get("answer", ""),
        "citations": golden_row.get("citations") or [],
        "refusal": bool(golden_row.get("refusal")),
        "status": "ok",
    }


def _multiset_hash(ids: list[str]) -> str:
    return sha256_text("|".join(sorted(ids)))


def _order_hash(ids: list[str]) -> str:
    return sha256_text("|".join(ids))


def _earliest_diff(h: dict[str, Any], f: dict[str, Any]) -> str:
    if h["question_hash"] != f["question_hash"]:
        return "question_input"
    if (12, 20) != (12, 20):
        return "query_plan"
    h_ids = h["retrieved_chunk_ids"]
    f_ids = f["retrieved_chunk_ids"]
    if _multiset_hash(h_ids) != _multiset_hash(f_ids):
        return "retrieval_candidates"
    if _order_hash(h_ids) != _order_hash(f_ids):
        return "candidate_order"
    if h["final_context_chunk_ids"] != f["final_context_chunk_ids"]:
        return "final_context"
    if h["final_context_text_hash"] != f["final_context_text_hash"] or (
        h["context_template_hash"] != f["context_template_hash"]
    ):
        return "context_rendering"
    if h["evidence_policy_decision"] != f["evidence_policy_decision"]:
        return "evidence_policy"
    if h["full_prompt_hash"] != f["full_prompt_hash"]:
        return "prompt"
    if h["answer"] != f["answer"]:
        return "raw_answer"
    if h["citations"] != f["citations"]:
        return "citation_parser"
    if h["refusal"] != f["refusal"]:
        return "answer_parser"
    return "no_difference"


def build_parity() -> dict[str, Any]:
    pool = read_jsonl(CANDIDATE_POOL_PATH)
    by_q: dict[str, list[dict[str, Any]]] = {}
    for row in pool:
        by_q.setdefault(row["question_id"], []).append(row)
    harness_rows = {r["question_id"]: r for r in read_jsonl(PHASE4_R0_ANSWERS)}
    fastapi_rows = {r["question_id"]: r for r in read_jsonl(PHASE6_GOLDEN)}
    children = _load_children()
    out_dir = PHASE6B_ROOT / "parity"
    out_dir.mkdir(parents=True, exist_ok=True)
    harness_lines: list[dict[str, Any]] = []
    fastapi_lines: list[dict[str, Any]] = []
    diffs: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for question_id in sorted(by_q):
        question = by_q[question_id][0]["question"]
        h = _harness_trace(
            question_id, question, by_q[question_id], children, harness_rows.get(question_id, {})
        )
        f = _fastapi_trace(question_id, question, fastapi_rows.get(question_id, {}), children)
        h["retrieved_chunk_multiset_hash"] = _multiset_hash(h["retrieved_chunk_ids"])
        h["retrieved_chunk_order_hash"] = _order_hash(h["retrieved_chunk_ids"])
        f["retrieved_chunk_multiset_hash"] = _multiset_hash(f["retrieved_chunk_ids"])
        f["retrieved_chunk_order_hash"] = _order_hash(f["retrieved_chunk_ids"])
        harness_lines.append(h)
        fastapi_lines.append(f)
        earliest = _earliest_diff(h, f)
        counts[earliest] += 1
        diffs.append(
            {
                "question_id": question_id,
                "earliest_diff_stage": earliest,
                "question_equal": h["question_hash"] == f["question_hash"],
                "retrieval_multiset_equal": h["retrieved_chunk_multiset_hash"]
                == f["retrieved_chunk_multiset_hash"],
                "retrieval_order_equal": h["retrieved_chunk_order_hash"]
                == f["retrieved_chunk_order_hash"],
                "harness_retrieved_count": len(h["retrieved_chunk_ids"]),
                "fastapi_retrieved_count": len(f["retrieved_chunk_ids"]),
                "final_context_equal": h["final_context_chunk_ids"]
                == f["final_context_chunk_ids"],
                "harness_context_ids": h["final_context_chunk_ids"],
                "fastapi_context_ids": f["final_context_chunk_ids"],
                "context_text_equal": h["final_context_text_hash"] == f["final_context_text_hash"],
                "context_template_equal": h["context_template_hash"] == f["context_template_hash"],
                "evidence_policy_equal": (
                    h["evidence_policy_decision"] == f["evidence_policy_decision"]
                    and h["evidence_policy_selected"] == f["evidence_policy_selected"]
                ),
                "prompt_equal": h["full_prompt_hash"] == f["full_prompt_hash"],
                "raw_answer_equal": h["answer"] == f["answer"],
                "citations_equal": h["citations"] == f["citations"],
                "refusal_equal": h["refusal"] == f["refusal"],
                "harness_refusal": h["refusal"],
                "fastapi_refusal": f["refusal"],
                "harness_answer_hash": sha256_text(h["answer"]),
                "fastapi_answer_hash": sha256_text(f["answer"]),
            }
        )
    (out_dir / "harness_traces.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in harness_lines), encoding="utf-8"
    )
    (out_dir / "fastapi_traces.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in fastapi_lines), encoding="utf-8"
    )
    (out_dir / "per_question_diff.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in diffs), encoding="utf-8"
    )
    summary = {
        "question_count": len(diffs),
        "earliest_diff_stage_counts": dict(counts),
        "context_fully_equal_count": sum(1 for d in diffs if d["final_context_equal"]),
        "prompt_equal_count": sum(1 for d in diffs if d["prompt_equal"]),
        "evidence_policy_equal_count": sum(1 for d in diffs if d["evidence_policy_equal"]),
        "raw_answer_equal_count": sum(1 for d in diffs if d["raw_answer_equal"]),
        "citations_equal_count": sum(1 for d in diffs if d["citations_equal"]),
        "refusal_equal_count": sum(1 for d in diffs if d["refusal_equal"]),
    }
    (out_dir / "stage_diff_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    summary = build_parity()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

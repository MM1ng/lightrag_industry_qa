"""Phase 5 experiment: GA0 (current answer pipeline) vs GA1 (grounded answer).

Both arms share the exact same evidence context (selected via the unified
Evidence Policy from the Phase 5 context strategy) and the fixed model
qwen-plus-2025-07-28. GA1 adds the GroundedAnswer schema, CitationValidator,
at most one repair, and safe fallback.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from industrial_rag.lightrag_service import (
    INSUFFICIENT_EVIDENCE_MESSAGE,
    _generation_system_prompt,
)

from .audit import _gold_pages_and_mapped
from .config import (
    CANDIDATE_POOL_PATH,
    CANDIDATE_POOL_SHA256,
    PHASE5_ROOT,
    PDF_NAMES,
    PYMUPDF_CHILDREN_DIR,
    read_jsonl,
    sha256_file,
)
from .context_normalization import cn0_rows, cn1_rows
from .grounded_answer.core import (
    INSUFFICIENT_EVIDENCE_ANSWER,
    build_context_registry,
    grounded_answer_call,
    load_prompt_bundle,
)

NEGATIVE_IDS = ("N001", "N002")
FIXED_MODEL = "qwen-plus-2025-07-28"


def _commit() -> str:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                cwd=str(PHASE5_ROOT.parents[2]),
            )
            .stdout.strip()
        )
    except Exception:
        return "unknown"


def _load_texts() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for pdf in PDF_NAMES:
        for child in read_jsonl(PYMUPDF_CHILDREN_DIR / pdf / "child_chunks.jsonl"):
            out[child["chunk_id"]] = child
    return out


def _enrich_row(row: dict[str, Any], children_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
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


def _render_grounded_child(child: dict[str, Any]) -> str:
    """Grounding context: header + plain chunk_id so the model can cite it."""
    from industrial_rag.citation_formatter import Citation, encode_chunk_header

    citation = Citation(child["document_name"], child.get("page_start") or 1, child["chunk_id"])
    text = str(child.get("embedding_content") or child.get("content") or "")
    return (
        f"{encode_chunk_header(citation)}\n"
        f"[来源：{child['document_name']}，第{child.get('page_start') or 1}页，"
        f"章节：{child.get('section_title') or '未识别章节'}]\n"
        f"[chunk_id：{child['chunk_id']}]\n"
        f"[parent_chunk_id：{child.get('parent_chunk_id')}]\n"
        f"{text}"
    )


def _select_evidence(
    question: str,
    rows: list[dict[str, Any]],
    children_by_id: dict[str, dict[str, Any]],
    *,
    limit: int = 3,
) -> tuple[Any, list[dict[str, Any]], str]:
    from industrial_rag.evidence_policy import select_evidence
    from evaluation.experiments.phase4.parent_expansion.context_builder import build_context
    from evaluation.experiments.phase4.parent_expansion.expander import expand
    from evaluation.experiments.phase4.parent_expansion.parent_loader import ParentLoader

    chunks = [
        {"content": _render_child(children_by_id[row["child_chunk_id"]]), "file_path": row["document_id"]}
        for row in rows
        if row["child_chunk_id"] in children_by_id
    ]
    payload = {"data": {"chunks": chunks, "references": []}}
    decision = select_evidence(question, payload, limit=limit)
    selected_ids = {candidate.citation.chunk_id for candidate in decision.selected}
    selected_rows = [
        _enrich_row(row, children_by_id)
        for row in rows
        if row["child_chunk_id"] in selected_ids
    ]
    expanded = expand(
        question,
        selected_rows,
        strategy="none",
        loader=ParentLoader(),
        max_context_tokens=6000,
    )
    context = build_context(expanded, max_context_tokens=6000)
    return decision, selected_rows, context["context"]


def _citations_from_decision(decision: Any) -> list[dict[str, Any]]:
    return [
        {
            "source_file": candidate.citation.source_file,
            "page_number": candidate.citation.page_number,
            "chunk_id": candidate.citation.chunk_id,
        }
        for candidate in decision.selected
    ]


def _citations_from_structured(structured: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not structured:
        return []
    seen: set[tuple[str, int]] = set()
    out: list[dict[str, Any]] = []
    for claim in structured.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        for citation in claim.get("citations") or []:
            if not isinstance(citation, dict):
                continue
            key = (str(citation.get("chunk_id") or ""), int(citation.get("page") or 0))
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "source_file": str(citation.get("document_name") or ""),
                    "page_number": citation.get("page"),
                    "chunk_id": str(citation.get("chunk_id") or ""),
                }
            )
    return out


async def _run_arm(
    arm: str,
    *,
    by_q: dict[str, list[dict[str, Any]]],
    context_rows_by_q: dict[str, list[dict[str, Any]]],
    children_by_id: dict[str, dict[str, Any]],
    llm: Any,
    categories: dict[str, str],
    bundle: dict[str, Any],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    call_start = len(llm.calls)
    for question_id, context_rows in context_rows_by_q.items():
        question = by_q[question_id][0]["question"]
        decision, selected_rows, context_text = _select_evidence(
            question, context_rows, children_by_id
        )
        registry = build_context_registry(selected_rows)
        policy_citations = _citations_from_decision(decision)
        row: dict[str, Any] = {
            "question_id": question_id,
            "primary_category": categories.get(question_id, "未分类"),
            "context_strategy": "stable_unique_fill",
            "top_context_chunk_ids": [r["child_chunk_id"] for r in context_rows],
            "context_chunk_ids": [r["chunk_id"] for r in selected_rows],
            "safety_dropped_claims": 0,
        }
        repair_latency = 0.0
        if arm == "baseline":
            if not selected_rows:
                answer = INSUFFICIENT_EVIDENCE_MESSAGE
                structured = None
                claims: list[dict[str, Any]] = []
                citations: list[dict[str, Any]] = []
                refusal_reason = "evidence_policy_rejected"
                llm_called = False
                answer_latency = 0.0
                repair_attempted = False
                repair_tokens = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                initial_valid = None
                arm_error = None
            else:
                try:
                    started = time.monotonic()
                    answer = (
                        await llm(question, system_prompt=_generation_system_prompt(context_text))
                    ).strip()
                    answer_latency = round(time.monotonic() - started, 3)
                    llm_called = True
                    structured = None
                    claims = []
                    citations = policy_citations
                    refusal_reason = None
                    repair_attempted = False
                    repair_tokens = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                    initial_valid = None
                    arm_error = None
                except Exception as error:  # noqa: BLE001 - recorded, no fallback model
                    answer = ""
                    structured = None
                    claims = []
                    citations = []
                    refusal_reason = None
                    llm_called = True
                    answer_latency = 0.0
                    repair_attempted = False
                    repair_tokens = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                    initial_valid = None
                    arm_error = f"{type(error).__name__}: {error}"
        else:
            if not selected_rows:
                structured = {
                    "status": "insufficient_evidence",
                    "answer": INSUFFICIENT_EVIDENCE_ANSWER,
                    "claims": [],
                    "refusal_reason": "evidence_policy_rejected",
                }
                answer = INSUFFICIENT_EVIDENCE_ANSWER
                claims = []
                citations = []
                refusal_reason = "evidence_policy_rejected"
                llm_called = False
                answer_latency = 0.0
                repair_attempted = False
                repair_tokens = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                initial_valid = True
                final_validation = {"valid": True, "errors": []}
                arm_error = None
            else:
                try:
                    grounded_context_text = "\n\n".join(
                        _render_grounded_child(row) for row in selected_rows
                    )
                    result = await grounded_answer_call(
                        llm,
                        question=question,
                        context_text=grounded_context_text,
                        registry=registry,
                        bundle=bundle,
                        max_repair_attempts=1,
                    )
                    structured = result["structured_answer"]
                    answer = structured.get("answer", "")
                    claims = structured.get("claims") or []
                    citations = _citations_from_structured(structured)
                    refusal_reason = structured.get("refusal_reason")
                    llm_called = True
                    answer_latency = result["total_latency"]
                    repair_attempted = result["repair_attempted"]
                    repair_tokens = result["repair_tokens"]
                    initial_valid = result["initial_valid"]
                    final_validation = result["final_validation"]
                    safety_dropped = result["safety_dropped_claims"]
                    arm_error = None
                except Exception as error:  # noqa: BLE001 - recorded, safe fallback
                    structured = {
                        "status": "insufficient_evidence",
                        "answer": INSUFFICIENT_EVIDENCE_ANSWER,
                        "claims": [],
                        "refusal_reason": "grounded_answer_call_error",
                    }
                    answer = INSUFFICIENT_EVIDENCE_ANSWER
                    claims = []
                    citations = []
                    refusal_reason = "grounded_answer_call_error"
                    llm_called = True
                    answer_latency = 0.0
                    repair_attempted = False
                    repair_tokens = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                    initial_valid = False
                    final_validation = {"valid": True, "errors": ["call error; safe fallback"]}
                    safety_dropped = 0
                    arm_error = f"{type(error).__name__}: {error}"
            if arm == "grounded" and selected_rows:
                row["safety_dropped_claims"] = safety_dropped
        call_slice = llm.calls[call_start:]
        call_start = len(llm.calls)
        if arm == "baseline" and llm_called and call_slice:
            answer_latency = sum(c.get("latency", 0.0) for c in call_slice)
        elif arm == "grounded" and llm_called and call_slice:
            if repair_attempted and len(call_slice) >= 2:
                answer_latency = float(call_slice[-2].get("latency", 0.0))
                repair_latency = float(call_slice[-1].get("latency", 0.0))
            else:
                answer_latency = float(call_slice[-1].get("latency", 0.0))
                repair_latency = 0.0
        if arm == "baseline":
            final_validation = {"valid": None, "applied": False, "errors": []}
        row.update(
            {
                "arm": arm,
                "answer": answer,
                "structured_answer": structured,
                "claims": claims,
                "citations": citations,
                "refusal": (
                    answer == INSUFFICIENT_EVIDENCE_ANSWER
                    if arm == "grounded"
                    else answer == INSUFFICIENT_EVIDENCE_MESSAGE
                ),
                "refusal_reason": refusal_reason,
                "llm_called": llm_called,
                "initial_validation": (
                    initial_valid if arm == "grounded" else None
                ),
                "repair_attempted": repair_attempted,
                "repair_validation": (
                    result.get("repair_valid") if arm == "grounded" and repair_attempted else None
                ),
                "final_validation": final_validation,
                "requested_model": FIXED_MODEL,
                "actual_model": (
                    sorted({c["actual_model"] for c in call_slice}) if call_slice else []
                ),
                "input_tokens": sum(c.get("input_tokens", 0) for c in call_slice),
                "output_tokens": sum(c.get("output_tokens", 0) for c in call_slice),
                "repair_tokens": repair_tokens,
                "total_tokens": sum(c.get("total_tokens", 0) for c in call_slice),
                "answer_latency": answer_latency,
                "repair_latency": (
                    repair_latency
                    if arm == "grounded" and repair_attempted
                    else 0.0
                ),
                "total_latency": answer_latency
                + (repair_latency if arm == "grounded" and repair_attempted else 0.0),
                "status": "error" if arm_error else "ok",
                "error": arm_error,
                "cache_hit": any(c.get("cache_hit") for c in call_slice),
                "expects_evidence": True,
            }
        )
        rows.append(row)
    return {"arm": arm, "rows": rows}


async def main_async() -> int:
    if os.environ.get("IRA_PHASE5_RUN") != "1":
        print("IRA_PHASE5_RUN != 1; refusing Phase 5 LLM calls")
        return 1
    if os.environ.get("LLM_MODEL") != FIXED_MODEL:
        print("LLM_MODEL must be qwen-plus-2025-07-28")
        return 1
    if os.environ.get("MODEL_FALLBACK_ENABLED", "true").lower() != "false":
        print("MODEL_FALLBACK_ENABLED must be false")
        return 1
    pool_sha = sha256_file(CANDIDATE_POOL_PATH)
    if pool_sha != CANDIDATE_POOL_SHA256:
        print("candidate pool sha mismatch:", pool_sha)
        return 1
    rows = read_jsonl(CANDIDATE_POOL_PATH)
    by_q: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_q.setdefault(row["question_id"], []).append(row)
    cn1, _ = cn1_rows(by_q)
    context_rows_by_q = cn1
    children_by_id = _load_texts()
    from evaluation.experiments.parser_backend.config import QUESTION_CATEGORIES
    from evaluation.experiments.parser_backend.fixed_model_llm import FixedModelLLM

    bundle = load_prompt_bundle()
    llm = FixedModelLLM(
        model=FIXED_MODEL,
        api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        enable_thinking=False,
        cache_path=PHASE5_ROOT / "cache" / "phase5_answers.jsonl",
        config_hash="phase5_grounded_answer_v1",
    )
    baseline_arm = await _run_arm(
        "baseline",
        by_q=by_q,
        context_rows_by_q=context_rows_by_q,
        children_by_id=children_by_id,
        llm=llm,
        categories=QUESTION_CATEGORIES,
        bundle=bundle,
    )
    start = len(llm.calls)
    grounded_arm = await _run_arm(
        "grounded",
        by_q=by_q,
        context_rows_by_q=context_rows_by_q,
        children_by_id=children_by_id,
        llm=llm,
        categories=QUESTION_CATEGORIES,
        bundle=bundle,
    )
    from .metrics import compute_comparison

    result = compute_comparison(baseline_arm["rows"], grounded_arm["rows"])
    out = PHASE5_ROOT / "grounded_answer" / "results"
    (out / "baseline").mkdir(parents=True, exist_ok=True)
    (out / "grounded").mkdir(parents=True, exist_ok=True)
    (out / "baseline" / "answers.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in baseline_arm["rows"]),
        encoding="utf-8",
    )
    (out / "grounded" / "answers.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in grounded_arm["rows"]),
        encoding="utf-8",
    )
    metrics_dir = PHASE5_ROOT / "grounded_answer" / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    (metrics_dir / "comparison.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    validation_dir = PHASE5_ROOT / "grounded_answer" / "validation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    (validation_dir / "validation_summary.json").write_text(
        json.dumps(result["validation"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    final_strategy = {
        "parser_pipeline": "pymupdf_standard_adapter",
        "query_mode": "mix",
        "top_k": 12,
        "chunk_top_k": 20,
        "parent_expansion": "none",
        "rerank_enabled": False,
        "context_strategy": (
            "stable_unique_fill" if result["context_strategy_approved"] else "current_rows"
        ),
        "answer_strategy": (
            "grounded_answer" if result["replacement"]["replacement_approved"] else "current"
        ),
        "citation_validation_enabled": result["replacement"]["replacement_approved"],
        "max_repair_attempts": 1 if result["replacement"]["replacement_approved"] else 0,
        "replacement_approved": result["replacement"]["replacement_approved"],
        "replacement_gates_passed": result["replacement"]["replacement_gates_passed"],
        "selection_reason": result["replacement"]["selection_reason"],
    }
    (PHASE5_ROOT / "final_answer_strategy.json").write_text(
        json.dumps(final_strategy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifests = PHASE5_ROOT / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    (manifests / "result_manifest.json").write_text(
        json.dumps(
            {
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "phase": "Phase 5",
                "code_commit": _commit(),
                "candidate_pool_sha256": pool_sha,
                "prompt_bundle_sha256": bundle["sha256"],
                "context_strategy": final_strategy["context_strategy"],
                "answer_strategy": final_strategy["answer_strategy"],
                "baseline_answers_sha256": hashlib.sha256(
                    (out / "baseline" / "answers.jsonl").read_bytes()
                ).hexdigest(),
                "grounded_answers_sha256": hashlib.sha256(
                    (out / "grounded" / "answers.jsonl").read_bytes()
                ).hexdigest(),
                "comparison": result,
                "sanitization": {
                    "api_key_logged": False,
                    "authorization_header_logged": False,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(final_strategy, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())

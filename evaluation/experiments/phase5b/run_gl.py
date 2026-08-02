"""Phase 5B harness: GL0 (reuse), GL1 (inline citation), GL2 (repair), GL3 (guard)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from typing import Any

from .config import (
    CANDIDATE_POOL_PATH,
    CANDIDATE_POOL_SHA256,
    PDF_NAMES,
    PHASE4_ANSWER_CACHE,
    PHASE4_R0_ANSWERS,
    PHASE5B_ROOT,
    PYMUPDF_CHILDREN_DIR,
    read_jsonl,
    sha256_file,
)
from .lite import (
    apply_claim_guard,
    apply_repair_mapping,
    build_alias_map,
    build_evidence_block,
    build_whitelist_text,
    load_prompt,
    process_sentences,
    sha256_text,
    validate_repair_output,
)
from .metrics import (
    category_metrics,
    citation_metrics,
    claim_guard_metrics,
    engineering,
    gl1_to_gl2_gates,
    gl2_to_gl3_gates,
    marker_and_coverage_metrics,
    paired_bootstrap_metrics,
    repair_metrics,
    replacement_gates,
    safety_metrics,
)

FIXED_MODEL = "qwen-plus-2025-07-28"
INSUFFICIENT_EVIDENCE_ANSWER = "现有资料不足以回答该问题。"
DEV_QUESTIONS = ["S001", "S002", "S003", "S007", "S011", "S012", "S015", "S017", "C001", "N001"]


def _commit() -> str:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                cwd=str(PHASE5B_ROOT.parents[2]),
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


def _gold() -> tuple[dict[str, set[tuple[str, int]]], dict[str, set[str]]]:
    from .diagnostics import _gold_pages

    gold_pages = _gold_pages()
    mapping = json.loads(
        (
            PHASE5B_ROOT.parent
            / "parser_backend"
            / "fixed_model"
            / "comparison"
            / "evidence_mapping_p0.json"
        ).read_text(encoding="utf-8")
    )
    mapped: dict[str, set[str]] = {}
    for entry in mapping["entries"]:
        if entry["mapped"]:
            mapped.setdefault(entry["case_id"], set()).update(entry["mapped_child_ids"])
    return gold_pages, mapped


def _select_evidence(
    question: str,
    rows: list[dict[str, Any]],
    children_by_id: dict[str, dict[str, Any]],
    *,
    limit: int = 3,
) -> tuple[Any, list[dict[str, Any]], str]:
    from evaluation.experiments.phase4.parent_expansion.context_builder import build_context
    from evaluation.experiments.phase4.parent_expansion.expander import expand
    from evaluation.experiments.phase4.parent_expansion.parent_loader import ParentLoader
    from evaluation.experiments.phase5.run_experiment import _render_child
    from industrial_rag.evidence_policy import select_evidence

    chunks = [
        {"content": _render_child(children_by_id[row["child_chunk_id"]]), "file_path": row["document_id"]}
        for row in rows
        if row["child_chunk_id"] in children_by_id
    ]
    payload = {"data": {"chunks": chunks, "references": []}}
    decision = select_evidence(question, payload, limit=limit)
    selected_ids = {candidate.citation.chunk_id for candidate in decision.selected}
    selected_rows = [row for row in rows if row["child_chunk_id"] in selected_ids]
    context_text = ""
    if selected_rows:
        from evaluation.experiments.phase5.run_experiment import _enrich_row

        enriched = [_enrich_row(row, children_by_id) for row in selected_rows]
        expanded = expand(
            question,
            enriched,
            strategy="none",
            loader=ParentLoader(),
            max_context_tokens=6000,
        )
        context_text = build_context(expanded, max_context_tokens=6000)["context"]
    return decision, selected_rows, context_text


def _registry_from_rows(rows: list[dict[str, Any]], texts: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    for row in rows:
        child = texts.get(row["child_chunk_id"], {})
        registry[row["child_chunk_id"]] = {
            "document": str(row.get("document_id") or ""),
            "page": row.get("page"),
            "text": str(child.get("embedding_content") or child.get("content") or ""),
        }
    return registry


def _build_rows(by_q: dict[str, list[dict[str, Any]]], texts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for question_id, raw in by_q.items():
        rows = sorted(raw, key=lambda r: r["rank"] or 999)[:12]
        question = raw[0]["question"]
        decision, selected_rows, context_text = _select_evidence(question, rows, texts)
        registry = _registry_from_rows(selected_rows, texts)
        out[question_id] = {
            "question": question,
            "top_rows": rows,
            "selected_rows": selected_rows,
            "context_text": context_text,
            "registry": registry,
            "whitelist": set(registry),
            "alias_map": build_alias_map(registry),
            "policy_citations": [
                {
                    "chunk_id": c.citation.chunk_id,
                    "document_name": c.citation.source_file,
                    "page": c.citation.page_number,
                }
                for c in decision.selected
            ],
            "policy_rejected": not selected_rows,
        }
    return out


def _gl0_from_phase4(
    by_q: dict[str, dict[str, Any]],
    categories: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    phase4 = {r["question_id"]: r for r in read_jsonl(PHASE4_R0_ANSWERS)}
    cache_keys = {
        json.loads(line)["key"]
        for line in PHASE4_ANSWER_CACHE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    cache_entries = {
        json.loads(line)["key"]: json.loads(line)
        for line in PHASE4_ANSWER_CACHE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    needs_live: list[str] = []
    rows: list[dict[str, Any]] = []
    for question_id, info in by_q.items():
        source = phase4.get(question_id)
        if source is None:
            needs_live.append(question_id)
            continue
        if source.get("llm_called"):
            from evaluation.experiments.parser_backend.fixed_model_llm import FixedModelLLM
            from industrial_rag.lightrag_service import _generation_system_prompt

            probe = FixedModelLLM(
                model=FIXED_MODEL,
                api_key="",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                config_hash="phase4d_rerank_answers",
            )
            expected_key = probe._cache_key(
                _generation_system_prompt(info["context_text"]), info["question"]
            )
            if expected_key not in cache_keys:
                needs_live.append(question_id)
                continue
            cached_latency = float(cache_entries[expected_key].get("latency", 0.0))
            source_latency = cached_latency
        else:
            source_latency = 0.0
        processed = process_sentences(
            source["answer"], whitelist=set(), registry=info["registry"]
        )
        rows.append(
            {
                "question_id": question_id,
                "primary_category": categories.get(question_id, "未分类"),
                "group": "gl0",
                "answer": source["answer"],
                "citations": [
                    {
                        "chunk_id": c["chunk_id"],
                        "document_name": c["source_file"],
                        "page": c["page_number"],
                    }
                    for c in source["citations"]
                ],
                "refusal": bool(source["refusal"]),
                "refusal_reason": source.get("refusal_reason"),
                "processed": processed,
                "repair_attempted": False,
                "repair_valid": None,
                "repair_tokens": {"total_tokens": 0},
                "answer_text_unchanged": True,
                "claim_guard": None,
                "llm_called": bool(source["llm_called"]),
                "actual_model": source.get("actual_model") or [],
                "input_tokens": source.get("input_tokens", 0),
                "output_tokens": source.get("output_tokens", 0),
                "total_tokens": source.get("total_tokens", 0),
                "answer_latency": source_latency,
                "total_latency": source_latency,
                "status": "ok",
                "error": None,
                "cache_hit": bool(source.get("cache_hit")),
            }
        )
    return rows, needs_live


async def _run_gl0_live(
    llm: Any,
    question_ids: list[str],
    by_q: dict[str, dict[str, Any]],
    categories: dict[str, str],
) -> list[dict[str, Any]]:
    from industrial_rag.lightrag_service import _generation_system_prompt

    rows: list[dict[str, Any]] = []
    for question_id in question_ids:
        info = by_q[question_id]
        start = len(llm.calls)
        if info["policy_rejected"]:
            rows.append(
                _gl1_row(
                    question_id,
                    info,
                    categories,
                    answer=INSUFFICIENT_EVIDENCE_ANSWER,
                    llm_called=False,
                    call_slice=[],
                    answer_latency=0.0,
                )
            )
            continue
        answer = (await llm(info["question"], system_prompt=_generation_system_prompt(info["context_text"]))).strip()
        rows.append(
            _gl1_row(
                question_id,
                info,
                categories,
                answer=answer,
                llm_called=True,
                call_slice=llm.calls[start:],
                answer_latency=0.0,
            )
        )
    for row in rows:
        row["group"] = "gl0"
    return rows


def _gl1_row(
    question_id: str,
    info: dict[str, Any],
    categories: dict[str, str],
    *,
    answer: str,
    llm_called: bool,
    call_slice: list[dict[str, Any]],
    answer_latency: float,
    status: str = "ok",
    error: str | None = None,
) -> dict[str, Any]:
    if call_slice:
        answer_latency = sum(c.get("latency", 0.0) for c in call_slice)
    processed = process_sentences(
        answer,
        whitelist=info["whitelist"],
        registry=info["registry"],
        alias_map=info["alias_map"],
    )
    citations = [
        citation
        for sentence_info in processed["sentences"]
        for citation in sentence_info["citations"]
    ]
    # dedupe citations
    seen: set[tuple[str, int]] = set()
    unique_citations = []
    for citation in citations:
        key = (citation["chunk_id"], citation["page"])
        if key in seen:
            continue
        seen.add(key)
        unique_citations.append(citation)
    return {
        "question_id": question_id,
        "primary_category": categories.get(question_id, "未分类"),
        "group": "gl1",
        "answer": processed["clean_answer"],
        "citations": unique_citations,
        "refusal": answer.strip() == INSUFFICIENT_EVIDENCE_ANSWER
        or answer.strip().startswith(INSUFFICIENT_EVIDENCE_ANSWER),
        "refusal_reason": "evidence_policy_rejected"
        if not llm_called
        else ("model_refused" if answer.strip().startswith(INSUFFICIENT_EVIDENCE_ANSWER) else None),
        "processed": processed,
        "repair_attempted": False,
        "repair_valid": None,
        "repair_tokens": {"total_tokens": 0},
        "answer_text_unchanged": True,
        "claim_guard": None,
        "llm_called": llm_called,
        "actual_model": sorted({c["actual_model"] for c in call_slice}) if call_slice else [],
        "input_tokens": sum(c.get("input_tokens", 0) for c in call_slice),
        "output_tokens": sum(c.get("output_tokens", 0) for c in call_slice),
        "total_tokens": sum(c.get("total_tokens", 0) for c in call_slice),
        "answer_latency": answer_latency,
        "total_latency": answer_latency,
        "status": status,
        "error": error,
        "cache_hit": any(c.get("cache_hit") for c in call_slice),
    }


async def _run_gl1(
    llm: Any,
    by_q: dict[str, dict[str, Any]],
    categories: dict[str, str],
    prompt: str,
    *,
    question_ids: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for question_id in question_ids:
        info = by_q[question_id]
        question = info["question"]
        start = len(llm.calls)
        if info["policy_rejected"]:
            rows.append(
                _gl1_row(
                    question_id,
                    info,
                    categories,
                    answer=INSUFFICIENT_EVIDENCE_ANSWER,
                    llm_called=False,
                    call_slice=[],
                    answer_latency=0.0,
                )
            )
            continue
        system_prompt = (
            prompt.replace("{question}", question)
            .replace("{whitelist}", build_whitelist_text(info["registry"]))
            .replace("{evidence}", build_evidence_block(info["registry"]))
        )
        try:
            answer = (await llm(question, system_prompt=system_prompt)).strip()
            rows.append(
                _gl1_row(
                    question_id,
                    info,
                    categories,
                    answer=answer,
                    llm_called=True,
                    call_slice=llm.calls[start:],
                    answer_latency=0.0,
                )
            )
        except Exception as error:
            rows.append(
                _gl1_row(
                    question_id,
                    info,
                    categories,
                    answer=INSUFFICIENT_EVIDENCE_ANSWER,
                    llm_called=True,
                    call_slice=[],
                    answer_latency=0.0,
                    status="error",
                    error=f"{type(error).__name__}: {error}",
                )
            )
    return rows


async def _run_gl2(
    llm: Any,
    gl1_rows: list[dict[str, Any]],
    by_q: dict[str, dict[str, Any]],
    categories: dict[str, str],
    repair_prompt: str,
) -> list[dict[str, Any]]:
    out_rows: list[dict[str, Any]] = []
    for row in gl1_rows:
        question_id = row["question_id"]
        info = by_q[question_id]
        processed = row["processed"]
        coverage = processed.get("coverage") or {}
        stats = processed.get("marker_stats") or {}
        needs_repair = (
            coverage.get("key_claims", 0) > coverage.get("covered_key_claims", 0)
            or stats.get("invalid_chunk_markers", 0) > 0
            or stats.get("malformed_markers", 0) > 0
        )
        if not needs_repair or row.get("refusal"):
            out_rows.append(dict(row))
            continue
        start = len(llm.calls)
        question = info["question"]
        system_prompt = (
            repair_prompt.replace("{question}", question)
            .replace("{whitelist}", build_whitelist_text(info["registry"]))
            .replace("{evidence}", build_evidence_block(info["registry"]))
            .replace("{answer}", row["answer"])
            .replace(
                "{sentences}",
                "\n".join(
                    f"{s['sentence_index']}: {s['clean_sentence']}"
                    for s in processed["sentences"]
                ),
            )
        )
        hash_before = sha256_text(row["answer"])
        try:
            raw = (await llm(question, system_prompt=system_prompt)).strip()
            latency = sum(c.get("latency", 0.0) for c in llm.calls[start:])
            mapping, errors = validate_repair_output(raw)
            repair_valid = mapping is not None
            if mapping is not None:
                processed = apply_repair_mapping(
                    processed,
                    mapping,
                    whitelist=info["whitelist"],
                    registry=info["registry"],
                    alias_map=info["alias_map"],
                    answer_text_hash_before=hash_before,
                )
                repair_valid = not processed.get("repair_errors")
            else:
                processed = dict(processed)
                processed["repair_errors"] = errors
                processed["answer_text_hash_before"] = hash_before
                processed["answer_text_hash_after"] = hash_before
                processed["answer_text_unchanged"] = True
            new_row = dict(row)
            new_row["group"] = "gl2"
            new_row["processed"] = processed
            new_row["repair_attempted"] = True
            new_row["repair_valid"] = repair_valid
            new_row["repair_tokens"] = {
                "total_tokens": sum(c.get("total_tokens", 0) for c in llm.calls[start:])
            }
            new_row["repair_latency"] = latency
            new_row["answer_text_unchanged"] = processed.get("answer_text_unchanged", True)
            new_row["total_latency"] = (row.get("total_latency") or 0) + latency
            new_row["total_tokens"] = (row.get("total_tokens") or 0) + sum(
                c.get("total_tokens", 0) for c in llm.calls[start:]
            )
            new_row["cache_hit"] = bool(row.get("cache_hit")) or any(
                c.get("cache_hit") for c in llm.calls[start:]
            )
            # refresh citations from processed sentences
            citations = [
                citation
                for s in processed["sentences"]
                for citation in s["citations"]
            ]
            seen: set[tuple[str, int]] = set()
            unique_citations = []
            for citation in citations:
                key = (citation["chunk_id"], citation["page"])
                if key in seen:
                    continue
                seen.add(key)
                unique_citations.append(citation)
            new_row["citations"] = unique_citations
            out_rows.append(new_row)
        except Exception as error:
            new_row = dict(row)
            new_row["group"] = "gl2"
            new_row["repair_attempted"] = True
            new_row["repair_valid"] = False
            new_row["repair_tokens"] = {
                "total_tokens": sum(c.get("total_tokens", 0) for c in llm.calls[start:])
            }
            new_row["repair_latency"] = 0.0
            new_row["error"] = f"{type(error).__name__}: {error}"
            new_row["answer_text_unchanged"] = True
            out_rows.append(new_row)
    return out_rows


def _run_gl3(gl2_rows: list[dict[str, Any]], categories: dict[str, str]) -> list[dict[str, Any]]:
    out_rows: list[dict[str, Any]] = []
    for row in gl2_rows:
        guard = apply_claim_guard(row["processed"])
        citations = [
            citation
            for sentence_info in guard["kept_sentences"]
            for citation in sentence_info["citations"]
        ]
        new_row = dict(row)
        new_row["group"] = "gl3"
        new_row["claim_guard"] = guard
        new_row["answer"] = guard["answer"]
        new_row["citations"] = citations
        new_row["refusal"] = guard["status"] == "insufficient_evidence"
        new_row["refusal_reason"] = (
            "all_claims_pruned" if new_row["refusal"] else row.get("refusal_reason")
        )
        out_rows.append(new_row)
    return out_rows


def _group_metrics(
    rows: list[dict[str, Any]],
    *,
    gold_pages: dict[str, set[tuple[str, int]]],
    mapped: dict[str, set[str]],
) -> dict[str, Any]:
    return {
        "citation": citation_metrics(rows, gold_pages=gold_pages, mapped=mapped),
        "marker": marker_and_coverage_metrics(rows),
        "claim_guard": claim_guard_metrics(rows),
        "repair": repair_metrics(rows),
        "engineering": engineering(rows),
        "categories": category_metrics(rows, gold_pages=gold_pages),
        "safety": safety_metrics(rows, gold_pages=gold_pages),
        "rows": rows,
    }


def _write_answers(rows: list[dict[str, Any]], group: str) -> None:
    out_dir = PHASE5B_ROOT / "results" / group
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "answers.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )


async def main_async(*, dev_only: bool = False) -> int:
    if os.environ.get("IRA_PHASE5B_RUN") != "1":
        print("IRA_PHASE5B_RUN != 1; refusing Phase 5B LLM calls")
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
    question_ids = DEV_QUESTIONS if dev_only else sorted(by_q)
    by_q_filtered = {q: by_q[q] for q in question_ids}
    texts = _load_texts()
    gold_pages, mapped = _gold()
    built = _build_rows(by_q_filtered, texts)
    from evaluation.experiments.parser_backend.config import QUESTION_CATEGORIES
    from evaluation.experiments.parser_backend.fixed_model_llm import FixedModelLLM

    llm = FixedModelLLM(
        model=FIXED_MODEL,
        api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        enable_thinking=False,
        cache_path=PHASE5B_ROOT / "cache" / "phase5b_answers.jsonl",
        config_hash="phase5b_gl_v1",
    )
    inline_prompt = load_prompt("inline_citation_prompt.txt")
    repair_prompt = load_prompt("citation_repair_prompt.txt")

    if dev_only:
        gl1_rows = await _run_gl1(
            llm, built, QUESTION_CATEGORIES, inline_prompt, question_ids=question_ids
        )
        summary = {
            question_id: {
                "refusal": row["refusal"],
                "marker_stats": (row.get("processed") or {}).get("marker_stats"),
                "coverage": (row.get("processed") or {}).get("coverage"),
            }
            for question_id, row in zip(question_ids, gl1_rows, strict=True)
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    # GL0: reuse Phase 4 R0 if all cache keys verify
    gl0_rows, needs_live = _gl0_from_phase4(built, QUESTION_CATEGORIES)
    if needs_live:
        gl0_live = await _run_gl0_live(llm, needs_live, built, QUESTION_CATEGORIES)
        gl0_rows.extend(gl0_live)
    gl0_verification = {
        "reused": not needs_live,
        "live_rerun_questions": needs_live,
    }
    _write_answers(gl0_rows, "gl0")

    # GL1
    gl1_rows = await _run_gl1(llm, built, QUESTION_CATEGORIES, inline_prompt, question_ids=question_ids)
    _write_answers(gl1_rows, "gl1")

    gl0_metrics = _group_metrics(gl0_rows, gold_pages=gold_pages, mapped=mapped)
    gl1_metrics = _group_metrics(gl1_rows, gold_pages=gold_pages, mapped=mapped)
    gates_gl1 = gl1_to_gl2_gates(
        {"citation": gl0_metrics["citation"], "safety": gl0_metrics["safety"]},
        {"citation": gl1_metrics["citation"], "marker": gl1_metrics["marker"], "safety": gl1_metrics["safety"]},
    )
    gl2_rows: list[dict[str, Any]] = []
    gl2_metrics: dict[str, Any] | None = None
    gates_gl2: dict[str, Any] | None = None
    if gates_gl1["passed"]:
        gl2_rows = await _run_gl2(
            llm, gl1_rows, built, QUESTION_CATEGORIES, repair_prompt
        )
        _write_answers(gl2_rows, "gl2")
        gl2_metrics = _group_metrics(gl2_rows, gold_pages=gold_pages, mapped=mapped)
        gates_gl2 = gl2_to_gl3_gates(
            {"engineering": gl0_metrics["engineering"], "citation": gl0_metrics["citation"]},
            {"engineering": gl2_metrics["engineering"], "repair": gl2_metrics["repair"], "citation": gl2_metrics["citation"], "rows": gl2_rows},
        )
    else:
        gl2_metrics = None
        gates_gl2 = None

    gl3_rows: list[dict[str, Any]] = []
    gl3_metrics: dict[str, Any] | None = None
    final_candidate = "gl1"
    if gates_gl1["passed"] and gates_gl2 is not None and gates_gl2["passed"]:
        gl3_rows = _run_gl3(gl2_rows, QUESTION_CATEGORIES)
        _write_answers(gl3_rows, "gl3")
        gl3_metrics = _group_metrics(gl3_rows, gold_pages=gold_pages, mapped=mapped)
        final_candidate = "gl3"
    elif gates_gl1["passed"] and gates_gl2 is not None and not gates_gl2["passed"]:
        final_candidate = "gl2"
    candidate_metrics = {
        "gl1": gl1_metrics,
        "gl2": gl2_metrics,
        "gl3": gl3_metrics,
    }[final_candidate]
    replacement = replacement_gates(
        gl0=gl0_metrics,
        candidate=candidate_metrics,
        safety0=gl0_metrics["safety"],
        safety_c=candidate_metrics["safety"],
    )
    if final_candidate != "gl3":
        replacement = {
            **replacement,
            "replacement_approved": False,
            "replacement_gates_passed": False,
            "selection_reason": (
                f"GL3 not reached (GL1/GL2 gates); final candidate {final_candidate} "
                "cannot enable claim-level guard"
            ),
        }
    bootstrap = {
        "gl0_vs_gl1": paired_bootstrap_metrics(gl0_rows, gl1_rows, gold_pages=gold_pages, mapped=mapped),
        "gl0_vs_gl2": (
            paired_bootstrap_metrics(gl0_rows, gl2_rows, gold_pages=gold_pages, mapped=mapped)
            if gl2_rows
            else None
        ),
        "gl0_vs_gl3": (
            paired_bootstrap_metrics(gl0_rows, gl3_rows, gold_pages=gold_pages, mapped=mapped)
            if gl3_rows
            else None
        ),
    }
    comparison = {
        "gl0": {
            "citation_metrics": gl0_metrics["citation"],
            "marker_metrics": gl0_metrics["marker"],
            "engineering": gl0_metrics["engineering"],
            "categories": gl0_metrics["categories"],
            "safety": gl0_metrics["safety"],
            "reuse_verification": gl0_verification,
        },
        "gl1": {
            "citation_metrics": gl1_metrics["citation"],
            "marker_metrics": gl1_metrics["marker"],
            "engineering": gl1_metrics["engineering"],
            "categories": gl1_metrics["categories"],
            "safety": gl1_metrics["safety"],
        },
        "gl2": (
            {
                "citation_metrics": gl2_metrics["citation"],
                "marker_metrics": gl2_metrics["marker"],
                "repair_metrics": gl2_metrics["repair"],
                "engineering": gl2_metrics["engineering"],
                "categories": gl2_metrics["categories"],
                "safety": gl2_metrics["safety"],
            }
            if gl2_metrics
            else None
        ),
        "gl3": (
            {
                "citation_metrics": gl3_metrics["citation"],
                "marker_metrics": gl3_metrics["marker"],
                "claim_guard_metrics": gl3_metrics["claim_guard"],
                "engineering": gl3_metrics["engineering"],
                "categories": gl3_metrics["categories"],
                "safety": gl3_metrics["safety"],
            }
            if gl3_metrics
            else None
        ),
        "gates": {
            "gl1_to_gl2": gates_gl1,
            "gl2_to_gl3": gates_gl2,
        },
        "bootstrap": bootstrap,
        "final_candidate": final_candidate,
        "replacement": replacement,
    }
    metrics_dir = PHASE5B_ROOT / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    (metrics_dir / "comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    validation_dir = PHASE5B_ROOT / "validation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    (validation_dir / "validation_summary.json").write_text(
        json.dumps(
            {
                "marker": gl1_metrics["marker"],
                "repair": gl2_metrics["repair"] if gl2_metrics else None,
                "claim_guard": gl3_metrics["claim_guard"] if gl3_metrics else None,
                "gates": comparison["gates"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    final_strategy = {
        "context_strategy": "current_rows",
        "answer_strategy": (
            "grounded_answer_lite" if replacement["replacement_approved"] else "current"
        ),
        "citation_mode": (
            "inline_chunk_marker" if replacement["replacement_approved"] else "current"
        ),
        "citation_only_repair": bool(replacement["replacement_approved"]),
        "claim_level_guard": bool(replacement["replacement_approved"]),
        "replacement_approved": bool(replacement["replacement_approved"]),
        "replacement_gates_passed": bool(replacement["replacement_gates_passed"]),
        "selection_reason": replacement["selection_reason"],
        "final_candidate": final_candidate,
    }
    (PHASE5B_ROOT / "final_answer_strategy.json").write_text(
        json.dumps(final_strategy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifests = PHASE5B_ROOT / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    (manifests / "result_manifest.json").write_text(
        json.dumps(
            {
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "phase": "Phase 5B",
                "code_commit": _commit(),
                "candidate_pool_sha256": pool_sha,
                "prompts": {
                    "inline_citation_prompt": sha256_text(inline_prompt),
                    "citation_repair_prompt": sha256_text(repair_prompt),
                },
                "final_strategy": final_strategy,
                "comparison": comparison,
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
    dev_only = "--dev" in sys.argv
    return asyncio.run(main_async(dev_only=dev_only))


if __name__ == "__main__":
    sys.exit(main())

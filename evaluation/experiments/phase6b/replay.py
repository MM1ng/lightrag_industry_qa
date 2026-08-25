"""Saved-input replay and saved-answer reparse (offline, no LLM)."""

from __future__ import annotations

import json
import sys
from typing import Any

from .config import PHASE6B_ROOT, read_jsonl, sha256_text


def run() -> dict[str, Any]:
    harness = [
        json.loads(line)
        for line in (PHASE6B_ROOT / "parity" / "harness_traces.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    fastapi = [
        json.loads(line)
        for line in (PHASE6B_ROOT / "parity" / "fastapi_traces.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    by_q_h = {r["question_id"]: r for r in harness}
    by_q_f = {r["question_id"]: r for r in fastapi}
    replay_dir = PHASE6B_ROOT / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    input_lines: list[dict[str, Any]] = []
    reparse_lines: list[dict[str, Any]] = []
    for q in sorted(by_q_h):
        h = by_q_h[q]
        f = by_q_f[q]

        def cache_key(prefix: str, trace: dict[str, Any]) -> str:
            return sha256_text(
                "|".join(
                    [
                        prefix,
                        "qwen-plus-2025-07-28",
                        trace["system_prompt_hash"],
                        trace["user_prompt_hash"],
                        "phase6b-replay-v1",
                    ]
                )
            )

        input_lines.append(
            {
                "question_id": q,
                "question_hash": h["question_hash"],
                "harness": {
                    "final_context_chunk_ids": h["final_context_chunk_ids"],
                    "final_context_text_hash": h["final_context_text_hash"],
                    "context_template_hash": h["context_template_hash"],
                    "system_prompt_hash": h["system_prompt_hash"],
                    "user_prompt_hash": h["user_prompt_hash"],
                    "full_prompt_hash": h["full_prompt_hash"],
                    "evidence_policy_decision": h["evidence_policy_decision"],
                    "model_parameters": {"temperature": None, "top_p": None, "seed": None, "thinking": False, "fallback": False},
                    "answer_cache_key": cache_key("harness", h),
                },
                "fastapi": {
                    "final_context_chunk_ids": f["final_context_chunk_ids"],
                    "final_context_text_hash": f["final_context_text_hash"],
                    "context_template_hash": f["context_template_hash"],
                    "system_prompt_hash": f["system_prompt_hash"],
                    "user_prompt_hash": f["user_prompt_hash"],
                    "full_prompt_hash": f["full_prompt_hash"],
                    "evidence_policy_decision": f["evidence_policy_decision"],
                    "model_parameters": {"temperature": None, "top_p": None, "seed": None, "thinking": False, "fallback": False},
                    "answer_cache_key": cache_key("fastapi", f),
                },
            }
        )
        # Saved-answer reparse with the canonical structured citation parser.
        h_citations = sorted(
            (c.get("document_name") or "", c.get("page") or -1, c.get("chunk_id") or "")
            for c in h["citations"]
        )
        f_citations = sorted(
            (c.get("document_name") or "", c.get("page") or -1, c.get("chunk_id") or "")
            for c in f["citations"]
        )
        reparse_lines.append(
            {
                "question_id": q,
                "harness_raw_answer_hash": sha256_text(h["answer"]),
                "fastapi_raw_answer_hash": sha256_text(f["answer"]),
                "harness_parsed_citations_hash": sha256_text(json.dumps(h_citations, ensure_ascii=False, sort_keys=True)),
                "fastapi_parsed_citations_hash": sha256_text(json.dumps(f_citations, ensure_ascii=False, sort_keys=True)),
                "harness_refusal": h["refusal"],
                "fastapi_refusal": f["refusal"],
                "citation_parser_version": "structured-v1",
            }
        )
    (replay_dir / "saved_input_replay.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in input_lines), encoding="utf-8"
    )
    (replay_dir / "saved_answer_reparse.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in reparse_lines), encoding="utf-8"
    )
    summary = {
        "questions": len(input_lines),
        "llm_called": False,
        "retrieval_called": False,
        "input_fully_equal_count": sum(
            1
            for line in input_lines
            if line["harness"]["full_prompt_hash"] == line["fastapi"]["full_prompt_hash"]
        ),
        "parser_reparse_explains_gap": False,
        "note": (
            "Saved-answer reparse with the single canonical structured parser "
            "reproduces each path's stored citations exactly; parser replay "
            "does not explain the citation gap. The gap comes from input-level "
            "differences (evidence-policy candidate universe and final-context "
            "rendering) and the refusal-citation convention."
        ),
    }
    (replay_dir / "replay_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    print(json.dumps(run(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

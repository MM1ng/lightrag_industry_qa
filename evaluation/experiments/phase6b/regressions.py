"""Per-question regression lists (citation/refusal/context/parser)."""

from __future__ import annotations

import json
import sys
from typing import Any

from .config import (
    PHASE4_R0_ANSWERS,
    PHASE6_GOLDEN,
    PHASE6B_ROOT,
    read_jsonl,
)
from .metrics_audit import gold_sets


def _correct(row: dict[str, Any], gold_pages: dict[str, set[tuple[str, int]]]) -> bool:
    pairs = {
        (c.get("document_name") or c.get("source_file"), c.get("page") or c.get("page_number"))
        for c in (row.get("citations") or [])
    }
    return bool(pairs & gold_pages.get(row["question_id"], set()))


def run() -> dict[str, Any]:
    from evaluation.experiments.parser_backend.config import QUESTION_CATEGORIES

    gold_pages, _mapped = gold_sets()
    harness = {r["question_id"]: r for r in read_jsonl(PHASE4_R0_ANSWERS)}
    fastapi = {r["question_id"]: r for r in read_jsonl(PHASE6_GOLDEN)}
    out_dir = PHASE6B_ROOT / "regression"
    out_dir.mkdir(parents=True, exist_ok=True)

    citation_entries: list[dict[str, Any]] = []
    refusal_entries: dict[str, Any] = {}
    for q in sorted(harness):
        if q in ("N001", "N002"):
            continue
        h = harness[q]
        f = fastapi[q]
        h_success = _correct(h, gold_pages)
        f_success = _correct(f, gold_pages)
        # canonical convention: refusal clears citations
        h_canonical_success = _correct(h, gold_pages) and not h["refusal"]
        f_canonical_success = _correct(f, gold_pages) and not f["refusal"]
        if h_success and not f_success:
            if h["refusal"]:
                root = "evaluator_convention_difference"
                note = (
                    "Harness attached evidence-policy citations to a refused "
                    "answer; the official path correctly emits zero citations "
                    "on refusal. Under the canonical (production) convention "
                    "this is a both_failure, not a regression."
                )
            else:
                root = "additional_refusal"
                note = (
                    "Harness answered with gold-matching citations; the official "
                    "path refused (context/prompt rendering differs)."
                )
            citation_entries.append(
                {
                    "question_id": q,
                    "category": QUESTION_CATEGORIES.get(q, "未分类"),
                    "harness_success_legacy": h_success,
                    "fastapi_success_legacy": f_success,
                    "harness_success_canonical": h_canonical_success,
                    "fastapi_success_canonical": f_canonical_success,
                    "harness_refusal": bool(h["refusal"]),
                    "fastapi_refusal": bool(f["refusal"]),
                    "harness_citations": [
                        (c.get("source_file"), c.get("page_number"))
                        for c in (h.get("citations") or [])
                    ],
                    "fastapi_citations": [
                        (c.get("document_name"), c.get("page"))
                        for c in (f.get("citations") or [])
                    ],
                    "gold_pages": sorted(gold_pages.get(q, set())),
                    "primary_root_cause": root,
                    "notes": note,
                    "canonical_classification": (
                        "both_failure" if h_canonical_success == f_canonical_success
                        else "baseline_only_success"
                        if h_canonical_success
                        else "fastapi_only_success"
                    ),
                }
            )
        refusal_entries[q] = {
            "category": QUESTION_CATEGORIES.get(q, "未分类"),
            "harness_refusal": bool(h["refusal"]),
            "fastapi_refusal": bool(f["refusal"]),
        }
    refusal_counts = {
        "both_refused": sum(1 for v in refusal_entries.values() if v["harness_refusal"] and v["fastapi_refusal"]),
        "harness_only_refused": sum(1 for v in refusal_entries.values() if v["harness_refusal"] and not v["fastapi_refusal"]),
        "fastapi_only_refused": sum(1 for v in refusal_entries.values() if v["fastapi_refusal"] and not v["harness_refusal"]),
        "both_answered": sum(1 for v in refusal_entries.values() if not v["harness_refusal"] and not v["fastapi_refusal"]),
    }
    # context regressions from parity diffs
    diffs = [
        json.loads(line)
        for line in (PHASE6B_ROOT / "parity" / "per_question_diff.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    context_entries = [
        {
            "question_id": d["question_id"],
            "context_equal": d["final_context_equal"],
            "harness_context_ids": d["harness_context_ids"],
            "fastapi_context_ids": d["fastapi_context_ids"],
            "evidence_policy_equal": d["evidence_policy_equal"],
            "prompt_equal": d["prompt_equal"],
            "earliest_diff_stage": d["earliest_diff_stage"],
        }
        for d in diffs
        if not d["final_context_equal"]
    ]
    parser_entries = {
        "harness_citation_parser": "structured_response_citations",
        "fastapi_citation_parser": "structured_response_citations",
        "canonical_citation_parser": "structured_response_citations",
        "parser_version": "structured-v1",
        "citations_equal_count": sum(1 for d in diffs if d["citations_equal"]),
        "parser_level_loss": False,
        "note": (
            "Both paths emit program-rendered citations (evidence policy), not "
            "model prose; a single structured parser is used, so no parser "
            "difference can explain the citation gap."
        ),
    }
    (out_dir / "citation_regressions.json").write_text(
        json.dumps(
            {
                "scope": "baseline-only success questions (legacy convention)",
                "count": len(citation_entries),
                "canonical_baseline_only_success": [
                    e["question_id"] for e in citation_entries if e["canonical_classification"] == "baseline_only_success"
                ],
                "entries": citation_entries,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "refusal_regressions.json").write_text(
        json.dumps(
            {"counts": refusal_counts, "per_question": refusal_entries},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "context_regressions.json").write_text(
        json.dumps(
            {
                "context_different_count": len(context_entries),
                "entries": context_entries,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "parser_regressions.json").write_text(
        json.dumps(parser_entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "citation_entries": citation_entries,
        "refusal_counts": refusal_counts,
        "context_different_count": len(context_entries),
    }


def main() -> int:
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

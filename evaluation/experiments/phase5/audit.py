"""Phase 5 closeout audits: duplicate retrieval rows and citation metrics."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .config import (
    CANDIDATE_POOL_PATH,
    EVIDENCE_MAPPING_PATH,
    PHASE4_ANSWERS_CN0,
    PHASE4_ANSWERS_R1,
    PHASE5_ROOT,
    read_jsonl,
)

NEGATIVE_IDS = ("N001", "N002")


def _gold_pages_and_mapped() -> tuple[dict[str, set[tuple[str, int]]], dict[str, set[str]]]:
    from evaluation.experiments.parser_backend.metrics import load_gold

    gold = load_gold()
    pages = {
        case.case_id: {(c.source_file, c.page_number) for c in case.expected_citations}
        for case in gold
    }
    mapping = json.loads(EVIDENCE_MAPPING_PATH.read_text(encoding="utf-8"))
    mapped: dict[str, set[str]] = {}
    for entry in mapping["entries"]:
        if entry["mapped"]:
            mapped.setdefault(entry["case_id"], set()).update(entry["mapped_child_ids"])
    return pages, mapped


def audit_duplicates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_q: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_q.setdefault(row["question_id"], []).append(row)
    per_question: dict[str, Any] = {}
    duplicate_rows_total = 0
    affected_questions: list[str] = []
    duplicate_chunk_ids_global: Counter[str] = Counter()
    for question_id in sorted(by_q):
        q_rows = sorted(by_q[question_id], key=lambda r: r["rank"] or 999)
        seen: dict[str, list[dict[str, Any]]] = {}
        dup_entries: list[dict[str, Any]] = []
        for row in q_rows:
            chunk_id = row["child_chunk_id"]
            if chunk_id in seen:
                first = seen[chunk_id]
                dup_entries.append(
                    {
                        "chunk_id": chunk_id,
                        "duplicate_rank": row["rank"],
                        "first_rank": first["rank"],
                        "document_id": row["document_id"],
                        "page": row["page"],
                        "text_hash": row["child_text_hash"],
                        "first_text_hash": first["child_text_hash"],
                        "original_rank": row["rank"],
                    }
                )
                duplicate_rows_total += 1
                duplicate_chunk_ids_global[chunk_id] += 1
            else:
                seen[chunk_id] = row
        if dup_entries:
            affected_questions.append(question_id)
        per_question[question_id] = {
            "row_count": len(q_rows),
            "unique_chunk_id_count": len({r["child_chunk_id"] for r in q_rows}),
            "duplicate_chunk_ids": sorted({e["chunk_id"] for e in dup_entries}),
            "duplicate_rows": dup_entries,
            "affected": bool(dup_entries),
        }
    return {
        "scope": "all 50 frozen questions",
        "source": str(CANDIDATE_POOL_PATH),
        "row_count_total": len(rows),
        "duplicate_row_count": duplicate_rows_total,
        "affected_question_count": len(affected_questions),
        "affected_questions": affected_questions,
        "duplicate_chunk_ids_global": dict(duplicate_chunk_ids_global),
        "likely_cause": (
            "mix query merges multiple recall channels (local/global/naive) into "
            "chunk_top_k=20; the same chunk_id can be recalled by several channels "
            "at different ranks. The Phase 4 freeze step did not deduplicate by "
            "chunk_id, so the frozen pool keeps duplicate rows with identical "
            "text_hash/document/page."
        ),
        "rrf_dedup_missing": (
            "Observed rows share chunk_id/text_hash/page but differ in rank; "
            "consistent with an RRF/mix merge that lacked a final chunk_id "
            "deduplication pass."
        ),
        "per_question": per_question,
    }


def _phase4_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    gold_pages, mapped = _gold_pages_and_mapped()
    answerable = [r for r in rows if r["question_id"] not in NEGATIVE_IDS]
    negatives = [r for r in rows if r["question_id"] in NEGATIVE_IDS]
    n = len(answerable)
    correct_rows = 0
    precision_sum = 0.0
    recall_sum = 0.0
    traceable_rows = 0
    total_citations = 0
    wrong_citations = 0
    false_rejections = 0
    answered_without_evidence = 0
    for row in answerable:
        expected = gold_pages.get(row["question_id"], set())
        citation_ids = {(c.get("source_file"), c.get("page_number")) for c in row["citations"]}
        correct = len(citation_ids & expected)
        if row["citations"]:
            correct_rows += int(correct >= 1)
            precision_sum += correct / len(row["citations"])
            total_citations += len(row["citations"])
            wrong_citations += len(row["citations"]) - correct
            traceable_rows += int(all(c.get("chunk_id") for c in row["citations"]))
        else:
            answered_without_evidence += int(not row["refusal"])
        if row["refusal"]:
            false_rejections += 1
        if expected:
            recall_sum += correct / len(expected)
    n_neg = len(negatives)
    neg_refusals = sum(1 for r in negatives if r["refusal"])
    return {
        "answerable_questions": n,
        "negative_questions": n_neg,
        "citation_accuracy": {
            "numerator": correct_rows,
            "denominator": n,
            "decimal": round(correct_rows / n, 4),
        },
        "citation_precision_sum": round(precision_sum, 4),
        "citation_recall_sum": round(recall_sum, 4),
        "citation_traceability": {
            "numerator": traceable_rows,
            "denominator": n,
            "decimal": round(traceable_rows / n, 4),
        },
        "unsupported_citation_reference_rate": {
            "numerator": wrong_citations,
            "denominator": total_citations,
            "decimal": round(wrong_citations / total_citations, 4) if total_citations else 0,
        },
        "false_rejection_rate": {
            "numerator": false_rejections,
            "denominator": n,
            "decimal": round(false_rejections / n, 4),
        },
        "insufficient_evidence_rejection_rate": {
            "numerator": neg_refusals,
            "denominator": n_neg,
            "decimal": round(neg_refusals / n_neg, 4),
        },
        "unsupported_answer_rate": {
            "numerator": n_neg - neg_refusals,
            "denominator": n_neg,
            "decimal": round((n_neg - neg_refusals) / n_neg, 4),
        },
        "answered_without_evidence_rate": {
            "numerator": answered_without_evidence,
            "denominator": n,
            "decimal": round(answered_without_evidence / n, 4),
        },
    }


def build_metrics_definition() -> dict[str, Any]:
    """Canonical metric definitions based on the actual Phase 4D-R2 code."""
    counts_cn0 = _phase4_counts(read_jsonl(PHASE4_ANSWERS_CN0))
    counts_r1 = _phase4_counts(read_jsonl(PHASE4_ANSWERS_R1))
    counts = counts_r1
    definitions: list[dict[str, Any]] = [
        {
            "metric_name": "answer_citation_accuracy",
            "historical_name": "Citation Accuracy",
            "definition": (
                "Per-question: share of answerable questions whose answer has at "
                "least one citation whose (document, page) pair is in the gold set."
            ),
            "numerator": "questions with >=1 gold-matching citation",
            "denominator": "answerable questions (48)",
            "included_questions": "S/D/C (48 answerable)",
            "excluded_questions": "N001/N002",
            "range": [0, 1],
            "higher_is_better": True,
            "complement_metric": None,
            "raw_counts": counts["citation_accuracy"],
            "phase4d_r2_r1_value": counts["citation_accuracy"]["decimal"],
            "phase4d_r2_cn0_value": counts_cn0["citation_accuracy"]["decimal"],
        },
        {
            "metric_name": "answer_citation_precision",
            "historical_name": "Citation Precision",
            "definition": (
                "Per-question mean of (gold-matching citation count / total cited "
                "count) over answerable questions; rows without citations count 0."
            ),
            "numerator": "sum over questions of correct/total cited",
            "denominator": "answerable questions (48)",
            "included_questions": "S/D/C (48 answerable)",
            "excluded_questions": "N001/N002",
            "range": [0, 1],
            "higher_is_better": True,
            "complement_metric": "unsupported_citation_reference_rate (per-citation, not strictly complementary)",
            "raw_counts": {
                "numerator": counts["citation_precision_sum"],
                "denominator": counts["answerable_questions"],
                "decimal": round(
                    counts["citation_precision_sum"] / counts["answerable_questions"], 4
                ),
            },
            "phase4d_r2_r1_value": round(
                counts["citation_precision_sum"] / counts["answerable_questions"], 4
            ),
        },
        {
            "metric_name": "answer_citation_recall",
            "historical_name": "Citation Recall",
            "definition": (
                "Per-question mean of (gold-matching citation count / expected gold "
                "citation count) over answerable questions."
            ),
            "numerator": "sum over questions of correct/expected",
            "denominator": "answerable questions (48)",
            "included_questions": "S/D/C (48 answerable)",
            "excluded_questions": "N001/N002",
            "range": [0, 1],
            "higher_is_better": True,
            "complement_metric": None,
            "raw_counts": {
                "numerator": counts["citation_recall_sum"],
                "denominator": counts["answerable_questions"],
                "decimal": round(
                    counts["citation_recall_sum"] / counts["answerable_questions"], 4
                ),
            },
            "phase4d_r2_r1_value": round(
                counts["citation_recall_sum"] / counts["answerable_questions"], 4
            ),
        },
        {
            "metric_name": "citation_traceability",
            "historical_name": "Citation Traceability",
            "definition": (
                "Per-question: share of answerable questions where every emitted "
                "citation carries a chunk_id."
            ),
            "numerator": "questions with all citations carrying chunk_id",
            "denominator": "answerable questions (48)",
            "included_questions": "S/D/C (48 answerable)",
            "excluded_questions": "N001/N002",
            "range": [0, 1],
            "higher_is_better": True,
            "complement_metric": None,
            "raw_counts": counts["citation_traceability"],
            "phase4d_r2_r1_value": counts["citation_traceability"]["decimal"],
            "phase4d_r2_cn0_value": counts_cn0["citation_traceability"]["decimal"],
        },
        {
            "metric_name": "citation_traceability_emitted",
            "historical_name": None,
            "definition": (
                "Phase 5 structural definition: among answerable questions that "
                "emitted at least one citation, the share whose every citation "
                "carries a chunk_id. Rows that refused (zero citations) are "
                "excluded from the denominator; refusal is evaluated separately "
                "by rejection metrics."
            ),
            "numerator": "questions with >=1 citation and all citations carry chunk_id",
            "denominator": "answerable questions with >=1 emitted citation",
            "included_questions": "S/D/C (48 answerable) with emitted citations",
            "excluded_questions": "N001/N002 and answerable refusals (0 citations)",
            "range": [0, 1],
            "higher_is_better": True,
            "complement_metric": None,
            "raw_counts": {
                "numerator": None,
                "denominator": None,
                "decimal": None,
                "note": "computed per Phase 5 arm; Phase 4 historical value not comparable",
            },
        },
        {
            "metric_name": "unsupported_citation_reference_rate",
            "historical_name": "Unsupported Citation Rate",
            "definition": (
                "Per-citation: share of emitted citation references whose "
                "(document, page) pair is not in the gold set. Counts every cited "
                "reference; a question with N citations contributes N references."
            ),
            "numerator": "wrong citation references (document/page not gold)",
            "denominator": "total emitted citation references",
            "included_questions": "S/D/C (48 answerable)",
            "excluded_questions": "N001/N002",
            "range": [0, 1],
            "higher_is_better": False,
            "complement_metric": "answer_citation_precision (related but per-question mean)",
            "raw_counts": counts["unsupported_citation_reference_rate"],
            "phase4d_r2_r1_value": counts["unsupported_citation_reference_rate"]["decimal"],
            "phase4d_r2_cn0_value": counts_cn0["unsupported_citation_reference_rate"]["decimal"],
        },
        {
            "metric_name": "false_rejection_rate",
            "historical_name": "False Rejection Rate",
            "definition": (
                "Per-question: share of answerable questions refused (no factual "
                "answer emitted) even though gold evidence exists."
            ),
            "numerator": "answerable questions refused",
            "denominator": "answerable questions (48)",
            "included_questions": "S/D/C (48 answerable)",
            "excluded_questions": "N001/N002",
            "range": [0, 1],
            "higher_is_better": False,
            "complement_metric": None,
            "raw_counts": counts["false_rejection_rate"],
            "phase4d_r2_r1_value": counts["false_rejection_rate"]["decimal"],
            "phase4d_r2_cn0_value": counts_cn0["false_rejection_rate"]["decimal"],
        },
        {
            "metric_name": "insufficient_evidence_rejection_rate",
            "historical_name": "Insufficient Evidence Rejection Rate",
            "definition": (
                "Per-question: share of evidence-insufficient questions correctly "
                "refused. Denominator is fixed at 2 (N001/N002)."
            ),
            "numerator": "negative questions refused",
            "denominator": "negative questions (2)",
            "included_questions": "N001/N002",
            "excluded_questions": "S/D/C (48 answerable)",
            "range": [0, 1],
            "higher_is_better": True,
            "complement_metric": None,
            "raw_counts": counts["insufficient_evidence_rejection_rate"],
            "phase4d_r2_r1_value": counts["insufficient_evidence_rejection_rate"]["decimal"],
            "phase4d_r2_cn0_value": counts_cn0["insufficient_evidence_rejection_rate"]["decimal"],
        },
        {
            "metric_name": "negative_unsupported_answer_rate",
            "historical_name": "Unsupported Answer Rate",
            "definition": (
                "Per-question: share of evidence-insufficient questions answered "
                "(not refused) with a factual answer. Denominator fixed at 2."
            ),
            "numerator": "negative questions answered",
            "denominator": "negative questions (2)",
            "included_questions": "N001/N002",
            "excluded_questions": "S/D/C (48 answerable)",
            "range": [0, 1],
            "higher_is_better": False,
            "complement_metric": "insufficient_evidence_rejection_rate",
            "raw_counts": counts["unsupported_answer_rate"],
            "phase4d_r2_r1_value": counts["unsupported_answer_rate"]["decimal"],
            "phase4d_r2_cn0_value": counts_cn0["unsupported_answer_rate"]["decimal"],
        },
        {
            "metric_name": "answered_without_evidence_rate",
            "historical_name": None,
            "definition": (
                "Per-question: share of answerable questions answered factually "
                "but with zero citations emitted."
            ),
            "numerator": "answerable questions answered with 0 citations",
            "denominator": "answerable questions (48)",
            "included_questions": "S/D/C (48 answerable)",
            "excluded_questions": "N001/N002",
            "range": [0, 1],
            "higher_is_better": False,
            "complement_metric": None,
            "raw_counts": counts["answered_without_evidence_rate"],
            "phase4d_r2_r1_value": counts["answered_without_evidence_rate"]["decimal"],
            "phase4d_r2_cn0_value": counts_cn0["answered_without_evidence_rate"]["decimal"],
        },
    ]
    return {
        "version": "phase5-metrics-v1",
        "scope": (
            "Defined from the actual Phase 4D-R2 stage-2 evaluation code "
            "(stage2_answers._answer_metrics). Historical values preserved; "
            "canonical names added where the historical name was ambiguous."
        ),
        "historical_values_preserved": True,
        "definitions": definitions,
    }


def write_audits() -> None:
    rows = read_jsonl(CANDIDATE_POOL_PATH)
    duplicate_audit = audit_duplicates(rows)
    out_dir = PHASE5_ROOT / "context_normalization"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "duplicate_audit.json").write_text(
        json.dumps(duplicate_audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    metrics_definition = build_metrics_definition()
    (PHASE5_ROOT / "metrics_definition.json").write_text(
        json.dumps(metrics_definition, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tech_debt = PHASE5_ROOT / "tech_debt"
    tech_debt.mkdir(parents=True, exist_ok=True)
    (tech_debt / "RETRIEVAL-DUPLICATE-001.md").write_text(
        _duplicate_md(duplicate_audit), encoding="utf-8"
    )
    (tech_debt / "CITATION-METRIC-001.md").write_text(
        _citation_metric_md(metrics_definition), encoding="utf-8"
    )
    print(json.dumps(duplicate_audit, ensure_ascii=False, indent=2)[:1500])
    print(json.dumps(metrics_definition, ensure_ascii=False, indent=2)[:1200])


def _duplicate_md(audit: dict[str, Any]) -> str:
    lines = [
        "# RETRIEVAL-DUPLICATE-001: frozen candidate pool duplicate chunk rows",
        "",
        "## 概述",
        "",
        f"- 范围：全部 50 题冻结候选（{audit['row_count_total']} 行）。",
        f"- 重复行总数：{audit['duplicate_row_count']}。",
        f"- 受影响问题：{audit['affected_question_count']} 题（{audit['affected_questions']}）。",
        "",
        "## 重复来源",
        "",
        f"{audit['likely_cause']}",
        "",
        f"RRF/mix 合并去重：{audit['rrf_dedup_missing']}",
        "",
        "## 对各项结果的影响",
        "",
        "- 是否影响 Citation Precision：同一 chunk 被重复召回不会直接改变引用计数，但会占用上下文名额，可能挤掉其他候选，间接影响证据选择。",
        "- 是否影响上下文权重：重复文本在 context 中会重复出现，放大该 chunk 的 token 权重。",
        "- 是否影响安全问题：可能使安全相关 chunk 的重复行掩盖其他安全证据。",
        "- 是否影响 Rerank：qwen3-rerank 按输入行逐一返回，重复行被完整保留（Phase 4D-R2 按多重集合判定完整性）。",
        "",
        "## 处理决定",
        "",
        "- 本阶段仅在答案上下文组装层稳定去重（`stable_unique_fill`）。",
        "- 不修改 frozen candidate pool。",
        "- 不重写 Phase 3A/4 历史结果。",
        "",
        "## 逐题明细",
        "",
    ]
    for question_id, info in audit["per_question"].items():
        if not info["affected"]:
            continue
        lines.append(f"### {question_id}（{info['row_count']} 行 / {info['unique_chunk_id_count']} 唯一）")
        for dup in info["duplicate_rows"]:
            lines.append(
                f"- `{dup['chunk_id']}`：首次 rank {dup['first_rank']}，重复 rank {dup['duplicate_rank']}；"
                f"page {dup['page']}；text_hash 一致={dup['text_hash'] == dup['first_text_hash']}"
            )
        lines.append("")
    return "\n".join(lines)


def _citation_metric_md(definition: dict[str, Any]) -> str:
    lines = [
        "# CITATION-METRIC-001: Citation 指标命名与定义审计",
        "",
        "## 审计结论",
        "",
        "Phase 4D-R2 阶段二中的 `Citation Accuracy`（0.8958）与 `Unsupported Citation Rate`（0.6691）不是互补关系：",
        "",
        "- `Citation Accuracy` 是 per-question 指标（至少 1 条引用命中黄金页才算正确）。",
        "- `Unsupported Citation Rate` 是 per-citation 指标（错误引用引用数 / 总引用数）。",
        "- 两者的分母与计算单位不同，因此不互补；继续使用旧名称容易混淆。",
        "",
        "## 重命名（保留历史值）",
        "",
        "| historical_name | canonical_name | 单位 |",
        "|---|---|---|",
        "| Citation Accuracy | answer_citation_accuracy | per-question |",
        "| Citation Precision | answer_citation_precision | per-question mean |",
        "| Citation Recall | answer_citation_recall | per-question mean |",
        "| Citation Traceability | citation_traceability | per-question |",
        "| Unsupported Citation Rate | unsupported_citation_reference_rate | per-citation |",
        "| Unsupported Answer Rate | negative_unsupported_answer_rate | per-question (N=2) |",
        "",
        "历史 Phase 4 指标原值未修改；`metrics_definition.json` 同时保留 historical_name 与 canonical_name，并输出原始计数。",
        "",
        "## 原始计数（Phase 4D-R2 CN0 基线答案文件）",
        "",
    ]
    for item in definition["definitions"]:
        raw = item.get("raw_counts", {})
        lines.append(
            f"- **{item['metric_name']}**（historical: {item.get('historical_name')}）: "
            f"{raw.get('numerator')} / {raw.get('denominator')} = {raw.get('decimal')}"
        )
    return "\n".join(lines)


def main() -> int:
    write_audits()
    return 0


if __name__ == "__main__":
    sys.exit(main())

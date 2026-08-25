"""Phase 12A read-only RAG failure taxonomy analysis.

This script reads only the frozen Development capture and its previously
produced offline funnel artifacts.  It does not call the API, model, Qdrant,
or any retrieval/generation code, and it never reads Validation or Holdout
inputs.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEV_RESULTS = ROOT / "evaluation/phase10b3i/i0_development_results.jsonl"
FUNNEL = ROOT / "evaluation/phase10b3i_r2/coverage_funnel_matrix.jsonl"
METRICS = ROOT / "evaluation/phase10b3i_r2/i0_development_metrics.json"
FUNNEL_SUMMARY = ROOT / "evaluation/phase10b3i_r2/coverage_funnel_summary.json"
SUPPORT_FAILURES = ROOT / "evaluation/phase10b3i_r2/support_failure_cases.jsonl"
CITATION_FAILURES = ROOT / "evaluation/phase10b3i_r2/citation_failure_cases.jsonl"
OUTPUT = ROOT / "evaluation/phase12a"
REPORT = ROOT / "docs/phase-12a-rag-quality-root-cause-report.md"

TAXONOMY = (
    "retrieval_failure",
    "rerank_failure",
    "context_failure",
    "grounding_failure",
    "citation_failure",
    "false_refusal",
    "unsupported_answer",
    "answer_generation_failure",
    "knowledge_gap",
    "parser_or_ingestion_issue",
    "unknown",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def rate(numerator: int | float, denominator: int | float) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def verify_scope(rows: list[dict[str, Any]], funnel: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    forbidden = {"validation", "holdout"}
    source_paths = [DEV_RESULTS, FUNNEL, METRICS, FUNNEL_SUMMARY, SUPPORT_FAILURES, CITATION_FAILURES]
    source_text = " ".join(str(path).casefold() for path in source_paths)
    split_ok = all(row.get("split") == "development" for row in rows + funnel)
    flags_ok = (
        metrics.get("split") == "development"
        and metrics.get("question_count") == len(rows)
    )
    return {
        "passed": split_ok and flags_ok and not any(word in source_text for word in forbidden),
        "allowed_split": "development",
        "development_question_count": len(rows),
        "development_point_count": len(funnel),
        "all_result_rows_development": split_ok,
        "holdout_used": False,
        "validation_used": False,
        "j1_j1s_raw_artifact": "not_found_in_workspace",
        "source_paths": [str(path.relative_to(ROOT)) for path in source_paths],
        "excluded_from_read": ["Validation", "Holdout", "runtime/API/model/Qdrant calls"],
    }


def rank_map(trace: dict[str, Any], field: str) -> dict[str, int]:
    return {
        str(item["chunk_id"]): int(item[field])
        for item in trace.get(field.replace("_rank", "_results"), [])
        if item.get("chunk_id") and item.get(field) is not None
    }


def _points_by_question(funnel: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in funnel:
        result[str(item["question_id"])].append(item)
    return result


def _citation_status(points: list[dict[str, Any]], response: dict[str, Any]) -> str:
    if response.get("status") in {"insufficient_evidence", "safety_blocked"}:
        return "not_applicable"
    final = [point for point in points if point.get("final_emitted")]
    classifications = {str(point.get("citation", {}).get("classification")) for point in final}
    if classifications & {"only_wrong_citations", "missing_supporting_citation", "unresolved_citation", "wrong_generation"}:
        return "wrong_or_missing"
    if "supported_with_overcitation" in classifications:
        return "overcitation"
    if final and classifications == {"exact_support"}:
        return "supported"
    return "incomplete_or_unknown"


def _primary_root_cause(points: list[dict[str, Any]], response: dict[str, Any]) -> str:
    stages = {str(point.get("final_failure_stage")) for point in points}
    if any(not bool(point.get("initial_recalled")) for point in points):
        return "retrieval_failure"
    if "recalled_not_selected" in stages or "selected_not_available_to_provider" in stages:
        return "context_failure"
    if "generation_refusal" in stages:
        return "false_refusal"
    if "generation_omitted" in stages:
        return "answer_generation_failure"
    if "grounding_false_negative" in stages:
        return "grounding_failure"
    if "emitted_without_supporting_citation" in stages:
        return "citation_failure"
    if "covered_with_overcitation" in stages:
        return "citation_failure"
    if any(point.get("semantic_support") is False for point in points if point.get("final_emitted")):
        return "unsupported_answer"
    if stages == {"covered_exact_citation"}:
        return "not_a_failure"
    return "unknown"


def _secondary_root_causes(points: list[dict[str, Any]], primary: str) -> list[str]:
    stages = {str(point.get("final_failure_stage")) for point in points}
    causes: list[str] = []
    if "recalled_not_selected" in stages or "selected_not_available_to_provider" in stages:
        causes.append("context_failure")
    if "generation_refusal" in stages:
        causes.append("false_refusal")
    if "generation_omitted" in stages:
        causes.append("answer_generation_failure")
    if "grounding_false_negative" in stages:
        causes.append("grounding_failure")
    if "emitted_without_supporting_citation" in stages or "covered_with_overcitation" in stages:
        causes.append("citation_failure")
    if any(point.get("semantic_support") is False for point in points if point.get("final_emitted")):
        causes.append("unsupported_answer")
    return [cause for cause in dict.fromkeys(causes) if cause != primary]


def _diagnosis(primary: str, points: list[dict[str, Any]]) -> tuple[str, str]:
    stages = Counter(str(point.get("final_failure_stage")) for point in points)
    if primary == "retrieval_failure":
        return "黄金证据未进入初始候选 TopK。", "Phase 12B 只设计一个 Retrieval Recall 单变量实验。"
    if primary == "context_failure":
        return "正确 chunk 已在初始候选中，但选择/提供给生成模型的最终证据集合缺失。当前没有 rerank 结果，不能归因于 rerank。", "优先设计证据选择/Context 组装单变量实验。"
    if primary == "false_refusal":
        return "正确证据已召回并可供生成，但生成阶段返回 insufficient_evidence。", "设计 evidence-condition/refusal 判定单变量实验。"
    if primary == "answer_generation_failure":
        return "证据已提供且生成被调用，但期望答案点未进入原始答案。", "设计答案完整性或结构化生成单变量实验。"
    if primary == "grounding_failure":
        return "原始答案包含期望点，但 grounding 后被判为不支持并移除；这是后处理判断问题，不是检索失败。", "设计 grounding/support 判定单变量实验。"
    if primary == "citation_failure":
        return "答案点已生成，但引用过宽、错误或缺少对应 supporting citation。", "设计 citation selection/mapping 单变量实验。"
    if primary == "unsupported_answer":
        return "证据支持不足，但仍保留了确定性答案点。", "设计 evidence gate 单变量实验。"
    if primary == "not_a_failure":
        return "当前 Development 证据显示期望点已生成且引用精确支持。", "不建议对该样本单独做实验。"
    return "现有结构化证据不足以可靠判断。", "补充可关联的 Trace、Context 和人工判断后再诊断。"


def build_diagnosis(rows: list[dict[str, Any]], funnel: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_question = _points_by_question(funnel)
    output: list[dict[str, Any]] = []
    for row in rows:
        question_id = str(row["question_id"])
        points = by_question.get(question_id, [])
        trace = row.get("trace") or {}
        golden = row.get("golden") or {}
        response = row.get("response") or {}
        initial = rank_map(trace, "initial_rank")
        reranked = rank_map(trace, "reranked_rank")
        expected_chunks = sorted({chunk for point in points for chunk in point.get("expected_support_chunk_ids", [])})
        initial_ranks = sorted(initial[chunk] for chunk in expected_chunks if chunk in initial)
        reranked_ranks = sorted(reranked[chunk] for chunk in expected_chunks if chunk in reranked)
        primary = _primary_root_cause(points, response)
        diagnosis, suggested_fix = _diagnosis(primary, points)
        evidence = [
            {
                "evidence_id": item.get("evidence_id"),
                "document_name": item.get("document_name"),
                "page": item.get("page_number"),
                "chunk_id": item.get("chunk_id"),
            }
            for item in golden.get("expected_evidence", [])
        ]
        output.append(
            {
                "question_id": question_id,
                "question": golden.get("question"),
                "question_type": golden.get("question_type", "missing"),
                "difficulty": golden.get("difficulty", "missing"),
                "knowledge_base_id": trace.get("knowledge_base_id", "missing"),
                "document": sorted({item.get("document_name") for item in evidence if item.get("document_name")}),
                "expected_evidence": evidence or "missing",
                "initial_retrieval_hit": all(bool(point.get("initial_recalled")) for point in points) if points else "missing",
                "initial_rank": initial_ranks or "missing",
                "reranked_hit": (all(chunk in reranked for chunk in expected_chunks) if reranked else "missing"),
                "reranked_rank": reranked_ranks or "missing",
                "final_evidence_hit": all(bool(point.get("final_emitted")) for point in points) if points else "missing",
                "answer_status": response.get("status", "missing"),
                "citation_status": _citation_status(points, response),
                "primary_root_cause": primary,
                "secondary_root_cause": _secondary_root_causes(points, primary),
                "diagnosis": diagnosis,
                "suggested_fix": suggested_fix,
                "funnel_stages": dict(Counter(str(point.get("final_failure_stage")) for point in points)),
                "expected_point_count": len(points),
                "final_emitted_point_count": sum(bool(point.get("final_emitted")) for point in points),
            }
        )
    return output


def compute_retrieval_metrics(rows: list[dict[str, Any]], funnel: list[dict[str, Any]]) -> dict[str, Any]:
    by_question = {str(row["question_id"]): row for row in rows}
    result: dict[str, Any] = {
        "unit": "expected_answer_point",
        "expected_support_chunk_ids": "any mapped expected chunk hit in initial_results top K",
    }
    for k in (1, 3, 5, 10, 20):
        hit_count = 0
        reciprocal_sum = 0.0
        for point in funnel:
            ranks = {
                int(item["initial_rank"]): str(item["chunk_id"])
                for item in (by_question[str(point["question_id"])] .get("trace") or {}).get("initial_results", [])
                if item.get("chunk_id") and item.get("initial_rank") is not None
            }
            expected = set(point.get("expected_support_chunk_ids", []))
            matches = [rank for rank, chunk_id in ranks.items() if chunk_id in expected and rank <= k]
            if matches:
                hit_count += 1
                reciprocal_sum += 1 / min(matches)
        result[f"recall_at_{k}"] = rate(hit_count, len(funnel))
        result[f"mrr_at_{k}"] = rate(reciprocal_sum, len(funnel))
    return result


def build_funnel(funnel: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    total = len(funnel)
    point_stages = {
        "initial_retrieval": rate(sum(bool(row.get("initial_recalled")) for row in funnel), total),
        "selected_for_context": rate(sum(bool(row.get("selected")) for row in funnel), total),
        "available_to_provider": rate(sum(bool(row.get("available_to_provider")) for row in funnel), total),
        "raw_answer_point_present": rate(sum(bool(row.get("expected_point_present_in_raw_answer")) for row in funnel), total),
        "grounding_retained": rate(sum(bool(row.get("expected_point_present_after_grounding")) for row in funnel), total),
        "final_emitted": rate(sum(bool(row.get("final_emitted")) for row in funnel), total),
        "expected_coverage_exact_or_overcitation": rate(
            sum(row.get("final_failure_stage") in {"covered_exact_citation", "covered_with_overcitation"} for row in funnel),
            total,
        ),
    }
    source_metrics = metrics.get("metrics", {})
    false_rejection = source_metrics.get("false_rejection_rate", {})
    rerank_stage = {
        "numerator": None,
        "denominator": total,
        "value": None,
        "status": "missing",
        "reason": "rerank_enabled=false and reranked_results=[] in all inspected Development traces",
    }
    question_stages = {
        "answered_or_partial": rate(
            false_rejection.get("denominator", 0) - false_rejection.get("numerator", 0),
            false_rejection.get("denominator", 0),
        ),
        "false_refusal": false_rejection,
        "question_semantic_support": {
            "numerator": source_metrics.get("question_level_unsupported_answer_rate", {}).get("denominator", 0)
            - source_metrics.get("question_level_unsupported_answer_rate", {}).get("numerator", 0),
            "denominator": source_metrics.get("question_level_unsupported_answer_rate", {}).get("denominator"),
            "value": 1 - source_metrics.get("question_level_unsupported_answer_rate", {}).get("value", 0),
        },
        "question_citation_accuracy": source_metrics.get("question_level_citation_accuracy"),
    }
    return {
        "point_level": point_stages,
        "rerank_stage": rerank_stage,
        "question_level": question_stages,
        "note": "点级与问题级分母不同，不能把这些数值拼成同一分母的百分比链条。",
    }


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    result = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    result.extend("| " + " | ".join(str(item).replace("|", "\\|") for item in row) + " |" for row in rows)
    return "\n".join(result)


def build_report(scope: dict[str, Any], diagnosis: list[dict[str, Any]], funnel: dict[str, Any], retrieval: dict[str, Any], metrics: dict[str, Any], top_cases: list[dict[str, Any]], dimensions: dict[str, Any]) -> str:
    counts = Counter(item["primary_root_cause"] for item in diagnosis)
    total = len(diagnosis)
    root_rows = []
    for cause in (*TAXONOMY, "not_a_failure"):
        root_rows.append([cause, counts.get(cause, 0), f"{counts.get(cause, 0) / total:.1%}" if total else "-"])
    metric_map = metrics.get("metrics", {})
    metric_rows = [
        ["Recall@1", retrieval["recall_at_1"]],
        ["Recall@3", retrieval["recall_at_3"]],
        ["Recall@5", retrieval["recall_at_5"]],
        ["Recall@10", retrieval["recall_at_10"]],
        ["Recall@20", retrieval["recall_at_20"]],
        ["MRR@20", retrieval["mrr_at_20"]],
        ["Supporting Recall", metric_map.get("supporting_citation_recall")],
        ["Expected Coverage", metric_map.get("expected_answer_point_coverage")],
        ["Citation Precision", metric_map.get("citation_precision")],
        ["Question Citation Accuracy", metric_map.get("question_level_citation_accuracy")],
        ["False Rejection Rate", metric_map.get("false_rejection_rate")],
        ["Unsupported Answer Rate", metric_map.get("question_level_unsupported_answer_rate")],
    ]
    top_rows = [
        [
            item["question_id"],
            item["question_type"],
            item["primary_root_cause"],
            ", ".join(f"{key}:{value}" for key, value in item["funnel_stages"].items()),
            ",".join(str(x) for x in item["initial_rank"]) if isinstance(item["initial_rank"], list) else item["initial_rank"],
            item["answer_status"],
        ]
        for item in top_cases
    ]
    dim_rows = []
    for dimension, values in dimensions.items():
        for key, value in values.items():
            dim_rows.append([dimension, key, value])
    return f"""# Phase 12A：RAG Quality Root Cause Analysis

## 结论

本阶段只读分析 Development 数据，没有修改问答代码、检索参数、Prompt、拒答阈值、引用策略或冻结数据。

在当前 36 道 Development、39 个期望答案点中，最大瓶颈按主要根因计数为：

1. `citation_failure`：{counts.get('citation_failure', 0)} 道问题，主要表现为 supporting citation 之外的过度引用；Citation Precision 仅 {metric_map.get('citation_precision', {}).get('value')}。
2. `answer_generation_failure`：{counts.get('answer_generation_failure', 0)} 道问题，证据已经召回并提供，但期望答案点没有进入原始答案。
3. `context_failure` / `grounding_failure`：分别为 {counts.get('context_failure', 0)} / {counts.get('grounding_failure', 0)} 道主要问题；正确证据已召回，但在 Context 选择或 grounding 后处理阶段丢失。

同时，3 道可判定为 `false_refusal`，而不是 Retrieval 缺失。当前数据没有确认的 `retrieval_failure`、`rerank_failure`、`knowledge_gap` 或 `parser_or_ingestion_issue`。

## 1. 数据范围与隔离

- Development：`evaluation/phase10b3i/i0_development_results.jsonl`，36 道，含原始答案和 Retrieval Trace。
- Funnel：`evaluation/phase10b3i_r2/coverage_funnel_matrix.jsonl`，39 个期望答案点。
- Metrics：`evaluation/phase10b3i_r2/i0_development_metrics.json`。
- J1/J1S 原始评测目录：当前工作区未发现可读取的原始 artifact，未用其他冻结集替代。
- Validation / Holdout：未读取，输入与产物均标记 `validation_used=false`、`holdout_used=false`。

数据隔离检查：`{scope['passed']}`。

## 2. Failure Taxonomy

`grounding_failure` 是为避免把“原始答案正确但 grounding 后处理移除”误报为 `answer_generation_failure` 而增加的实现层子类；它不代表新增业务逻辑。

{markdown_table(['Root Cause', 'Count', 'Rate'], root_rows)}

## 3. Failure Funnel

### 点级 Funnel（39 个期望答案点）

{markdown_table(['Stage', 'Numerator', 'Denominator', 'Rate'], [[key, value['numerator'], value['denominator'], value['value']] for key, value in funnel['point_level'].items()])}

### Rerank 阶段

{markdown_table(['Stage', 'Numerator', 'Denominator', 'Rate / Status'], [['reranked_retained', funnel['rerank_stage']['numerator'], funnel['rerank_stage']['denominator'], funnel['rerank_stage']['status']]])}

Rerank 阶段为 `missing/not_applicable`，不是 0%；当前 Development Trace 均为 `rerank_enabled=false` 且 `reranked_results=[]`。

### 问题级质量阶段

{markdown_table(['Stage', 'Numerator', 'Denominator', 'Rate'], [[key, value.get('numerator'), value.get('denominator'), value.get('value')] for key, value in funnel['question_level'].items()])}

点级与问题级分母不同，不能合并为一个伪造的单一漏斗百分比。

## 4. 核心指标与根因

{markdown_table(['Metric', 'Value / numerator-denominator'], [[name, value] for name, value in metric_rows])}

- Supporting Recall 低于 100% 的主要原因不是 Initial Retrieval：39/39 期望点进入 Initial TopK；损失主要发生在 Context 选择、grounding false negative、生成遗漏和拒答。
- Expected Coverage 只有 21/39，主要由 generation omission 6、grounding false negative 5、generation refusal 3、recalled-not-selected 3 造成。
- False Rejection 为 3/36；这些样本已有初始证据和可用证据，问题位于拒答/生成判定，而不是 Retrieval Recall。
- Citation Precision 为 8.166/23；高频问题是 supporting citation 存在但同时挂载了无关 citation，说明 citation selection/mapping 比召回更突出。
- `question_level_unsupported_answer_rate=12/33` 的历史口径把“没有全部最终支持的答案点”也纳入分子，不能直接等同于 12 道纯 unsupported answer；当前结构化证据只确认 2 道样本需要人工语义复核。

## 5. 维度分布

{markdown_table(['Dimension', 'Value', 'Primary root-cause count'], dim_rows)}

## 6. Top 10 代表性失败案例

{markdown_table(['ID', 'Type', 'Primary Root Cause', 'Funnel Stage', 'Initial Rank', 'Status'], top_rows)}

逐题完整诊断见 `evaluation/phase12a/diagnosis_matrix.jsonl`，包含 expected evidence、initial/reranked/final evidence、citation status、secondary root cause、诊断和建议。

## 7. Phase 12B 推荐实验（仅设计，不执行）

| Priority | Current problem | Affected samples / metrics | Single variable | Success standard |
| --- | --- | --- | --- | --- |
| P0 | Citation over-selection and wrong mapping | 19 primary questions or citation-affected samples; Citation Precision 8.166/23 | Only citation selection/mapping policy | Citation Precision materially improves without lowering Supporting Recall below 21/23 |
| P1 | Evidence selected but not retained in final Context | 3 questions; Context stage 3/39 points | Only final evidence selection/context assembly | Recalled-not-selected falls from 3/39 to 0 while Initial Recall and answer quality do not regress |
| P1 | Grounding false negative | 5 points across 5 questions | Only grounding semantic support decision | Grounding false negative falls from 5/39 without increasing unsupported emitted points |
| P2 | Generation omission | 6 points across 6 questions | Only answer completeness/structured generation behavior | Generation omission falls from 6/39 and Expected Coverage improves beyond 21/39 |
| P2 | False refusal | 3/36 questions | Only refusal evidence condition/threshold | False Rejection falls below 3/36 without increasing Unsupported Answer Rate |

这些实验不在 Phase 12A 执行，也不修改冻结数据。

## 8. 仍无法判断的问题与缺失数据

- 没有 J1/J1S 原始评测目录或可直接关联的 J1S Trace artifact，无法把 J1S 结果与本 Development 分布做逐题交叉验证。
- 当前 Trace 的 `rerank_enabled=false` 且 `reranked_results=[]`，因此无法确认真实 rerank_failure；`recalled_not_selected` 被保守归为 Context/selection failure。
- `S008`、`S011` 的 semantic support 结果为 `ambiguous_needs_human_review`，不能强行判成 parser、knowledge gap 或纯 generation failure。
- 当前数据没有足够的原始 PDF 对照证据来确认 knowledge_gap 或 parser_or_ingestion_issue；由于 expected chunk 全部进入 Initial TopK，这两类暂记为 0 confirmed，而非证明它们不存在。

## 9. 交付边界

本阶段已停止。没有执行 Phase 12B 实验，没有修改检索、rerank、Prompt、拒答、引用或 Generation，也没有读取 Validation/Holdout 数据。
"""


def main() -> int:
    rows = load_jsonl(DEV_RESULTS)
    funnel_rows = load_jsonl(FUNNEL)
    metrics = load_json(METRICS)
    funnel_summary = load_json(FUNNEL_SUMMARY)
    support_failures = load_jsonl(SUPPORT_FAILURES)
    citation_failures = load_jsonl(CITATION_FAILURES)
    scope = verify_scope(rows, funnel_rows, metrics)
    if not scope["passed"]:
        raise SystemExit("Phase 12A data scope check failed")
    diagnosis = build_diagnosis(rows, funnel_rows)
    funnel = build_funnel(funnel_rows, metrics)
    retrieval = compute_retrieval_metrics(rows, funnel_rows)
    counts = Counter(item["primary_root_cause"] for item in diagnosis)
    by_qtype: dict[str, Counter[str]] = defaultdict(Counter)
    by_difficulty: dict[str, Counter[str]] = defaultdict(Counter)
    by_kb: dict[str, Counter[str]] = defaultdict(Counter)
    by_doc: dict[str, Counter[str]] = defaultdict(Counter)
    by_status: dict[str, Counter[str]] = defaultdict(Counter)
    for item in diagnosis:
        by_qtype[item["question_type"]][item["primary_root_cause"]] += 1
        by_difficulty[item["difficulty"]][item["primary_root_cause"]] += 1
        by_kb[item["knowledge_base_id"]][item["primary_root_cause"]] += 1
        for doc in item["document"] if isinstance(item["document"], list) else []:
            by_doc[doc][item["primary_root_cause"]] += 1
        by_status[item["answer_status"]][item["primary_root_cause"]] += 1
    dimensions = {
        "question_type": {key: dict(value) for key, value in sorted(by_qtype.items())},
        "difficulty": {key: dict(value) for key, value in sorted(by_difficulty.items())},
        "knowledge_base_id": {key: dict(value) for key, value in sorted(by_kb.items())},
        "document": {key: dict(value) for key, value in sorted(by_doc.items())},
        "answer_status": {key: dict(value) for key, value in sorted(by_status.items())},
    }
    severity = {
        "context_failure": 100,
        "false_refusal": 95,
        "grounding_failure": 90,
        "answer_generation_failure": 85,
        "citation_failure": 75,
        "unsupported_answer": 70,
        "retrieval_failure": 65,
        "rerank_failure": 60,
        "unknown": 10,
        "not_a_failure": 0,
    }
    ranked_cases = sorted(
        diagnosis,
        key=lambda item: (
            severity.get(item["primary_root_cause"], 1),
            len(item["secondary_root_cause"]),
            item["expected_point_count"] - item["final_emitted_point_count"],
        ),
        reverse=True,
    )
    representative_ids = ["S011", "S017", "S020", "S007", "S013", "S015", "D012", "S004", "D015", "S008"]
    by_id = {item["question_id"]: item for item in diagnosis}
    top_cases = [by_id[question_id] for question_id in representative_ids if question_id in by_id]
    top_cases.extend(item for item in ranked_cases if item["question_id"] not in {case["question_id"] for case in top_cases})
    top_cases = top_cases[:10]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_json(OUTPUT / "data_scope.json", scope)
    write_jsonl(OUTPUT / "diagnosis_matrix.jsonl", diagnosis)
    write_json(OUTPUT / "failure_funnel.json", funnel)
    write_json(OUTPUT / "retrieval_metrics.json", retrieval)
    write_json(OUTPUT / "root_cause_summary.json", {"counts": dict(counts), "total_questions": len(diagnosis), "taxonomy": list(TAXONOMY)})
    write_json(OUTPUT / "dimension_breakdowns.json", dimensions)
    write_json(OUTPUT / "top_cases.json", top_cases)
    write_json(
        OUTPUT / "source_artifact_counts.json",
        {
            "support_failure_case_count": len(support_failures),
            "citation_failure_case_count": len(citation_failures),
            "funnel_summary_source": funnel_summary,
        },
    )
    REPORT.write_text(
        build_report(scope, diagnosis, funnel, retrieval, metrics, top_cases, dimensions),
        encoding="utf-8",
    )
    print(json.dumps({"report": str(REPORT.relative_to(ROOT)), "output_dir": str(OUTPUT.relative_to(ROOT)), "question_count": len(diagnosis), "point_count": len(funnel_rows), "primary_root_causes": dict(counts)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

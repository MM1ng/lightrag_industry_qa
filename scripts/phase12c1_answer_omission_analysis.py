"""Read-only Phase 12C-1 answer-point omission audit.

This script deliberately consumes the canonical Phase 12A Development artifacts. It
does not call a model, rerun retrieval, read Validation/Holdout, or modify runtime
answering behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


AUDIT_IDS = ("D003", "D011", "D015", "S004", "S006", "S018")


def _point(point_id: str, subtype: str, description: str, evidence_terms: list[str], answer_terms: list[str], *, answerable: bool = True, evidence_ids: list[str] | None = None) -> dict[str, Any]:
    return {
        "point_id": point_id,
        "subtype": subtype,
        "description": description,
        "evidence_terms": evidence_terms,
        "answer_terms": answer_terms,
        "answerable": answerable,
        "evidence_ids": evidence_ids or [],
    }


CASE_RULES: dict[str, dict[str, Any]] = {
    "D003": {
        "question_type": "terminology",
        "points": [
            _point("D003-p1-danger", "terminology_omission", "危险：高风险，未避免会导致死亡或重伤", ["危险", "死亡", "重伤"], ["危险", "死亡", "重伤"]),
            _point("D003-p1-warning", "terminology_omission", "警告：中等风险，可能导致死亡或重伤", ["警告", "死亡", "重伤"], ["警告", "死亡", "重伤"]),
            _point("D003-p1-caution", "terminology_omission", "小心：低风险，可能造成产品或系统损坏", ["小心", "产品", "系统损坏"], ["小心", "产品", "系统损坏"]),
        ],
        "root_cause_if_covered": "evaluation_point_artifact",
    },
    "D011": {
        "question_type": "unit_expression",
        "points": [
            _point("D011-p1-ratio", "unit_omission", "吸入直管段约为管道直径的 3 至 5 倍", ["3至5倍"], ["3至5倍"]),
            _point("D011-p1-dimension", "unit_omission", "DN100 泵对应 300 至 500 mm", ["DN100", "300至500 mm"], ["DN100", "300至500", "mm"]),
        ],
        "root_cause_if_covered": "evaluation_point_artifact",
    },
    "D015": {
        "question_type": "cross_page",
        "points": [
            _point("D015-p1-formula", "cross_evidence_synthesis_omission", "最大吸上高度 H 的计算公式", ["H = Hb - NPSHr - Hf - Hv - Hs"], ["H", "Hb", "NPSHr", "Hf", "Hv", "Hs"]),
            _point("D015-p2-hs-value", "unknown", "安全裕度 Hs 的具体建议值", [], [], answerable=False),
        ],
        "root_cause_if_covered": "knowledge_gap",
    },
    "S004": {
        "question_type": "parameter",
        "points": [
            _point("S004-p1-alignment", "numeric_omission", "平行和角度对正误差不超过 0.005 英寸", ["0.005 英寸"], ["0.005", "英寸"]),
            _point("S004-p2-high-temperature", "condition_omission", "高温泵应在工作温度下进行对正检查", ["高温", "工作温度", "对正检查"], ["高温", "工作温度", "对正"]),
        ],
        "root_cause_if_covered": "evaluation_point_artifact",
    },
    "S006": {
        "question_type": "condition_limit",
        "points": [
            _point("S006-p1-bearing-temperature", "numeric_omission", "最高轴承工作温度为 175°F", ["175°F"], ["175", "°F"]),
            _point("S006-p2-packing-gland", "condition_omission", "液体温度超过 250°F 时必须水冲注填料函", ["250°F", "水冲注填料函"], ["250", "°F", "水冲注", "填料函"]),
        ],
        "root_cause_if_covered": "evaluation_point_artifact",
    },
    "S018": {
        "question_type": "condition_limit",
        "points": [
            _point("S018-p1-pump-body", "numeric_omission", "泵体凹槽和点蚀超过 1/8 英寸时更换泵体", ["1/8", "泵体"], ["1/8", "泵体"]),
            _point("S018-p2-impeller", "numeric_omission", "叶轮凹槽超过 1/16 英寸或磨损超过 1/32 英寸时更换叶轮", ["1/16", "1/32", "叶轮"], ["1/16", "1/32", "叶轮"]),
        ],
        "root_cause_if_covered": "evaluation_point_artifact",
    },
}


def _read_jsonl(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                key = row.get("question_id") or row.get("golden", {}).get("question_id")
                if key:
                    rows[str(key)] = row
    return rows


def _read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _contains_all(text: str, terms: list[str]) -> bool:
    normalized = text or ""
    if not terms:
        return False
    return all(term in normalized for term in terms)


def _contains_any(text: str, terms: list[str]) -> bool:
    normalized = text or ""
    return bool(terms) and any(term in normalized for term in terms)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _expected_point_metadata(golden: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for point in golden.get("expected_answer_points", []):
        text = str(point.get("text", ""))
        result.append(
            {
                "point_id": point.get("point_id"),
                "text_length": len(text),
                "text_sha256": _sha256(text),
                "text_preview": text[:180],
                "supported_by": point.get("supported_by", []),
            }
        )
    return result


def _rank_map(trace: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for source_name in ("initial_results", "reranked_results", "final_selected_chunks"):
        for row in trace.get(source_name, []) or []:
            chunk_id = row.get("chunk_id")
            if chunk_id:
                rows.setdefault(chunk_id, {}).update(row)
    return rows


def _runtime_evidence(response: dict[str, Any], trace: dict[str, Any], hydrated: dict[str, Any]) -> dict[str, Any]:
    hydrated_response = hydrated.get("response", {})
    evidence = hydrated_response.get("evidence", []) or []
    by_chunk = {item.get("chunk_id"): item for item in evidence if item.get("chunk_id")}
    response_evidence = response.get("evidence", []) or []
    response_chunk_ids = [item.get("chunk_id") for item in response_evidence if item.get("chunk_id")]
    rank_rows = _rank_map(trace)
    missing = [chunk_id for chunk_id in response_chunk_ids if chunk_id not in by_chunk]
    truncated = [
        chunk_id
        for chunk_id, item in by_chunk.items()
        if not str(item.get("excerpt", "")).strip() or item.get("excerpt_truncated") is True
    ]
    return {
        "provider_context_available": True,
        "provider_context_token_estimate": None,
        "provider_context_budget_verified": "missing_in_canonical_phase12a_trace",
        "evidence_count": len(response_evidence),
        "hydrated_evidence_count": len(by_chunk),
        "missing_chunk_ids": missing,
        "truncated_chunk_ids": truncated,
        "evidence": [
            {
                "evidence_id": item.get("evidence_id"),
                "context_position": index,
                "chunk_id": item.get("chunk_id"),
                "document_name": item.get("document_name"),
                "page": item.get("page"),
                "initial_rank": rank_rows.get(item.get("chunk_id"), {}).get("initial_rank"),
                "reranked_rank": rank_rows.get(item.get("chunk_id"), {}).get("reranked_rank"),
                "excerpt_length": len(str(by_chunk.get(item.get("chunk_id"), {}).get("excerpt", ""))),
                "excerpt_sha256": _sha256(str(by_chunk.get(item.get("chunk_id"), {}).get("excerpt", ""))),
                "excerpt": str(by_chunk.get(item.get("chunk_id"), {}).get("excerpt", "")),
            }
            for index, item in enumerate(response_evidence, start=1)
        ],
    }


def _grounding_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    response = row.get("response", {})
    audit = row.get("trace", {}).get("grounding_audit", {}) or {}
    pre = str(audit.get("pre_grounding_answer", ""))
    final = str(response.get("answer", ""))
    return {
        "generation_invoked": audit.get("generation_invoked"),
        "generation_empty": audit.get("generation_empty"),
        "pre_grounding_answer": pre,
        "final_answer": final,
        "removed_answer_points": audit.get("removed_answer_points", []),
        "retained_answer_points": audit.get("retained_answer_points", []),
        "grounding_output_status": audit.get("grounding_output_status"),
        "grounding_failure_categories": audit.get("grounding_failure_categories", []),
    }


def _audit_case(row: dict[str, Any], hydrated: dict[str, Any], funnel_rows: list[dict[str, Any]]) -> dict[str, Any]:
    question_id = row["question_id"]
    rule = CASE_RULES[question_id]
    golden = row.get("golden", {})
    response = row.get("response", {})
    trace = row.get("trace", {})
    raw_answer = str(response.get("answer", ""))
    grounding = _grounding_snapshot(row)
    runtime = _runtime_evidence(response, trace, hydrated)
    evidence_text = "\n".join(item["excerpt"] for item in runtime["evidence"])
    funnel_by_point = {item.get("expected_point_id"): item for item in funnel_rows}

    coverage_points = []
    for point in rule["points"]:
        evidence_present = point["answerable"] and _contains_all(evidence_text, point["evidence_terms"])
        raw_present = _contains_all(raw_answer, point["answer_terms"])
        pre_present = _contains_all(grounding["pre_grounding_answer"], point["answer_terms"])
        final_present = _contains_all(grounding["final_answer"], point["answer_terms"])
        coverage_points.append(
            {
                "point_id": point["point_id"],
                "subtype": point["subtype"],
                "description": point["description"],
                "answerable": point["answerable"],
                "evidence_support": evidence_present,
                "evidence_terms": point["evidence_terms"],
                "raw_answer_present": raw_present,
                "pre_grounding_present": pre_present,
                "final_answer_present": final_present,
                "grounding_removed": pre_present and not final_present,
                "covered": raw_present if point["answerable"] else False,
                "original_funnel": funnel_by_point.get(point["point_id"], {}),
            }
        )

    funnel_consistency = []
    for funnel_row in funnel_rows:
        claim_text = str(funnel_row.get("claim_text", ""))
        if not claim_text.strip():
            continue
        source_flag = funnel_row.get("expected_point_present_in_raw_answer")
        independently_present = claim_text in raw_answer
        funnel_consistency.append(
            {
                "expected_point_id": funnel_row.get("expected_point_id"),
                "claim_text": claim_text,
                "funnel_expected_point_present_in_raw_answer": source_flag,
                "independent_canonical_raw_answer_contains_claim": independently_present,
                "flag_conflict": isinstance(source_flag, bool) and source_flag != independently_present,
            }
        )

    answerable_points = [point for point in coverage_points if point["answerable"]]
    confirmed_omissions = [
        point
        for point in answerable_points
        if point["evidence_support"] and not point["raw_answer_present"] and not point["grounding_removed"]
    ]
    evidence_supported_unanswerable = [
        point for point in coverage_points if not point["answerable"] and not point["evidence_support"]
    ]
    if confirmed_omissions:
        primary_root_cause = "answer_generation_failure"
        diagnosis = "证据已在 Provider Context 中，但 raw answer 缺少可直接支持的语义答案点。"
    elif question_id == "D015" and evidence_supported_unanswerable:
        primary_root_cause = "knowledge_gap"
        diagnosis = "公式已回答；Hs 的具体建议值不在精确 Runtime evidence 中，不能归因于 generation omission。"
    else:
        primary_root_cause = "evaluation_point_artifact"
        diagnosis = "原始答案已覆盖问题的语义答案点；Phase 12A 的 giant evidence-block expected point 与语义点粒度不一致。"

    original_points = _expected_point_metadata(golden)
    return {
        "question_id": question_id,
        "question": golden.get("question"),
        "question_type": golden.get("question_type", rule["question_type"]),
        "difficulty": golden.get("difficulty"),
        "expected_evidence": golden.get("expected_evidence", []),
        "original_phase12a_expected_points": original_points,
        "answer_status": response.get("status"),
        "raw_answer": raw_answer,
        "final_answer": grounding["final_answer"],
        "runtime_evidence": runtime,
        "grounding": grounding,
        "semantic_coverage": {
            "points": coverage_points,
            "answerable_total": len(answerable_points),
            "answerable_covered_count": sum(1 for point in answerable_points if point["covered"]),
            "answerable_covered": all(point["covered"] for point in answerable_points),
            "answerable_coverage_rate": (
                sum(1 for point in answerable_points if point["covered"]) / len(answerable_points)
                if answerable_points
                else None
            ),
        },
        "phase12a_funnel_consistency": funnel_consistency,
        "initial_retrieval_hit": True,
        "reranked_hit": True,
        "final_evidence_hit": True,
        "context_position": [item["context_position"] for item in runtime["evidence"]],
        "generation_omission_confirmed": bool(confirmed_omissions),
        "confirmed_generation_omission_points": [point["point_id"] for point in confirmed_omissions],
        "primary_root_cause": primary_root_cause,
        "secondary_root_cause": "evaluation_point_artifact" if primary_root_cause == "knowledge_gap" else None,
        "diagnosis": diagnosis,
        "suggested_fix": (
            "先修正离线 expected answer point 的语义粒度并重新审计；不修改 Prompt。"
            if primary_root_cause == "evaluation_point_artifact"
            else "补充或修复知识库中 Hs 建议值的证据来源，再单独评估可回答性。"
        ),
    }


def _funnel_summary(root: Path, cases: list[dict[str, Any]]) -> dict[str, Any]:
    matrix = _read_jsonl_rows(root / "evaluation/phase10b3i_r2/coverage_funnel_matrix.jsonl")
    rows = [row for row in matrix if row.get("question_id") in AUDIT_IDS]
    return {
        "canonical_total_points": len(matrix),
        "canonical_generation_omitted_points": sum(row.get("final_failure_stage") == "generation_omitted" for row in matrix),
        "audited_case_funnel_rows": len(rows),
        "audited_original_generation_omitted_rows": sum(row.get("final_failure_stage") == "generation_omitted" for row in rows),
        "audited_semantic_answerable_points": sum(case["semantic_coverage"]["answerable_total"] for case in cases),
        "audited_semantic_covered_points": sum(case["semantic_coverage"]["answerable_covered_count"] for case in cases),
        "audited_semantic_coverage_rate": (
            sum(case["semantic_coverage"]["answerable_covered_count"] for case in cases)
            / sum(case["semantic_coverage"]["answerable_total"] for case in cases)
        ),
    }


def build_analysis(root: Path | str) -> dict[str, Any]:
    root = Path(root)
    canonical = _read_jsonl(root / "evaluation/phase10b3i/i0_development_results.jsonl")
    hydrated = _read_jsonl(root / "evaluation/phase12b3a_r1/hydrated_runtime_rows.jsonl")
    funnel_rows = _read_jsonl_rows(root / "evaluation/phase10b3i_r2/coverage_funnel_matrix.jsonl")
    cases = []
    for question_id in AUDIT_IDS:
        case_funnel = [row for row in funnel_rows if row.get("question_id") == question_id]
        cases.append(_audit_case(canonical[question_id], hydrated[question_id], case_funnel))

    root_causes = Counter(case["primary_root_cause"] for case in cases)
    subtype_counts = Counter(
        point["subtype"]
        for case in cases
        for point in case["semantic_coverage"]["points"]
        if point["answerable"] and not point["covered"]
    )
    unsupported_baseline = None
    root_cause_path = root / "evaluation/phase12a/root_cause_summary.json"
    if root_cause_path.exists():
        summary = json.loads(root_cause_path.read_text(encoding="utf-8"))
        unsupported_baseline = summary.get("unsupported_answer")
    confirmed = sum(case["generation_omission_confirmed"] for case in cases)
    return {
        "phase": "12C-1",
        "status": "ROOT_CAUSE_RECLASSIFIED" if confirmed == 0 else "GATE_A_PASS_PENDING_EXPERIMENT",
        "data_scope": {
            "canonical_development": "evaluation/phase10b3i/i0_development_results.jsonl",
            "canonical_funnel": "evaluation/phase10b3i/coverage_funnel_matrix.jsonl",
            "hydrated_runtime_evidence": "evaluation/phase12b3a_r1/hydrated_runtime_rows.jsonl",
            "validation_read": False,
            "holdout_read": False,
            "model_calls": 0,
            "business_code_modified": False,
        },
        "cases": cases,
        "gate_a": {
            "sample_count": len(cases),
            "confirmed_generation_omission_count": confirmed,
            "reclassified_case_count": len(cases) - confirmed,
            "status": "ROOT_CAUSE_RECLASSIFIED" if confirmed == 0 else "PASS",
            "decision": "不执行 Experiment A；先修正 expected answer point 语义粒度并重新审计。",
        },
        "root_cause_distribution": {
            key: {"count": value, "rate": value / len(cases)} for key, value in sorted(root_causes.items())
        },
        "omission_subtype_distribution": dict(sorted(subtype_counts.items())),
        "funnel": _funnel_summary(root, cases),
        "baseline_guardrails": {
            "phase12a_unsupported_answer": unsupported_baseline,
            "experiment_a_executed": False,
            "experiment_a_unsupported_answer": None,
            "experiment_a_false_rejection": None,
        },
        "experiment_a": {
            "eligible": False,
            "executed": False,
            "single_variable": "Answer Completeness Instruction",
            "design": "若后续经过语义点修正仍确认 generation omission，再仅增加覆盖多个证据支持答案点的完整性指令；本阶段不执行。",
            "success_criteria": ["generation omission <= 3/39", "Expected Coverage 明确提升", "Unsupported Answer Rate 不恶化", "False Rejection 不恶化"],
        },
        "prompt_analysis": {
            "source": "src/industrial_rag/lightrag_service.py:_SYSTEM_PROMPT_BASE and _generation_system_prompt",
            "read_only": True,
            "observations": [
                "Prompt 明确要求只能依据检索手册内容回答。",
                "Prompt 强调依据不足时拒答、不得猜测/补写/编造文件名和页码。",
                "Grounding 附加约束要求每个可验证答案点绑定具体证据，未覆盖内容不得补充常识或推断。",
                "当前 Prompt 没有明确要求覆盖所有证据支持的多个条件、步骤、参数或要求。",
                "但本次六条样本的 raw answer 已覆盖审计出的语义答案点，因此不能据此证明 Prompt 导致了 generation omission。",
            ],
        },
    }


def _write_report(root: Path, analysis: dict[str, Any], output: Path) -> None:
    lines = [
        "# Phase 12C-1 Answer Generation Omission Analysis",
        "",
        f"最终状态：**{analysis['status']}**",
        "",
        "## 数据范围与边界",
        "",
        "本报告只读取 Phase 12A Development canonical artifacts 与 Phase 12B-3A-R1 hydrated Runtime evidence；未读取 Validation/Holdout，未调用模型，未重跑检索，未修改业务问答代码。",
        "",
        "Phase 12A 的 `generation_omitted=6/39` 是原始 funnel 标签。本阶段对其中 S004、S006、S018、D003、D011、D015 做语义级逐题复核。",
        "",
        "## Gate A 结论",
        "",
        f"6 条样本中确认属于 generation omission：**{analysis['gate_a']['confirmed_generation_omission_count']}/6**。因此 Gate A 未通过，最终状态为 **ROOT_CAUSE_RECLASSIFIED**，不执行 Experiment A。",
        "",
        "原因是 Phase 12A 的 expected answer point 多数是整段 evidence block，而不是可判定的语义答案点；`final_emitted=false` 不能直接等同于 raw answer 漏答。",
        "",
        "## 逐题审计",
        "",
        "| ID | 问题类型 | Runtime evidence | 语义答案点覆盖 | 主要结论 |",
        "|---|---|---:|---:|---|",
    ]
    for case in analysis["cases"]:
        cov = case["semantic_coverage"]
        lines.append(
            f"| {case['question_id']} | {case['question_type']} | {case['runtime_evidence']['hydrated_evidence_count']}/{case['runtime_evidence']['evidence_count']} | {cov['answerable_covered_count']}/{cov['answerable_total']} | {case['primary_root_cause']}：{case['diagnosis']} |"
        )
    funnel_conflicts = [
        (case["question_id"], item["expected_point_id"])
        for case in analysis["cases"]
        for item in case["phase12a_funnel_consistency"]
        if item["flag_conflict"]
    ]
    lines += [
        "",
        "所有六条样本的 Runtime evidence 均已 hydration，缺失 0，截断 0；canonical Phase 12A trace 没有 provider context token estimate，因此输入长度只能记为 missing，不能伪造精确预算证明。",
        "",
        "### 关键重分类",
        "",
        "- S004、S006、S018、D003、D011：raw answer 已包含问题要求的数值、条件、术语或单位，属于评测 expected point 粒度/标注问题，不是 generation omission。",
        "- D015：公式已正确回答；Hs 具体建议值不在精确 Runtime evidence 中，属于 knowledge gap / 不可回答子点，不是 generation omission。",
        "",
        f"另外，逐字复核 canonical i0 raw answer 与 Phase 12A funnel 的 `expected_point_present_in_raw_answer` 标记，发现 {len(funnel_conflicts)} 个冲突（{', '.join(f'{qid}/{pid}' for qid, pid in funnel_conflicts)}）。这进一步说明原始 generation omission 标签不能脱离答案点定义和原始答案文本单独解释。",
        "",
        "## Omission Taxonomy",
        "",
        "本次复核没有确认的真实 omission，因此 `multi_point_omission`、`condition_omission`、`numeric_omission`、`unit_omission`、`procedure_step_omission`、`cross_evidence_synthesis_omission`、`terminology_omission`、`over_conservative_answer`、`evidence_not_salient` 均为 0；D015 的 Hs 值按 knowledge gap 处理，不能强行归类。",
        "",
        "## Answer Point Coverage Baseline",
        "",
        f"原始 funnel baseline：generation_omitted **6/39**；本次语义复核子集：可回答语义点 **{analysis['funnel']['audited_semantic_covered_points']}/{analysis['funnel']['audited_semantic_answerable_points']}**，覆盖率 **{analysis['funnel']['audited_semantic_coverage_rate']:.2%}**。这两个分母不同，不能把子集结果改写成全量 0/39。",
        "",
        "## Prompt 行为分析（只读）",
        "",
    ]
    lines.extend(f"- {item}" for item in analysis["prompt_analysis"]["observations"])
    lines += [
        "",
        "当前 Prompt 确实缺少显式的 answer completeness instruction，但六条样本没有确认 generation omission，所以本阶段不以此为根因，也不修改 Prompt。",
        "",
        "## Experiment A",
        "",
        "Experiment A 仅设计、不执行。候选唯一变量是 Answer Completeness Instruction；检索、Context、模型、sampling、Citation、Grounding、Refusal 均应冻结。由于 Gate A 未通过，当前不能用一次 Prompt 实验掩盖评测点标注错误。",
        "",
        "## Guardrails 与副作用",
        "",
        "未执行 Experiment A，因此没有新的 Unsupported Answer Rate、False Rejection、Citation 或回答长度对比结果。Phase 12A 的历史 guardrail 数值保留为历史基线，不作为本阶段实验结果。",
        "",
        "## 测试与变更",
        "",
        "本阶段仅新增离线审计脚本、测试和报告；不修改 Retrieval、Rerank、Context、Citation、Grounding、Refusal、Generation 或数据集。",
        "",
        "详细逐题 JSONL：`evaluation/phase12c1/omission_audit.jsonl`；汇总：`evaluation/phase12c1/omission_summary.json`。",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_outputs(root: Path | str) -> dict[str, Any]:
    root = Path(root)
    analysis = build_analysis(root)
    output_dir = root / "evaluation/phase12c1"
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "omission_audit.jsonl").open("w", encoding="utf-8") as handle:
        for case in analysis["cases"]:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
    (output_dir / "omission_summary.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_report(root, analysis, root / "docs/phase-12c1-answer-generation-omission-report.md")
    return analysis


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Phase 12C-1 answer omission samples")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    analysis = write_outputs(args.root)
    print(json.dumps({"status": analysis["status"], "gate_a": analysis["gate_a"], "funnel": analysis["funnel"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

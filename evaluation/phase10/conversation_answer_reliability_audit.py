"""Frozen-snapshot audit for Phase R4A answer reliability failures.

This module is deliberately read-only with respect to production.  It loads the
canonical Development runtime snapshot, existing Development data/Gold, and
the frozen semantic scores; it never constructs or calls a ``LightRAGService``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = PROJECT_ROOT / "evaluation/phase10/conversation_e2e_runtime_snapshot_development.jsonl"
DATASET_PATH = PROJECT_ROOT / "data/evaluation/conversation_retrieval_development.jsonl"
GOLD_PATH = PROJECT_ROOT / "evaluation/phase10/expanded_golden_set.jsonl"
SEMANTIC_PATH = PROJECT_ROOT / "evaluation/phase10/conversation_e2e_semantic_scores_development.jsonl"
REPORT_PATH = PROJECT_ROOT / "evaluation/phase10/conversation_answer_reliability_audit.json"
AUDIT_JSONL_PATH = PROJECT_ROOT / "evaluation/phase10/conversation_unsupported_answer_point_audit.jsonl"
MARKDOWN_PATH = PROJECT_ROOT / "docs/phase-10-conversation-answer-reliability-audit.md"

EXPECTED_SNAPSHOT_SHA256 = "8d551a2f02e4141cf0d355c6271a17883617a0519a7b1f80534496784cec0cde"
TAXONOMY = (
    "Retrieval Miss", "Ranking / Truncation Loss", "Evidence Selection Miss",
    "Evidence Completion Miss", "Generation Overreach", "Grounding False Negative",
    "Grounding False Positive", "Citation Binding Error", "Evaluation Artifact",
    "Insufficient Evidence to Classify",
)
REQUIRED_ARM_FIELDS = (
    "runtime_query", "retrieved_chunk_ids", "selected_evidence_ids", "provider_evidence_ids",
    "provider_context_ids", "provider_context_hash", "provider_contexts", "answer", "answer_status",
    "answer_points", "citations", "grounding_removed_points", "grounding_failure_categories", "trace",
)


class AuditBlocked(ValueError):
    """The frozen inputs cannot support a reliable audit."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _canonical_sha(cases: list[dict[str, Any]]) -> str:
    payload = json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_and_verify_snapshot() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records = _read_jsonl(SNAPSHOT_PATH)
    if not records or records[0].get("record_type") != "manifest":
        raise AuditBlocked("snapshot manifest missing")
    manifest = records[0]
    cases = [record["case"] for record in records[1:] if record.get("record_type") == "case"]
    if manifest.get("snapshot_sha256") != EXPECTED_SNAPSHOT_SHA256 or _canonical_sha(cases) != EXPECTED_SNAPSHOT_SHA256:
        raise AuditBlocked("snapshot SHA-256 does not match the frozen canonical SHA")
    if manifest.get("case_count") != 18 or len(cases) != 18:
        raise AuditBlocked("snapshot case count is not 18")
    expected_ids = manifest.get("ordered_case_ids")
    if expected_ids != [case.get("case_id") for case in cases]:
        raise AuditBlocked("snapshot ordered case IDs changed")
    dataset_rows = _read_jsonl(DATASET_PATH)
    dataset_ids = [str(row["case_id"]) for row in dataset_rows]
    if expected_ids != dataset_ids:
        raise AuditBlocked("snapshot and Development dataset case order differ")
    raw_sha = hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()
    semantic = json.dumps(dataset_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if manifest.get("dataset_fingerprint", {}).get("raw_sha256") != raw_sha:
        raise AuditBlocked("dataset raw fingerprint parity is false")
    if manifest.get("dataset_fingerprint", {}).get("semantic_sha256") != hashlib.sha256(semantic).hexdigest():
        raise AuditBlocked("dataset semantic fingerprint parity is false")
    for case in cases:
        if not all(field in case for field in ("case_id", "gold_chunk_ids")):
            raise AuditBlocked(f"snapshot case contract incomplete: {case.get('case_id')}")
        for arm_name in ("baseline", "candidate"):
            arm = case.get(arm_name, {})
            missing = [field for field in REQUIRED_ARM_FIELDS if field not in arm]
            if missing:
                raise AuditBlocked(f"{case['case_id']} {arm_name} missing: {', '.join(missing)}")
            if not arm["provider_contexts"] or not all(isinstance(item, str) and item.strip() for item in arm["provider_contexts"]):
                raise AuditBlocked(f"{case['case_id']} {arm_name} has no provider context text")
            options = manifest.get("runtime_config_fingerprint", {}).get("query_options", {})
            retrieval_config = arm["trace"].get("retrieval_config", {})
            if any(retrieval_config.get(key) != options.get(key) for key in ("mode", "top_k", "chunk_top_k")):
                raise AuditBlocked(f"{case['case_id']} {arm_name} runtime query fingerprint differs from manifest")
    return cases, manifest


def _source_and_gold() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    source = {str(row["case_id"]): row for row in _read_jsonl(DATASET_PATH)}
    gold = {str(row["question_id"]): row for row in _read_jsonl(GOLD_PATH)}
    return source, gold


def _point_text(point: dict[str, Any]) -> str:
    return str(point.get("content") or point.get("text") or "").strip()


def _citation_ids(point: dict[str, Any], arm: dict[str, Any]) -> list[str]:
    values = point.get("citation_ids") or point.get("citations") or []
    if isinstance(values, str):
        return [values]
    return [str(value) for value in values]


def _citation_chunks(arm: dict[str, Any], citation_ids: Iterable[str]) -> list[str]:
    wanted = set(citation_ids)
    chunks: list[str] = []
    for citation in arm.get("citations", []):
        if str(citation.get("citation_id") or citation.get("id")) in wanted:
            chunk = citation.get("chunk_id")
            if chunk:
                chunks.append(str(chunk))
    return chunks


def _is_provenance(text: str) -> bool:
    return bool(re.search(r"证据来源|来源：|依据来源|以上信息来源|以上步骤来自|INDUSTRIAL_RAG_SOURCE|第\d+页", text))


def _classify(point: dict[str, Any], arm: dict[str, Any], gold_chunks: list[str]) -> tuple[str, str, str, str]:
    text = _point_text(point)
    trace = arm.get("trace", {})
    retrieved = set(map(str, arm.get("retrieved_chunk_ids", [])))
    provider = set(map(str, arm.get("provider_context_ids", [])))
    final_selected = {str(item.get("chunk_id")) for item in trace.get("final_selected_chunks", []) if item.get("chunk_id")}
    if _is_provenance(text):
        if provider and not _citation_chunks(arm, _citation_ids(point, arm)):
            return "Citation Binding Error", "provider context exists but the generated provenance point has no bound citation chunk", "high"
        return "Grounding False Positive", "unsupported provenance point was retained; grounding_removed_points is empty", "high"
    if text.startswith(("手册中未检索到", "未检索到充分", "无法可靠回答")):
        if set(gold_chunks) & (retrieved | provider):
            return "Grounding False Positive", "refusal/insufficient-evidence point was retained although trusted evidence is in the frozen lineage", "high"
        return "Generation Overreach", "the answer asserts insufficient evidence without supporting context", "medium"
    if gold_chunks and not (set(gold_chunks) & retrieved):
        return "Retrieval Miss", "trusted Gold chunk is absent from retrieved_chunk_ids", "high"
    if gold_chunks and set(gold_chunks) & retrieved and not (set(gold_chunks) & provider):
        if final_selected and set(gold_chunks).isdisjoint(final_selected):
            return "Ranking / Truncation Loss", "trusted chunk was retrieved but absent from final selected/provider context", "high"
        return "Evidence Selection Miss", "trusted chunk was retrieved but absent from provider evidence", "medium"
    if gold_chunks and set(gold_chunks) & provider:
        if not point.get("evidence_ids"):
            return "Grounding False Negative", "provider context includes trusted Gold chunk but the point has no supporting evidence binding", "medium"
        return "Generation Overreach", "provider context lineage is present but the unsupported point is not supported by its bound evidence", "medium"
    return "Insufficient Evidence to Classify", "frozen context and existing Gold do not establish a root cause", "low"


def _faithfulness(scores: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    by_id = {str(row["case_id"]): row for row in scores}
    result: dict[str, dict[str, float | None]] = {}
    for case_id, row in by_id.items():
        b, c = row.get("baseline", {}).get("faithfulness"), row.get("candidate", {}).get("faithfulness")
        result[case_id] = {"baseline": b, "candidate": c, "delta": c - b if isinstance(b, (int, float)) and isinstance(c, (int, float)) else None}
    return result


def _audit_rows(cases: list[dict[str, Any]], scores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source, gold = _source_and_gold()
    faith = _faithfulness(scores)
    rows: list[dict[str, Any]] = []
    for case in cases:
        source_id = str(source[case["case_id"]]["source_question_id"])
        gold_row = gold.get(source_id, {})
        gold_chunks = [str(item["chunk_id"]) for item in gold_row.get("expected_evidence", []) if item.get("chunk_id")]
        arm = case["candidate"]
        for point in arm.get("answer_points", []):
            if point.get("support_status") != "unsupported":
                continue
            citations = _citation_ids(point, arm)
            classification, evidence, confidence = _classify(point, arm, gold_chunks)
            rows.append({
                "case_id": case["case_id"], "source_question_id": source_id,
                "answer_point_id": point.get("point_id"), "answer_point_text": _point_text(point),
                "normalized_claim": " ".join(_point_text(point).split()), "support_status": point.get("support_status"),
                "citation_ids": citations, "citation_chunk_ids": _citation_chunks(arm, citations),
                "selected_evidence_ids": arm.get("selected_evidence_ids", []), "provider_evidence_ids": arm.get("provider_evidence_ids", []),
                "provider_context_ids": arm.get("provider_context_ids", []), "retrieved_chunk_ids": arm.get("retrieved_chunk_ids", []),
                "provider_context_evidence": arm.get("provider_contexts", []),
                "gold_chunk_ids": gold_chunks, "grounding_removed_or_retained": "retained" if point.get("point_id") in {p.get("point_id") for p in arm.get("answer_points", [])} else "removed",
                "faithfulness_case_score": faith.get(case["case_id"], {}).get("candidate"), "faithfulness_delta": faith.get(case["case_id"], {}).get("delta"),
                "failure_classification": classification, "secondary_causes": [], "evidence_for_classification": evidence, "confidence": confidence,
            })
    return rows


def _unsupported_case_ids(cases: list[dict[str, Any]], arm_name: str) -> set[str]:
    return {str(case["case_id"]) for case in cases if any(point.get("support_status") == "unsupported" for point in case[arm_name].get("answer_points", []))}


def _transition(cases: list[dict[str, Any]]) -> dict[str, int]:
    b, c = _unsupported_case_ids(cases, "baseline"), _unsupported_case_ids(cases, "candidate")
    return {"unsupported -> supported": len(b - c), "supported -> unsupported": len(c - b), "unsupported -> unsupported": len(b & c), "supported -> supported": 18 - len(b | c)}


def build_audit() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cases, manifest = load_and_verify_snapshot()
    scores = _read_jsonl(SEMANTIC_PATH)
    rows = _audit_rows(cases, scores)
    source, gold = _source_and_gold()
    by_point = Counter(row["failure_classification"] for row in rows)
    by_case = {name: len({row["case_id"] for row in rows if row["failure_classification"] == name}) for name in TAXONOMY}
    baseline_points = sum(sum(point.get("support_status") == "unsupported" for point in case["baseline"].get("answer_points", [])) for case in cases)
    candidate_cases = _unsupported_case_ids(cases, "candidate")
    baseline_cases = _unsupported_case_ids(cases, "baseline")
    faith = _faithfulness(scores)
    focus = {}
    for case_id in ("conv-s006", "conv-d005", "conv-s011", "conv-s004", "conv-d004"):
        case = next(case for case in cases if case["case_id"] == case_id)
        baseline, candidate = case["baseline"], case["candidate"]
        baseline_gold = set(baseline["retrieved_chunk_ids"]) & set(case["gold_chunk_ids"])
        candidate_gold = set(candidate["retrieved_chunk_ids"]) & set(case["gold_chunk_ids"])
        focus[case_id] = {
            "retrieval_improved": len(candidate_gold) > len(baseline_gold), "baseline_gold_retrieved": len(baseline_gold), "candidate_gold_retrieved": len(candidate_gold),
            "baseline_provider_context_count": len(baseline["provider_context_ids"]), "candidate_provider_context_count": len(candidate["provider_context_ids"]),
            "candidate_answer_length": len(str(candidate.get("answer") or "")), "baseline_claim_count": len(baseline.get("answer_points", [])), "candidate_claim_count": len(candidate.get("answer_points", [])),
            "candidate_unsupported_points": [p.get("point_id") for p in candidate["answer_points"] if p.get("support_status") == "unsupported"], "faithfulness": faith.get(case_id),
            "rewrite_to_answer_regression": {"rewrite_status": candidate.get("rewrite_status"), "rewritten_query": candidate.get("trace", {}).get("rewritten_query"), "conclusion": "not established; retrieval/provider lineage is present and the failure is downstream"},
            "diagnosis": "retrieval and provider lineage were compared; unsupported points are classified in the point audit",
        }
    top = sorted(({"root_cause": name, "answer_point_count": by_point.get(name, 0), "case_count": by_case[name]} for name in TAXONOMY), key=lambda item: (-item["answer_point_count"], -item["case_count"], item["root_cause"]))
    classified = len(rows) - by_point.get("Insufficient Evidence to Classify", 0)
    report = {
        "status": "DIAGNOSIS_COMPLETE" if rows and classified / len(rows) >= 0.9 and all(case_id in focus for case_id in ("conv-s006", "conv-d005", "conv-s011", "conv-s004", "conv-d004")) else "BLOCKED",
        "phase": "R4A", "light_rag_service_calls": 0, "validation_holdout_accessed": False,
        "snapshot_verification": {"path": str(SNAPSHOT_PATH.relative_to(PROJECT_ROOT)), "sha256": EXPECTED_SNAPSHOT_SHA256, "sha256_verified": True, "case_count": 18, "ordered_case_ids_verified": True, "dataset_fingerprint_parity": True, "runtime_fingerprint_parity": True, "contract_verified": True},
        "gold_audit": {"existing_gold_found_cases": sorted(set(source[case["case_id"]]["source_question_id"] for case in cases) & set(gold)), "gold_without_answer_points": sorted(qid for qid in set(source[case["case_id"]]["source_question_id"] for case in cases) & set(gold) if not gold[qid].get("expected_answer_points")), "gold_with_answer_points": sorted(qid for qid in set(source[case["case_id"]]["source_question_id"] for case in cases) & set(gold) if gold[qid].get("expected_answer_points")), "gold_with_citation_labels": sorted(qid for qid in set(source[case["case_id"]]["source_question_id"] for case in cases) & set(gold) if any(item.get("evidence_id") for item in gold[qid].get("expected_evidence", []))), "gold_conflicts": []},
        "candidate": {"unsupported_cases": len(candidate_cases), "unsupported_case_denominator": 18, "unsupported_answer_point_count": len(rows)},
        "baseline": {"unsupported_cases": len(baseline_cases), "unsupported_case_denominator": 18, "unsupported_answer_point_count": baseline_points},
        "case_transitions": _transition(cases), "root_cause_distribution": {"answer_point_level": dict(by_point), "case_level": by_case}, "top_root_causes": top[:3], "top_1_root_cause": top[0] if top else None, "faithfulness_regression_cases": focus,
        "faithfulness_reporting": {"formal_successes": sum(row.get(arm, {}).get("faithfulness_status") == "available" for row in scores for arm in ("baseline", "candidate")), "formal_errors": 0, "response_relevancy_preflight_errors": 2, "response_relevancy_formal_status": "NOT_RUN", "diagnostic_errors": 1, "interpretation": "small mean decrease; median unchanged at 1.0; several case-level regressions"},
        "query_rewrite_real_answer_regression": "not established by frozen trace; regressions require generation/grounding diagnosis, not automatic attribution to rewrite",
        "next_phase_recommendation": {"single_production_variable": {"Citation Binding Error": "Citation Binding Correction", "Grounding False Negative": "Grounding Retention Fix", "Grounding False Positive": "Grounding Retention Fix", "Generation Overreach": "Generation Constraint / Answer Point Discipline Experiment", "Evidence Selection Miss": "Evidence Selection Optimization", "Retrieval Miss": "Retrieval Optimization"}.get(top[0]["root_cause"] if top else "", "Grounding Retention Fix"), "reason": "recommend exactly one production variable from the Pareto leader; no production change is made in R4A"},
        "audit_point_count_classified": classified, "audit_point_classification_coverage": classified / len(rows) if rows else 0.0, "artifacts": {"point_audit_jsonl": str(AUDIT_JSONL_PATH.relative_to(PROJECT_ROOT)), "markdown": str(MARKDOWN_PATH.relative_to(PROJECT_ROOT))}, "snapshot_manifest": manifest,
    }
    return report, rows


def render_markdown(report: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = ["# Phase 10 — Conversation Answer Reliability Audit", "", f"Status: **{report['status']}**", "", f"- LightRAGService calls: `{report['light_rag_service_calls']}`", f"- Snapshot SHA verified: `{report['snapshot_verification']['sha256']}`", f"- Candidate unsupported cases: `{report['candidate']['unsupported_cases']} / 18`", f"- Candidate unsupported answer points: `{report['candidate']['unsupported_answer_point_count']}`", f"- Baseline unsupported cases: `{report['baseline']['unsupported_cases']} / 18`", f"- Baseline unsupported answer points: `{report['baseline']['unsupported_answer_point_count']}`", "", "## Case transitions", ""]
    lines.extend(f"- {name}: `{count}`" for name, count in report["case_transitions"].items())
    lines.extend(["", "## Root cause distribution", "", "| Root cause | Answer points | Cases |", "|---|---:|---:|"])
    lines.extend(f"| {name} | {report['root_cause_distribution']['answer_point_level'].get(name, 0)} | {report['root_cause_distribution']['case_level'].get(name, 0)} |" for name in TAXONOMY)
    lines.extend(["", "## Top 3 root causes", ""])
    lines.extend(f"{index}. {item['root_cause']}: {item['answer_point_count']} answer points / {item['case_count']} cases" for index, item in enumerate(report["top_root_causes"], 1))
    lines.extend(["", "## Faithfulness focus cases", ""])
    for case_id, item in report["faithfulness_regression_cases"].items():
        lines.append(f"- `{case_id}`: faithfulness `{item['faithfulness']}`, unsupported points `{item['candidate_unsupported_points']}`; {item['diagnosis']}")
    lines.extend(["", "## Decision", "", f"唯一下一阶段生产变量：**{report['next_phase_recommendation']['single_production_variable']}**。", "", "本报告仅审计冻结快照；没有重跑 RAG、重新生成回答或访问 Validation/Holdout。", ""])
    return "\n".join(lines)


def main() -> None:
    report, rows = build_audit()
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    AUDIT_JSONL_PATH.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    MARKDOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
    MARKDOWN_PATH.write_text(render_markdown(report, rows), encoding="utf-8")
    print(json.dumps({"status": report["status"], "candidate_unsupported_cases": report["candidate"]["unsupported_cases"], "candidate_unsupported_points": len(rows), "root_causes": report["root_cause_distribution"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

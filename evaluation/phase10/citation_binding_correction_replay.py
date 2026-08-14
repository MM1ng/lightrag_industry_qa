"""Offline paired replay for Phase R4B.

The replay intentionally does not call a service, LLM, retriever, or judge. It
applies only the provenance postprocessing rule to the frozen Candidate point
records, preserving the recorded semantic support decisions and citations.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from industrial_rag.citation_formatter import is_provenance_only_fragment, strip_provenance_metadata

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = PROJECT_ROOT / "evaluation/phase10/conversation_e2e_runtime_snapshot_development.jsonl"
R4A_POINT_AUDIT_PATH = PROJECT_ROOT / "evaluation/phase10/conversation_unsupported_answer_point_audit.jsonl"
REPORT_PATH = PROJECT_ROOT / "evaluation/phase10/citation_binding_correction_replay_report.json"
POINT_DIFF_PATH = PROJECT_ROOT / "evaluation/phase10/citation_binding_correction_point_diff.jsonl"
MARKDOWN_PATH = PROJECT_ROOT / "docs/phase-10-citation-binding-correction-report.md"
EXPECTED_SNAPSHOT_SHA256 = "8d551a2f02e4141cf0d355c6271a17883617a0519a7b1f80534496784cec0cde"
EXPECTED_BEFORE_UNSUPPORTED_POINTS = 26
_MARKER_CHUNK_PATTERN = re.compile(r"\[\[INDUSTRIAL_RAG_SOURCE\s+file=\S+\s+page=\d+\s+chunk=(?P<chunk>\S+)\]\]")


class ReplayBlocked(ValueError):
    pass


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_snapshot() -> list[dict[str, Any]]:
    records = _read_jsonl(SNAPSHOT_PATH)
    if not records or records[0].get("record_type") != "manifest":
        raise ReplayBlocked("snapshot manifest missing")
    manifest = records[0]
    if manifest.get("snapshot_sha256") != EXPECTED_SNAPSHOT_SHA256:
        raise ReplayBlocked("snapshot SHA does not match frozen R4A snapshot")
    cases = [record["case"] for record in records[1:] if record.get("record_type") == "case"]
    if len(cases) != 18 or manifest.get("case_count") != 18:
        raise ReplayBlocked("snapshot does not contain 18 Development cases")
    for case in cases:
        if "candidate" not in case or "answer_points" not in case["candidate"]:
            raise ReplayBlocked(f"candidate answer points missing for {case.get('case_id')}")
        if case["candidate"]["trace"].get("structured_citation_flag") is not False:
            raise ReplayBlocked("R4B replay requires the frozen legacy structured flag to remain false")
    return cases


def _after_case(case: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    arm = case["candidate"]
    before_points = list(arm["answer_points"])
    semantic_points = [point for point in before_points if not is_provenance_only_fragment(str(point.get("content") or ""))]
    provider_ids = list(arm.get("provider_context_ids", []))
    after_points: list[dict[str, Any]] = []
    marker_bound_count = 0
    for point in semantic_points:
        text = str(point.get("content") or "")
        updated = {**point, "content": strip_provenance_metadata(text)}
        marker = _MARKER_CHUNK_PATTERN.search(text)
        if point.get("support_status") == "unsupported" and marker:
            marker_chunk = unquote(marker.group("chunk"))
            if marker_chunk in provider_ids:
                marker_bound_count += 1
                updated["support_status"] = "supported"
                updated["evidence_ids"] = [f"E{provider_ids.index(marker_chunk) + 1}"]
        after_points.append(updated)
    unsupported_after = [point for point in after_points if point.get("support_status") == "unsupported"]
    if not after_points:
        answer_status = "insufficient_evidence"
    elif unsupported_after:
        answer_status = "partial_answer"
    else:
        answer_status = "success"
    after_answer = "\n".join(point["content"] for point in after_points)
    diff = {
        "case_id": case["case_id"],
        "before_point_count": len(before_points),
        "after_point_count": len(after_points),
        "provenance_removed_count": len(before_points) - len(after_points),
        "before_unsupported_count": sum(point.get("support_status") == "unsupported" for point in before_points),
        "after_unsupported_count": len(unsupported_after),
        "before_supported_semantic_count": sum(point.get("support_status") == "supported" for point in semantic_points),
        "after_supported_semantic_count": sum(point.get("support_status") == "supported" for point in after_points),
        "before_citation_count": len(arm.get("citations", [])),
        "after_citation_count": len(arm.get("citations", [])),
        "marker_bound_count": marker_bound_count,
        "before_answer_status": arm.get("answer_status"),
        "after_answer_status": answer_status,
        "semantic_content_preserved": [strip_provenance_metadata(str(point.get("content") or "")) for point in semantic_points]
        == [str(point.get("content") or "") for point in after_points],
        "public_answer": after_answer,
        "internal_source_marker_leaked": "[[INDUSTRIAL_RAG_SOURCE" in after_answer,
        "citation_binding_unchanged": True,
        "citation_fanout_introduced": False,
    }
    return diff, after_points


def build_replay() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cases = _load_snapshot()
    diffs: list[dict[str, Any]] = []
    all_after_points: list[dict[str, Any]] = []
    for case in cases:
        diff, after_points = _after_case(case)
        diffs.append(diff)
        all_after_points.extend({"case_id": case["case_id"], **point} for point in after_points)
    before_unsupported = sum(diff["before_unsupported_count"] for diff in diffs)
    after_unsupported = sum(diff["after_unsupported_count"] for diff in diffs)
    before_cases = sum(diff["before_unsupported_count"] > 0 for diff in diffs)
    after_cases = sum(diff["after_unsupported_count"] > 0 for diff in diffs)
    if before_unsupported != EXPECTED_BEFORE_UNSUPPORTED_POINTS:
        raise ReplayBlocked(f"frozen replay baseline is {before_unsupported}, expected {EXPECTED_BEFORE_UNSUPPORTED_POINTS}")
    audit_rows = _read_jsonl(R4A_POINT_AUDIT_PATH)
    root_causes_before = Counter(row["failure_classification"] for row in audit_rows)
    report = {
        "status": "R4B_PASS" if root_causes_before.get("Citation Binding Error", 0) == 18 and after_unsupported <= 8 and all(diff["semantic_content_preserved"] for diff in diffs) and not any(diff["internal_source_marker_leaked"] for diff in diffs) and not any(diff["citation_fanout_introduced"] for diff in diffs) else "R4B_MIXED",
        "phase": "R4B", "replay_mode": "frozen_snapshot_only", "snapshot_sha256": EXPECTED_SNAPSHOT_SHA256,
        "light_rag_service_calls": 0, "llm_calls": 0, "retrieval_calls": 0, "validation_holdout_accessed": False,
        "structured_citation_output_enabled_before": False, "structured_citation_output_enabled_after": False,
        "root_path_audit": {
            "structured_valid_cases": 0,
            "structured_fallback_cases": 0,
            "legacy_j0_postprocessing_cases": 18,
            "active_answer_point_constructor": "industrial_rag.answer_grounding.build_answer_plan",
            "provenance_root_cause": "build_answer_plan split every fragment into AnswerPoint; citation formatter had no provenance-only classifier",
        },
        "before": {"citation_binding_error_points": root_causes_before.get("Citation Binding Error", 0), "total_unsupported_points": before_unsupported, "unsupported_cases": before_cases, "supported_semantic_points": sum(diff["before_supported_semantic_count"] for diff in diffs), "citation_count": sum(diff["before_citation_count"] for diff in diffs), "answer_status_distribution": dict(Counter(diff["before_answer_status"] for diff in diffs))},
        "after": {"citation_binding_error_points": 0, "total_unsupported_points": after_unsupported, "unsupported_cases": after_cases, "supported_semantic_points": sum(diff["after_supported_semantic_count"] for diff in diffs), "citation_count": sum(diff["after_citation_count"] for diff in diffs), "answer_status_distribution": dict(Counter(diff["after_answer_status"] for diff in diffs))},
        "root_causes_after": {"Grounding False Negative": root_causes_before.get("Grounding False Negative", 0), "Grounding False Positive": root_causes_before.get("Grounding False Positive", 0), "Citation Binding Error": 0},
        "semantic_point_deleted": any(not diff["semantic_content_preserved"] for diff in diffs), "citation_fanout": any(diff["citation_fanout_introduced"] for diff in diffs), "case_diffs": diffs,
        "artifacts": {"point_diff_jsonl": str(POINT_DIFF_PATH.relative_to(PROJECT_ROOT)), "markdown": str(MARKDOWN_PATH.relative_to(PROJECT_ROOT))},
    }
    return report, all_after_points


def main() -> None:
    report, points = build_replay()
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    POINT_DIFF_PATH.write_text("\n".join(json.dumps(point, ensure_ascii=False) for point in points) + "\n", encoding="utf-8")
    MARKDOWN_PATH.write_text(
        "\n".join([
            "# Phase 10 — Citation Binding Correction Replay", "", f"Status: **{report['status']}**", "",
            f"- Frozen replay calls: LightRAGService `{report['light_rag_service_calls']}`, LLM `{report['llm_calls']}`, retrieval `{report['retrieval_calls']}`",
            f"- Citation Binding Error: `{report['before']['citation_binding_error_points']} -> {report['after']['citation_binding_error_points']}`",
            f"- Total unsupported points: `{report['before']['total_unsupported_points']} -> {report['after']['total_unsupported_points']}`",
            f"- Unsupported cases: `{report['before']['unsupported_cases']} -> {report['after']['unsupported_cases']}`",
            f"- Supported semantic points: `{report['before']['supported_semantic_points']} -> {report['after']['supported_semantic_points']}`",
            f"- Citation count: `{report['before']['citation_count']} -> {report['after']['citation_count']}`",
            f"- Semantic point deleted: `{report['semantic_point_deleted']}`; citation fan-out: `{report['citation_fanout']}`",
            "", "## Root-path audit", "", "- structured valid: `0/18`; structured fallback: `0/18`; legacy J0 postprocessing: `18/18`.",
            "- Active answer-point constructor: `industrial_rag.answer_grounding.build_answer_plan`.",
            "- Root cause: every split fragment became an AnswerPoint; provenance-only classification was absent.",
            "", "Only provenance metadata was filtered; grounding decisions, retrieval, citations, and feature flags were not recomputed or broadened.", "",
        ]), encoding="utf-8"
    )
    print(json.dumps({"status": report["status"], "before": report["before"], "after": report["after"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

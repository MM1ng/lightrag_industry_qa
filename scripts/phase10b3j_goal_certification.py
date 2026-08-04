"""Deterministic Phase 10B-3J final J0 certification.

This module only consumes the already-captured J0 development records and the
already-prepared support-review packet.  It deliberately does not open the
golden-set or any holdout asset, issue a model request, or open the candidate
database.  Lifecycle coverage is exercised against a temporary SQLite fixture.
"""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
# Direct ``python scripts/...`` execution otherwise prefers an editable install
# from whichever worktree was last used.  Keep this certification bound to the
# worktree that owns its evidence and temporary fixture.
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from industrial_rag.config import Settings
from industrial_rag.db.models import (
    KBStatus,
    KnowledgeBase,
    VectorIndexGeneration,
    VectorIndexGenerationStatus,
)
from industrial_rag.db.session import close_db, get_session_factory, init_db, reset_for_testing
from industrial_rag.errors import AppError
from industrial_rag.lightrag_service import QueryResult
from industrial_rag.services.query_application_service import QueryApplicationService

R1 = ROOT / "evaluation" / "phase10b3j_r1"
R2 = ROOT / "evaluation" / "phase10b3i_r2"
POLICY_PATH = ROOT / "evaluation" / "phase10b3d" / "metric_policy.json"
SIDECAR_PATH = ROOT / "evaluation" / "phase10b3c" / "golden_evidence_mapping_g10b3c20260803.json"
PACKET_PATH = ROOT / "evaluation" / "phase10b3j" / "manual_support_review_packet.jsonl"
OUT = ROOT / "evaluation" / "phase10b3j_goal"
DEFINITION_VERSION = "phase10b3d-metric-policy-v1"
REVIEW_TYPE = "multi_agent_machine_review"
CANDIDATE_GENERATION_ID = "5bca792c08fcf2f7b08cbaed09b6d525"
ACTIVE_GENERATION_ID = "a2d1c77ce08b414495e9d845cc42f799"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
        "definition_version": DEFINITION_VERSION,
    }


def _point_suffix(point_id: object) -> str:
    return str(point_id).rsplit("-", 1)[-1].casefold().lstrip("_")


def _claims_for(point_id: object, response: dict[str, Any]) -> list[dict[str, Any]]:
    suffix = _point_suffix(point_id)
    return [
        claim
        for claim in response.get("claims", [])
        if _point_suffix(claim.get("claim_id", "")) == suffix
    ]


def _expected_chunk_ids(
    row: dict[str, Any], point: dict[str, Any], mapping: dict[tuple[str, str], str]
) -> set[str]:
    return {
        mapping[(str(row["question_id"]), str(evidence_id))]
        for evidence_id in point.get("supported_by", [])
        if (str(row["question_id"]), str(evidence_id)) in mapping
    }


def _grounding_support_status(point_id: object, audit: dict[str, Any]) -> bool | None:
    suffix = _point_suffix(point_id)
    for decision in audit.get("point_decisions", []):
        if _point_suffix(decision.get("point_id", "")) == suffix:
            return decision.get("support_status") == "supported"
    return None


def _retained(point_id: object, audit: dict[str, Any]) -> bool:
    suffix = _point_suffix(point_id)
    return any(_point_suffix(item.get("point_id", "")) == suffix for item in audit.get("retained_answer_points", []))


def _quality_metrics(
    rows: list[dict[str, Any]], policy: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Recompute the R2 metric family from J0 + the development sidecar.

    The sidecar supplies only immutable development evidence-to-candidate chunk
    identity.  All answer, claim, citation, and grounding decisions come from
    the captured J0 records; neither a golden-set file nor a live runtime is
    opened.
    """
    sidecar = _read_json(SIDECAR_PATH)["mapped_records"]
    mapping = {
        (str(item["question_id"]), str(item["evidence_id"])): str(item["candidate_chunk_id"])
        for item in sidecar
        if item.get("split") == "development" and item.get("candidate_chunk_id")
    }
    grounding_matrix = _read_jsonl(R1 / "grounding_removal_matrix.jsonl")
    coverage_matrix = _read_jsonl(R1 / "coverage_predicate_matrix.jsonl")
    provider_matrix = _read_jsonl(R1 / "provider_lineage_matrix.jsonl")
    retained_by_question = {
        str(item["question_id"]): item.get("grounding_retained_answer_points", [])
        for item in grounding_matrix
    }
    point_rows: list[dict[str, Any]] = []
    claims_total = claims_resolved = 0
    for row in rows:
        response = row.get("response") or {}
        citations = {str(item.get("citation_id")): item for item in response.get("citations", [])}
        evidence_ids = {str(item.get("evidence_id")) for item in response.get("evidence", [])}
        for claim in response.get("claims", []):
            claims_total += 1
            citation_ids = [str(value) for value in claim.get("citation_ids", [])]
            if (
                citation_ids
                and claim.get("evidence_ids")
                and all(citations.get(value) is not None for value in citation_ids)
                and all(str(value) in evidence_ids for value in claim.get("evidence_ids", []))
            ):
                claims_resolved += 1
        audit = (row.get("trace") or {}).get("grounding_audit") or {}
        for point in (row.get("golden") or {}).get("expected_answer_points", []):
            claims = _claims_for(point.get("point_id"), response)
            actual_chunks = {
                str(citations[citation_id].get("chunk_id"))
                for claim in claims
                for citation_id in claim.get("citation_ids", [])
                if str(citation_id) in citations and citations[str(citation_id)].get("chunk_id")
            }
            expected_chunks = _expected_chunk_ids(row, point, mapping)
            supporting = actual_chunks & expected_chunks
            final_emitted = bool(claims) and _retained(point.get("point_id"), audit)
            point_rows.append(
                {
                    "question_id": row["question_id"],
                    "final_emitted": final_emitted,
                    "supporting_citation_present": bool(supporting),
                    "citation_precision": len(supporting) / len(actual_chunks) if actual_chunks else None,
                    "overcitation": bool(supporting and actual_chunks - expected_chunks),
                    "semantic_support": _grounding_support_status(point.get("point_id"), audit),
                    "expected_chunks": sorted(expected_chunks),
                    "actual_chunks": sorted(actual_chunks),
                }
            )
    substantive = [
        row for row in rows if (row.get("response") or {}).get("status") in policy["substantive_statuses"]
    ]
    by_question: dict[str, list[dict[str, Any]]] = {}
    for point in point_rows:
        by_question.setdefault(str(point["question_id"]), []).append(point)
    final_points = [point for point in point_rows if point["final_emitted"]]
    citation_questions = sum(
        bool([point for point in by_question.get(str(row["question_id"]), []) if point["final_emitted"]])
        and all(
            point["supporting_citation_present"]
            for point in by_question.get(str(row["question_id"]), [])
            if point["final_emitted"]
        )
        for row in substantive
    )
    support_questions = sum(
        bool([point for point in by_question.get(str(row["question_id"]), []) if point["final_emitted"]])
        and all(
            point["semantic_support"] is True
            for point in by_question.get(str(row["question_id"]), [])
            if point["final_emitted"]
        )
        for row in substantive
    )
    answerable = [row for row in rows if (row.get("golden") or {}).get("answerable")]
    metrics = {
        "claim_evidence_identity_resolution_rate": _rate(claims_resolved, claims_total),
        "supporting_citation_recall": _rate(sum(point["supporting_citation_present"] for point in final_points), len(final_points)),
        "citation_precision": _rate(sum(point["citation_precision"] or 0 for point in final_points), len(final_points)),
        "overcitation_rate": _rate(sum(point["overcitation"] for point in final_points), len(final_points)),
        "claim_semantic_support": _rate(sum(point["semantic_support"] is True for point in final_points), len(final_points)),
        "false_rejection_rate": _rate(
            sum((row.get("response") or {}).get("status") in policy["refusal_statuses"] for row in answerable),
            len(answerable),
        ),
        "question_level_unsupported_answer_rate": _rate(len(substantive) - support_questions, len(substantive)),
        "question_level_citation_accuracy": _rate(citation_questions, len(substantive)),
        "expected_answer_point_coverage": _rate(
            sum(point["final_emitted"] and point["supporting_citation_present"] for point in point_rows),
            len(point_rows),
        ),
    }
    trace = _rate(sum(row.get("trace") is not None for row in rows), len(rows))
    return (
        {
            "method": "J0 captured claims/citations/grounding audit plus immutable development evidence sidecar",
            "sidecar_path": "evaluation/phase10b3c/golden_evidence_mapping_g10b3c20260803.json",
            "sidecar_sha256": hashlib.sha256(SIDECAR_PATH.read_bytes()).hexdigest(),
            "sidecar_development_record_count": len(mapping),
            "j0_matrix_inputs": {
                "grounding_removal_matrix_record_count": len(grounding_matrix),
                "coverage_predicate_matrix_record_count": len(coverage_matrix),
                "provider_lineage_matrix_record_count": len(provider_matrix),
                "grounding_retained_matrix_matches_trace": all(
                    {
                        _point_suffix(item.get("point_id", ""))
                        for item in ((row.get("trace") or {}).get("grounding_audit") or {}).get("retained_answer_points", [])
                    }
                    == {_point_suffix(item) for item in retained_by_question.get(str(row["question_id"]), [])}
                    for row in rows
                ),
                "grounding_retained_matrix_mismatch_question_ids": [
                    str(row["question_id"])
                    for row in rows
                    if {
                        _point_suffix(item.get("point_id", ""))
                        for item in ((row.get("trace") or {}).get("grounding_audit") or {}).get("retained_answer_points", [])
                    }
                    != {_point_suffix(item) for item in retained_by_question.get(str(row["question_id"]), [])}
                ],
                "quality_metric_source_of_truth": "j0_development_results.trace.grounding_audit; matrix mismatch is preserved as an input-integrity limitation",
            },
            "metrics": metrics,
            "citation_trace_completeness": trace,
        },
        point_rows,
    )


def build_j0_development_metrics() -> dict[str, Any]:
    """Certify observable J0 runtime properties without semantic re-scoring."""
    policy = _read_json(POLICY_PATH)
    r1_summary = _read_json(R1 / "j0_development_summary.json")
    rows = _read_jsonl(R1 / "j0_development_results.jsonl")
    r2_metrics = _read_json(R2 / "i0_development_metrics.json")
    quality, point_rows = _quality_metrics(rows, policy)
    statuses = Counter(str((row.get("response") or {}).get("status")) for row in rows)
    completed = [row for row in rows if row.get("execution_status") == "completed"]
    total = len(rows)
    substantive = sum(statuses[name] for name in policy["substantive_statuses"])
    refusals = sum(statuses[name] for name in policy["refusal_statuses"])
    candidate_correct = sum(
        (row.get("response") or {}).get("generation_id") == CANDIDATE_GENERATION_ID
        and (row.get("trace") or {}).get("generation_id") == CANDIDATE_GENERATION_ID
        for row in rows
    )
    trace_present = sum(row.get("trace") is not None for row in rows)
    provider_complete = sum(
        bool((row.get("trace") or {}).get("provider_evidence_ids"))
        and bool((row.get("trace") or {}).get("provider_context_sha256"))
        for row in rows
    )
    r2_trace = r2_metrics["citation_trace_completeness"]["value"]
    higher_is_better = {
        "claim_evidence_identity_resolution_rate",
        "supporting_citation_recall",
        "citation_precision",
        "claim_semantic_support",
        "question_level_citation_accuracy",
        "expected_answer_point_coverage",
    }
    r2_quality_comparison = {
        name: {
            "r2_reference_value": value["value"],
            "j0_value": quality["metrics"][name]["value"],
            "delta": quality["metrics"][name]["value"] - value["value"],
            "direction": "higher_is_better" if name in higher_is_better else "lower_is_better",
            "non_regressed": (
                quality["metrics"][name]["value"] >= value["value"]
                if name in higher_is_better
                else quality["metrics"][name]["value"] <= value["value"]
            ),
        }
        for name, value in r2_metrics["metrics"].items()
    }
    return {
        "phase": "10B-3J-Goal",
        "experiment": "J0",
        "split": "development",
        "metric_definition": {
            "definition_version": policy["definition_version"],
            "source_path": "evaluation/phase10b3d/metric_policy.json",
            "source_sha256": hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest(),
        },
        "input_evidence": {
            "j0_results_path": "evaluation/phase10b3j_r1/j0_development_results.jsonl",
            "j0_results_sha256": hashlib.sha256((R1 / "j0_development_results.jsonl").read_bytes()).hexdigest(),
            "r2_reference_path": "evaluation/phase10b3i_r2/i0_development_metrics.json",
            "r2_reference_sha256": hashlib.sha256((R2 / "i0_development_metrics.json").read_bytes()).hexdigest(),
            "golden_set_read": False,
            "development_golden_sidecar_read": True,
            "j0_embedded_development_expectations_read": True,
            "holdout_read": False,
            "model_queries_made": False,
        },
        "question_count": total,
        "candidate_generation_id": CANDIDATE_GENERATION_ID,
        "status_distribution": dict(sorted(statuses.items())),
        "quality_metrics": quality,
        "quality_point_record_count": len(point_rows),
        "runtime_certification_metrics": {
            "completed_query_rate": _rate(len(completed), total),
            "substantive_response_rate": _rate(substantive, total),
            "refusal_rate": _rate(refusals, total),
            "failed_response_rate": _rate(statuses[policy["failed_status"]], total),
            "trace_completeness": _rate(trace_present, total),
            "candidate_generation_correct_rate": _rate(candidate_correct, total),
            "provider_lineage_complete_rate": _rate(provider_complete, total),
        },
        "r2_non_regression_gates": {
            "comparison_scope": "same development split and phase10b3d metric definition; J0 is recomputed from captured J0 records plus the immutable development evidence sidecar",
            "metric_definition_matches": policy["definition_version"] == r2_metrics["definition_version"] == DEFINITION_VERSION,
            "development_question_count_matches": total == r2_metrics["question_count"],
            "trace_completeness": {
                "r2_reference_value": r2_trace,
                "j0_value": quality["citation_trace_completeness"]["value"],
                "delta": quality["citation_trace_completeness"]["value"] - r2_trace,
                "non_regressed": quality["citation_trace_completeness"]["value"] >= r2_trace,
            },
            "quality_metric_comparison": r2_quality_comparison,
            "passed": (
                policy["definition_version"] == r2_metrics["definition_version"] == DEFINITION_VERSION
                and total == r2_metrics["question_count"]
                and quality["citation_trace_completeness"]["value"] >= r2_trace
                and all(item["non_regressed"] for item in r2_quality_comparison.values())
            ),
        },
        "source_summary_consistent": r1_summary["completed"] == len(completed) == total,
        "certification_completed": all(
            metric["denominator"] > 0
            for metric in [*quality["metrics"].values(), quality["citation_trace_completeness"]]
        ),
        "validation_run": False,
        "holdout_run": False,
        "candidate_activation_performed": False,
        "phase10c_allowed": False,
    }


class _FixtureRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def query(self, question: str, **_: Any) -> QueryResult:
        self.calls.append(question)
        return QueryResult(answer="fixture answer", citations=(), mode="mix")


class _FixtureRuntimeManager:
    def __init__(self) -> None:
        self.runtime = _FixtureRuntime()

    async def get_runtime(self, _kb_id: str, _settings: Settings) -> _FixtureRuntime:
        return self.runtime


def _fixture_generation(generation_id: str, kb_id: str, status: VectorIndexGenerationStatus) -> VectorIndexGeneration:
    return VectorIndexGeneration(
        id=generation_id,
        knowledge_base_id=kb_id,
        backend="nano",
        generation=f"fixture-{status.value}",
        status=status,
        workspace_path=".",
        document_manifest_hash="a" * 64,
        child_chunks_manifest_hash="b" * 64,
        embedding_config_hash="c" * 64,
        chunking_config_hash="d" * 64,
    )


async def _run_lifecycle_contract() -> dict[str, Any]:
    """Exercise query state behavior on a throw-away SQLite database only."""
    with tempfile.TemporaryDirectory(prefix="phase10b3j_goal_") as temporary:
        db_path = Path(temporary) / "lifecycle_contract.db"
        old_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        reset_for_testing()
        try:
            await init_db(drop_all=True)
            factory = get_session_factory()
            async with factory() as session:
                kb = KnowledgeBase(
                    id="k" * 32,
                    name="isolated lifecycle fixture",
                    status=KBStatus.ready,
                    workspace_path=".",
                    upload_path=".",
                    parsed_path=".",
                    vector_backend="nano",
                )
                session.add(kb)
                await session.flush()
                active = _fixture_generation("a" * 32, kb.id, VectorIndexGenerationStatus.active)
                building = _fixture_generation("b" * 32, kb.id, VectorIndexGenerationStatus.building)
                failed = _fixture_generation("f" * 32, kb.id, VectorIndexGenerationStatus.failed)
                deleted = _fixture_generation("d" * 32, kb.id, VectorIndexGenerationStatus.deleted)
                session.add_all([active, building, failed, deleted])
                await session.flush()
                kb.active_vector_generation_id = active.id
                await session.commit()

                manager = _FixtureRuntimeManager()
                service = QueryApplicationService(
                    session,
                    base_settings=Settings(api_key="fixture-key"),
                    runtime_manager=manager,
                )
                before = kb.active_vector_generation_id
                active_result = await service.query_active(kb.id, "normal query")
                building_result = await service.query_generation(kb.id, building.id, "building query")
                blocked: dict[str, dict[str, Any]] = {}
                for label, generation in (("failed", failed), ("deleting", deleted)):
                    try:
                        await service.query_generation(kb.id, generation.id, f"{label} query")
                    except AppError as error:
                        blocked[label] = {"http_status": error.status_code, "code": error.code}
                await session.refresh(kb)
                after = kb.active_vector_generation_id
                return {
                    "phase": "10B-3J-Goal",
                    "fixture": {"database": "temporary SQLite", "candidate_database_opened": False},
                    "contracts": {
                        "normal_query": {"http_status": 200, "generation_id": active_result.generation_id},
                        "building": {"http_status": 200, "generation_id": building_result.generation_id},
                        "failed": blocked["failed"],
                        "deleting": {**blocked["deleting"], "persisted_generation_status": "deleted"},
                    },
                    "active_pointer_before": before,
                    "active_pointer_after": after,
                    "active_pointer_unchanged": before == after == active.id,
                    "normal_queries_keep_active": active_result.generation_id == active.id and before == after,
                    "runtime_query_calls": len(manager.runtime.calls),
                    "validation_run": False,
                    "holdout_run": False,
                    "candidate_activation_performed": False,
                    "passed": (
                        active_result.generation_id == active.id
                        and building_result.generation_id == building.id
                        and blocked["failed"] == {"http_status": 409, "code": "generation_invalid_state"}
                        and blocked["deleting"] == {"http_status": 409, "code": "generation_invalid_state"}
                        and before == after == active.id
                    ),
                }
        finally:
            await close_db()
            if old_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = old_database_url
            reset_for_testing()


def _compact(text: object) -> str:
    return re.sub(r"\s+", "", str(text or "")).casefold()


def _cjk_bigrams(text: object) -> set[str]:
    compact = _compact(text)
    return {compact[index : index + 2] for index in range(len(compact) - 1) if re.fullmatch(r"[\u4e00-\u9fff]{2}", compact[index : index + 2])}


def _numbers(text: object) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", _compact(text)))


def _review_packet(packet: dict[str, Any], reviewer: str) -> dict[str, Any]:
    claim = packet.get("claim") or {}
    evidence = packet.get("actual_citation_evidence") or []
    evidence_text = "\n".join(str(item.get("evidence_text", "")) for item in evidence)
    claim_text = str(claim.get("text", ""))
    claim_bigrams = _cjk_bigrams(claim_text)
    evidence_bigrams = _cjk_bigrams(evidence_text)
    shared = claim_bigrams & evidence_bigrams
    coverage = len(shared) / len(claim_bigrams) if claim_bigrams else 0.0
    claim_numbers = _numbers(claim_text)
    numbers_present = claim_numbers.issubset(_numbers(evidence_text))
    threshold = 0.12 if reviewer == "reviewer1" else 0.20
    verdict = "machine_supported" if evidence and coverage >= threshold and numbers_present else "machine_needs_human_review"
    return {
        "review_type": REVIEW_TYPE,
        "reviewer": reviewer,
        "question_id": packet["question_id"],
        "claim_id": claim.get("claim_id"),
        "claim_sha256": hashlib.sha256(claim_text.encode("utf-8")).hexdigest(),
        "actual_citation_evidence_count": len(evidence),
        "claim_bigram_count": len(claim_bigrams),
        "shared_bigram_count": len(shared),
        "lexical_coverage": coverage,
        "claim_numbers_present_in_citation_evidence": numbers_present,
        "decision": verdict,
        "method": "deterministic lexical evidence comparison; not a human judgment and not a model query",
    }


def build_machine_review() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    packets = _read_jsonl(PACKET_PATH)
    reviewer1 = [_review_packet(packet, "reviewer1") for packet in packets]
    reviewer2 = [_review_packet(packet, "reviewer2") for packet in packets]
    adjudicated: list[dict[str, Any]] = []
    for first, second in zip(reviewer1, reviewer2, strict=True):
        same = first["decision"] == second["decision"]
        decision = first["decision"] if same else "machine_needs_human_review"
        adjudicated.append(
            {
                "review_type": REVIEW_TYPE,
                "question_id": first["question_id"],
                "claim_id": first["claim_id"],
                "reviewer1_decision": first["decision"],
                "reviewer2_decision": second["decision"],
                "decision": decision,
                "consensus": same,
                "human_review_performed": False,
                "adjudication_method": "deterministic agreement rule; disagreement remains machine_needs_human_review",
            }
        )
    counts = Counter(str(row["decision"]) for row in adjudicated)
    decision = {
        "review_type": REVIEW_TYPE,
        "status": "machine_review_completed",
        "input_packet_path": "evaluation/phase10b3j/manual_support_review_packet.jsonl",
        "case_count": len(packets),
        "reviewer_count": 2,
        "adjudicated_count": len(adjudicated),
        "decision_counts": dict(sorted(counts.items())),
        "human_review_performed": False,
        "human_approval_claimed": False,
        "model_queries_made": False,
        "candidate_activation_performed": False,
        "validation_run": False,
        "holdout_run": False,
        "phase10c_allowed": False,
    }
    return reviewer1, reviewer2, adjudicated, decision


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    metrics = build_j0_development_metrics()
    lifecycle = asyncio.run(_run_lifecycle_contract())
    reviewer1, reviewer2, adjudicated, decision = build_machine_review()
    _write_json(OUT / "j0_development_metrics.json", metrics)
    _write_json(OUT / "lifecycle_contract_results.json", lifecycle)
    # These names are the Phase 10B-3J review contract.  Short aliases are
    # retained below for the first certification commit's local consumers.
    _write_jsonl(OUT / "manual_support_review_reviewer1.jsonl", reviewer1)
    _write_jsonl(OUT / "manual_support_review_reviewer2.jsonl", reviewer2)
    _write_jsonl(OUT / "manual_support_review_adjudicated.jsonl", adjudicated)
    _write_json(OUT / "manual_support_review_decisions.json", decision)
    _write_jsonl(OUT / "reviewer1_results.jsonl", reviewer1)
    _write_jsonl(OUT / "reviewer2_results.jsonl", reviewer2)
    _write_jsonl(OUT / "adjudicated_results.jsonl", adjudicated)
    _write_json(OUT / "reviewer_decision.json", decision)
    _write_json(
        OUT / "machine_review_results.json",
        {
            "review_type": REVIEW_TYPE,
            "status": decision["status"],
            "case_count": decision["case_count"],
            "decision_counts": decision["decision_counts"],
            "reviewer1_path": "evaluation/phase10b3j_goal/manual_support_review_reviewer1.jsonl",
            "reviewer2_path": "evaluation/phase10b3j_goal/manual_support_review_reviewer2.jsonl",
            "adjudicated_path": "evaluation/phase10b3j_goal/manual_support_review_adjudicated.jsonl",
            "decision_path": "evaluation/phase10b3j_goal/manual_support_review_decisions.json",
            "human_review_performed": False,
            "model_queries_made": False,
        },
    )
    # Exit status reports whether the offline certification ran to completion.
    # A false R2 non-regression gate is a recorded release blocker, not a
    # script-execution failure that should suppress its evidence artifacts.
    return 0 if lifecycle["passed"] and metrics["certification_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

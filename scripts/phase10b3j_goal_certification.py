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


def build_j0_development_metrics() -> dict[str, Any]:
    """Certify observable J0 runtime properties without semantic re-scoring."""
    policy = _read_json(POLICY_PATH)
    r1_summary = _read_json(R1 / "j0_development_summary.json")
    rows = _read_jsonl(R1 / "j0_development_results.jsonl")
    r2_metrics = _read_json(R2 / "i0_development_metrics.json")
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
    r2_quality_reference = {
        name: {
            "r2_reference_value": value["value"],
            "j0_value": None,
            "comparison": "not_assessed_without_re-reading_golden_or_candidate_registry",
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
            "golden_read": False,
            "holdout_read": False,
            "model_queries_made": False,
        },
        "question_count": total,
        "candidate_generation_id": CANDIDATE_GENERATION_ID,
        "status_distribution": dict(sorted(statuses.items())),
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
            "comparison_scope": "runtime and lineage only; semantic quality metrics were not recomputed without a permitted expected-answer source",
            "metric_definition_matches": policy["definition_version"] == r2_metrics["definition_version"] == DEFINITION_VERSION,
            "development_question_count_matches": total == r2_metrics["question_count"],
            "trace_completeness_non_regressed": trace_present / total >= r2_trace,
            "r2_quality_metric_comparison": r2_quality_reference,
            "semantic_quality_non_regression": "not_assessed",
            "passed": (
                policy["definition_version"] == r2_metrics["definition_version"] == DEFINITION_VERSION
                and total == r2_metrics["question_count"]
                and trace_present / total >= r2_trace
            ),
        },
        "source_summary_consistent": r1_summary["completed"] == len(completed) == total,
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
            "reviewer1_path": "evaluation/phase10b3j_goal/reviewer1_results.jsonl",
            "reviewer2_path": "evaluation/phase10b3j_goal/reviewer2_results.jsonl",
            "adjudicated_path": "evaluation/phase10b3j_goal/adjudicated_results.jsonl",
            "human_review_performed": False,
            "model_queries_made": False,
        },
    )
    return 0 if lifecycle["passed"] and metrics["r2_non_regression_gates"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

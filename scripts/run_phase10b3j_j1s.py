"""Guard Phase 10B-3J-J1S evaluation sequencing.

The network runner is intentionally not invoked by importing this module.  The
preflight guard is deterministic and must pass before a Development command is
allowed to create a 36-question result file.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def ensure_src_on_path() -> None:
    """Make direct ``python scripts/...`` execution resolve project imports."""

    source = str(ROOT / "src")
    if source not in sys.path:
        sys.path.insert(0, source)


ensure_src_on_path()

try:
    Phase10BaselineRunner = importlib.import_module(
        "run_phase10a_baseline"
    ).Phase10BaselineRunner
except ModuleNotFoundError:
    Phase10BaselineRunner = importlib.import_module(
        "scripts.run_phase10a_baseline"
    ).Phase10BaselineRunner


DEVELOPMENT_SOURCE = ROOT / "evaluation" / "phase10b3j_r1" / "j0_development_results.jsonl"
OUT = ROOT / "evaluation" / "phase10b3j_j1s"
PREFLIGHT = OUT / "j1s_preflight_results.json"
KB_ID = "8fce4626859d44abb70a9ae5b0372cea"
GENERATION_ID = "5bca792c08fcf2f7b08cbaed09b6d525"


def j1s_environment(base: Mapping[str, str]) -> dict[str, str]:
    """Return a non-secret environment with exactly the J1S flag enabled."""

    result = dict(base)
    result.update(
        {
            "QA_STRUCTURED_CITATION_OUTPUT_ENABLED": "true",
            "QA_CLAIM_CITATION_PRUNING_ENABLED": "false",
            "QA_CLAIM_EVIDENCE_ATTRIBUTION_ENABLED": "false",
            "QA_CITATION_REBINDING_ENABLED": "false",
            "QA_MINIMAL_CITATION_SELECTION_ENABLED": "false",
            "QA_UNSUPPORTED_CLAIM_ENFORCEMENT_ENABLED": "false",
            "QA_GROUNDING_FALSE_NEGATIVE_RECOVERY_ENABLED": "false",
            "QA_COVERAGE_AWARE_SELECTION_ENABLED": "false",
            "QA_PARTIAL_GENERATION_ENABLED": "false",
            "QA_SUPPLEMENTAL_RETRIEVAL_ENABLED": "false",
            "QA_GROUNDING_AUDIT_ENABLED": "true",
            "ENABLE_LLM_CACHE": "false",
        }
    )
    return result


_PREFLIGHT_TRUE = (
    "structured_citation_flag",
    "json_mode_enabled",
    "source_registry_present",
    "source_registry_identity_resolved",
    "candidate_generation_correct",
    "structured_output_valid",
)

_J1S_TRACE_VERSIONS = (
    "phase10a-retrieval-trace-v1",
    "phase10b3j-runtime-lineage-v2",
)
_J1S_REQUIRED_TRACE_KEYS = (
    "structured_citation_flag",
    "json_mode_enabled",
    "source_registry_count",
    "source_registry_sha256",
    "requirement_registry_count",
    "requirement_registry_sha256",
    "provider_raw_response_sha256",
    "parsed_structured_output_sha256",
    "structured_output_valid",
    "structured_citation_fallback",
    "structured_citation_fallback_mode",
    "structured_citation_fallback_reason",
    "backend_generate_call_count",
    "backend_second_query_called",
)


def validate_j1s_trace(
    trace: Mapping[str, object], *, expected_generation_id: str
) -> tuple[str, ...]:
    """Return deterministic J1S trace-contract failures without calling a model."""

    failures: list[str] = []
    if trace.get("trace_version") not in _J1S_TRACE_VERSIONS:
        failures.append("trace_version")
    if trace.get("generation_id") != expected_generation_id:
        failures.append("generation_id")
    for key in _J1S_REQUIRED_TRACE_KEYS:
        if key not in trace:
            failures.append(key)
    if trace.get("structured_citation_flag") is not True:
        failures.append("structured_citation_flag")
    if trace.get("json_mode_enabled") is not True:
        failures.append("json_mode_enabled")
    structured_output_valid = trace.get("structured_output_valid")
    structured_fallback = trace.get("structured_citation_fallback")
    if structured_output_valid is not True and structured_output_valid is not False:
        failures.append("structured_output_valid")
    if structured_fallback is not True and structured_fallback is not False:
        failures.append("structured_citation_fallback")
    if structured_output_valid is False:
        if structured_fallback is not True:
            failures.append("structured_citation_fallback")
        if trace.get("structured_citation_fallback_mode") not in {
            "fallback_to_j0_postprocessing",
            "safe_failure_no_second_generation",
        }:
            failures.append("structured_citation_fallback_mode")
        reason = trace.get("structured_citation_fallback_reason")
        if not isinstance(reason, str) or not reason.strip():
            failures.append("structured_citation_fallback_reason")
    elif structured_fallback is True:
        failures.append("structured_citation_fallback")
    if trace.get("backend_generate_call_count") != 1:
        failures.append("backend_generate_call_count")
    if trace.get("backend_second_query_called") is not False:
        failures.append("backend_second_query_called")
    for key in (
        "source_registry_sha256",
        "requirement_registry_sha256",
        "provider_raw_response_sha256",
        "parsed_structured_output_sha256",
    ):
        if not isinstance(trace.get(key), str) or not str(trace.get(key)).strip():
            failures.append(key)
    for key in ("source_registry_count", "requirement_registry_count"):
        value = trace.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            failures.append(key)
    return tuple(dict.fromkeys(failures))


def reconcile_j1s_result(
    saved: Mapping[str, object],
    *,
    persisted_trace: Mapping[str, object],
    expected_generation_id: str,
) -> dict[str, object]:
    """Re-evaluate a saved J1S response against its immutable stored Trace."""

    result = dict(saved)
    result["trace"] = dict(persisted_trace)
    result["trace_reconciled_from_persisted_record"] = True
    failures = validate_j1s_trace(
        persisted_trace, expected_generation_id=expected_generation_id
    )
    if failures:
        result["execution_status"] = "trace_contract_failed"
        result["trace_contract_failures"] = list(failures)
    else:
        result["execution_status"] = "completed"
        result.pop("trace_contract_failures", None)
    return result


def load_persisted_trace(candidate_db: Path, request_id: str) -> dict[str, object]:
    """Read one immutable Trace payload using SQLite read-only mode."""

    uri = candidate_db.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        row = connection.execute(
            "SELECT payload FROM retrieval_traces WHERE request_id = ?", (request_id,)
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError(f"persisted Trace is missing for request_id={request_id}")
    try:
        payload = json.loads(row[0])
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"persisted Trace payload is invalid for request_id={request_id}"
        ) from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"persisted Trace payload is not an object for request_id={request_id}")
    return payload


def load_persisted_trace_by_trace_id(candidate_db: Path, trace_id: str) -> dict[str, object]:
    """Read one immutable Trace by the response trace identifier, never by a copy."""

    uri = candidate_db.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        rows = connection.execute(
            "SELECT payload FROM retrieval_traces WHERE trace_id = ?", (trace_id,)
        ).fetchall()
    finally:
        connection.close()
    if len(rows) != 1:
        raise RuntimeError(f"persisted Trace lookup is not unique for trace_id={trace_id}")
    try:
        payload = json.loads(rows[0][0])
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"persisted Trace payload is invalid for trace_id={trace_id}") from error
    if not isinstance(payload, dict) or payload.get("trace_id") != trace_id:
        raise RuntimeError(f"persisted Trace identity is invalid for trace_id={trace_id}")
    return payload


def reconcile_development_from_persisted_traces(
    *,
    saved_path: Path,
    candidate_db: Path,
    output_path: Path,
    expected_generation_id: str,
) -> dict[str, int]:
    """Reconcile saved Development responses from immutable Trace records only."""

    saved_rows = [
        json.loads(line)
        for line in saved_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    reconciled: list[dict[str, object]] = []
    for saved in saved_rows:
        response = saved.get("response")
        if not isinstance(response, dict):
            raise RuntimeError("saved J1S result has no response object")
        trace_id = response.get("trace_id")
        if not isinstance(trace_id, str) or not trace_id:
            raise RuntimeError("saved J1S response has no trace_id")
        if response.get("generation_id") != expected_generation_id:
            raise RuntimeError("saved J1S response has a wrong generation")
        trace = load_persisted_trace_by_trace_id(candidate_db, trace_id)
        reconciled.append(
            reconcile_j1s_result(
                saved,
                persisted_trace=trace,
                expected_generation_id=expected_generation_id,
            )
        )
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in reconciled),
        encoding="utf-8",
    )
    temporary.replace(output_path)
    return {
        "questions": len(reconciled),
        "completed": sum(row["execution_status"] == "completed" for row in reconciled),
        "reconciled_from_persisted_traces": len(reconciled),
    }


def _rate(numerator: int | float, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def _point_suffix(point_id: object) -> str:
    return str(point_id).rsplit("-", 1)[-1].casefold().lstrip("_")


def evaluate_j1s_development(
    rows: Sequence[Mapping[str, object]],
    *,
    expected_chunk_by_evidence: Mapping[tuple[str, str], str],
    expected_generation_id: str,
) -> dict[str, object]:
    """Score saved Development output by immutable expected child identities."""

    point_records: list[dict[str, object]] = []
    statuses: Counter[str] = Counter()
    answerable_count = 0
    false_rejections = 0
    structured_valid = 0
    fallbacks = 0
    wrong_generation = 0
    trace_present = 0
    for row in rows:
        response = row.get("response")
        golden = row.get("golden")
        trace = row.get("trace")
        if not isinstance(response, Mapping) or not isinstance(golden, Mapping):
            raise RuntimeError("J1S result is missing its saved response or Development expectation")
        status = str(response.get("status", "error"))
        statuses[status] += 1
        if golden.get("answerable") is True:
            answerable_count += 1
            false_rejections += status == "insufficient_evidence"
        if isinstance(trace, Mapping):
            trace_present += 1
            structured_valid += trace.get("structured_output_valid") is True
            fallbacks += trace.get("structured_citation_fallback") is True
            wrong_generation += trace.get("generation_id") != expected_generation_id
        else:
            wrong_generation += 1
        if response.get("generation_id") != expected_generation_id:
            wrong_generation += 1
        citations = {
            str(item.get("citation_id")): item
            for item in response.get("citations", ())
            if isinstance(item, Mapping) and item.get("citation_id")
        }
        claims = [item for item in response.get("claims", ()) if isinstance(item, Mapping)]
        question_id = str(row.get("question_id", ""))
        expected_points = golden.get("expected_answer_points", ())
        if not isinstance(expected_points, Sequence):
            raise RuntimeError("J1S Development expectation has invalid answer points")
        for point in expected_points:
            if not isinstance(point, Mapping):
                raise RuntimeError("J1S Development expectation has invalid answer point")
            matching_claims = [
                claim
                for claim in claims
                if _point_suffix(claim.get("claim_id", ""))
                == _point_suffix(point.get("point_id", ""))
            ]
            actual_chunks = {
                str(citation.get("chunk_id"))
                for claim in matching_claims
                for citation_id in claim.get("citation_ids", ())
                if (citation := citations.get(str(citation_id))) is not None
                and citation.get("chunk_id")
            }
            supported_by = point.get("supported_by", ())
            if not isinstance(supported_by, Sequence):
                raise RuntimeError("J1S Development answer point has invalid support ids")
            expected_chunks = {
                expected_chunk_by_evidence[(question_id, str(evidence_id))]
                for evidence_id in supported_by
                if (question_id, str(evidence_id)) in expected_chunk_by_evidence
            }
            supporting_chunks = actual_chunks & expected_chunks
            point_records.append(
                {
                    "question_id": question_id,
                    "emitted": bool(matching_claims),
                    "supporting_citation_present": bool(supporting_chunks),
                    "citation_precision": (
                        len(supporting_chunks) / len(actual_chunks)
                        if actual_chunks
                        else None
                    ),
                    "overcitation": bool(supporting_chunks and actual_chunks - expected_chunks),
                }
            )
    final_points = [record for record in point_records if record["emitted"]]
    substantive_statuses = {"success", "partial_answer"}
    substantive_questions = [
        row
        for row in rows
        if isinstance(row.get("response"), Mapping)
        and row["response"].get("status") in substantive_statuses
    ]
    points_by_question: dict[str, list[dict[str, object]]] = {}
    for point in point_records:
        points_by_question.setdefault(str(point["question_id"]), []).append(point)
    citation_correct_questions = sum(
        bool([point for point in points_by_question.get(str(row.get("question_id")), ()) if point["emitted"]])
        and all(
            point["supporting_citation_present"]
            for point in points_by_question.get(str(row.get("question_id")), ())
            if point["emitted"]
        )
        for row in substantive_questions
    )
    unsupported_questions = sum(
        any(
            point["emitted"] and not point["supporting_citation_present"]
            for point in points_by_question.get(str(row.get("question_id")), ())
        )
        for row in substantive_questions
    )
    return {
        "status_counts": statuses,
        "expected_answer_point_count": len(point_records),
        "emitted_answer_point_count": len(final_points),
        "supporting_citation_recall": _rate(
            sum(point["supporting_citation_present"] for point in final_points),
            len(final_points),
        ),
        "expected_answer_point_coverage": _rate(
            sum(
                point["emitted"] and point["supporting_citation_present"]
                for point in point_records
            ),
            len(point_records),
        ),
        "citation_precision": _rate(
            sum(float(point["citation_precision"] or 0) for point in final_points),
            len(final_points),
        ),
        "overcitation_rate": _rate(
            sum(point["overcitation"] for point in final_points), len(final_points)
        ),
        "false_rejection_rate": _rate(false_rejections, answerable_count),
        "question_level_citation_accuracy": _rate(
            citation_correct_questions, len(substantive_questions)
        ),
        "question_unsupported_answer_rate": _rate(
            unsupported_questions, len(substantive_questions)
        ),
        "structured_json_valid_rate": _rate(structured_valid, len(rows)),
        "structured_citation_fallback_rate": _rate(fallbacks, len(rows)),
        "wrong_generation_count": wrong_generation,
        "trace_completeness": _rate(trace_present, len(rows)),
        "point_records": point_records,
    }


def assert_preflight_allows_development(cases: Sequence[Mapping[str, object]]) -> None:
    """Fail closed unless each of the exactly three J1S-1 cases is proven safe."""

    if len(cases) != 3:
        raise RuntimeError(f"J1S-1 preflight requires exactly 3 cases, got {len(cases)}")
    failures: list[str] = []
    for index, case in enumerate(cases, 1):
        if case.get("http_status") != 200:
            failures.append(f"case {index}: http_status")
        if case.get("backend_generate_call_count") != 1:
            failures.append(f"case {index}: backend_generate_call_count")
        if case.get("backend_second_query_called") is not False:
            failures.append(f"case {index}: backend_second_query_called")
        if case.get("active_pointer_changed") is not False:
            failures.append(f"case {index}: active_pointer_changed")
        for name in _PREFLIGHT_TRUE:
            if case.get(name) is not True:
                failures.append(f"case {index}: {name}")
    if failures:
        raise RuntimeError("J1S-1 preflight failed: " + ", ".join(failures))


def assert_preflight_config_sha(cases: Sequence[Mapping[str, object]]) -> str:
    """Require the same complete feature-flag configuration across preflight."""

    values = [case.get("feature_flag_config_sha256") for case in cases]
    if not values or any(not isinstance(value, str) or not value for value in values):
        raise RuntimeError("J1S preflight configuration SHA is missing")
    if len(set(values)) != 1:
        raise RuntimeError("J1S preflight configuration SHA does not match")
    return values[0]


def assert_j1s_evaluation_order(
    *,
    j1s0_passed: bool,
    j1s1_cases: Sequence[Mapping[str, object]],
    j1s2_passed: bool,
) -> None:
    """Refuse Development unless every required J1S precondition passed."""

    if not j1s0_passed:
        raise RuntimeError("J1S-0 flag-off non-regression has not passed")
    assert_preflight_allows_development(j1s1_cases)
    if not j1s2_passed:
        raise RuntimeError("J1S-2 historical citation subset has not passed")


def _load_env(path: Path, candidate_db: Path) -> dict[str, str]:
    values = {
        key.strip(): value.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
        for key, value in [line.split("=", 1)]
    }
    values["DATABASE_URL"] = f"sqlite+aiosqlite:///{candidate_db}"
    values = j1s_environment(values)
    values["QA_GROUNDING_AUDIT_ENABLED"] = "true"
    os.environ.update(values)
    return values


def _load_development(source: Path = DEVELOPMENT_SOURCE) -> list[dict[str, object]]:
    """Load only the frozen Development records; never filter a mixed split file."""

    rows: list[dict[str, object]] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("split") != "development":
            raise RuntimeError("Development source contains a non-Development row")
        golden = record.get("golden", record)
        if not isinstance(golden, dict) or golden.get("split", "development") != "development":
            raise RuntimeError("Development source contains an invalid Golden record")
        rows.append(golden)
    return rows


def _load_preflight() -> dict[str, object]:
    if not PREFLIGHT.exists():
        raise RuntimeError("J1S preflight evidence is required before Development")
    payload = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    assert_j1s_evaluation_order(
        j1s0_passed=payload.get("j1s0_passed") is True,
        j1s1_cases=payload.get("j1s1_cases", ()),
        j1s2_passed=payload.get("j1s2_passed") is True,
    )
    assert_preflight_config_sha(payload.get("j1s1_cases", ()))
    return payload


async def main() -> int:
    env_file = Path(os.environ.get("PHASE10_STAGING_ENV_FILE", ROOT / ".env.local_staging"))
    candidate_db = Path(
        os.environ.get(
            "PHASE10_CANDIDATE_DB",
            ROOT / "runtime" / "phase10b3c" / "industrial_rag_candidate.db",
        )
    )
    values = _load_env(env_file, candidate_db)
    _load_preflight()
    rows = _load_development()
    if len(rows) != 36:
        raise RuntimeError(f"J1S Development must contain exactly 36 questions, got {len(rows)}")
    source_rows = [
        json.loads(line)
        for line in DEVELOPMENT_SOURCE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    dataset_shas = {str(row.get("dataset_sha256", "")) for row in source_rows}
    if len(dataset_shas) != 1 or not next(iter(dataset_shas)):
        raise RuntimeError("frozen Development records must carry one dataset SHA")
    dataset_sha = next(iter(dataset_shas))
    OUT.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8011", timeout=240) as client:
        results = await Phase10BaselineRunner(
            client=client,
            knowledge_base_id=KB_ID,
            expected_generation_id=GENERATION_ID,
            service_api_key=values["SERVICE_API_KEY"],
            admin_api_key=values["ADMIN_API_KEY"],
            dataset_sha256=dataset_sha,
            output_dir=ROOT / "runtime" / "phase10b3j_j1s" / "development",
            required_trace_keys=(
                "structured_citation_flag",
                "json_mode_enabled",
                "source_registry_count",
                "source_registry_sha256",
                "structured_output_valid",
                "backend_generate_call_count",
                "backend_second_query_called",
            ),
            explicit_generation=True,
            trace_versions=("phase10b3j-runtime-lineage-v2",),
        ).run(rows)
    output = OUT / "development_results.jsonl"
    output.write_text(
        "\n".join(json.dumps({"split": "development", **row}, ensure_ascii=False) for row in results)
        + "\n",
        encoding="utf-8",
    )
    summary = {
        "experiment": "J1S",
        "questions": len(results),
        "completed": sum(row.get("execution_status") == "completed" for row in results),
        "candidate_generation_id": GENERATION_ID,
        "dataset_sha256": dataset_sha,
        "validation_run": False,
        "holdout_run": False,
    }
    (OUT / "development_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["completed"] == 36 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

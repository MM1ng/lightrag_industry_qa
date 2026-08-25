"""Run the Phase 10A baseline through the real ordinary and admin HTTP APIs."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx
from industrial_rag.phase10_evaluation import diagnose_case, evaluate_retrieval
from industrial_rag.services.golden_set_policy import CANONICAL_QUESTION_IDS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRACE_VERSION = "phase10a-retrieval-trace-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class Phase10BaselineRunner:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        knowledge_base_id: str,
        expected_generation_id: str,
        service_api_key: str,
        admin_api_key: str,
        dataset_sha256: str,
        output_dir: Path,
        required_trace_keys: tuple[str, ...] = (),
        explicit_generation: bool = False,
        trace_versions: tuple[str, ...] = (TRACE_VERSION,),
    ) -> None:
        if not service_api_key or not admin_api_key:
            raise ValueError("both role credentials are required")
        if service_api_key == admin_api_key:
            raise ValueError("service and admin credentials must differ")
        self._client = client
        self._kb_id = knowledge_base_id
        self._expected_generation_id = expected_generation_id
        self._service_key = service_api_key
        self._admin_key = admin_api_key
        self._dataset_sha256 = dataset_sha256
        self._output_dir = output_dir
        self._required_trace_keys = required_trace_keys
        self._explicit_generation = explicit_generation
        self._trace_versions = trace_versions

    async def run_case(self, golden: dict[str, Any]) -> dict[str, Any]:
        base = {
            "question_id": golden["question_id"],
            "dataset_sha256": self._dataset_sha256,
            "golden": golden,
            "response": {"status": "error", "citations": []},
            "trace": None,
        }
        try:
            query_path = (
                f"/v1/knowledge-bases/{self._kb_id}/generations/{self._expected_generation_id}/query"
                if self._explicit_generation
                else f"/v1/knowledge-bases/{self._kb_id}/query"
            )
            ordinary = await self._client.post(
                query_path,
                json={"query": golden["question"]},
                headers={"Authorization": f"Bearer {self._admin_key}" if self._explicit_generation else f"Bearer {self._service_key}"},
            )
        except httpx.HTTPError as error:
            return {
                **base,
                "execution_status": "ordinary_query_failed",
                "failure_type": type(error).__name__,
            }
        if ordinary.status_code != 200:
            return {
                **base,
                "execution_status": "ordinary_query_failed",
                "ordinary_http_status": ordinary.status_code,
                "response": _safe_json(ordinary),
            }
        response = _safe_json(ordinary)
        request_id = response.get("request_id")
        if not request_id or response.get("generation_id") != self._expected_generation_id:
            return {
                **base,
                "execution_status": "ordinary_contract_failed",
                "response": response,
            }
        try:
            diagnostic = await self._client.get(
                f"/v1/admin/diagnostics/requests/{request_id}/retrieval-trace",
                headers={"Authorization": f"Bearer {self._admin_key}"},
            )
        except httpx.HTTPError as error:
            return {
                **base,
                "execution_status": "trace_missing",
                "failure_type": type(error).__name__,
                "response": response,
            }
        if diagnostic.status_code != 200:
            return {
                **base,
                "execution_status": "trace_missing",
                "diagnostic_http_status": diagnostic.status_code,
                "diagnostic_error": _safe_json(diagnostic),
                "response": response,
            }
        trace = _safe_json(diagnostic)
        if (
            trace.get("trace_version") not in self._trace_versions
            or trace.get("request_id") != request_id
            or trace.get("generation_id") != self._expected_generation_id
        ):
            return {
                **base,
                "execution_status": "trace_contract_failed",
                "response": response,
                "trace": trace,
            }
        return {
            **base,
            "execution_status": "completed",
            "response": response,
            "trace": trace,
        }

    async def run(self, golden_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results_path = self._output_dir / "baseline_results.jsonl"
        existing = {
            row["question_id"]: row
            for row in _load_jsonl(results_path)
            if row.get("dataset_sha256") == self._dataset_sha256
            and row.get("execution_status") == "completed"
            and row.get("trace") is not None
            and all(key in row["trace"] for key in self._required_trace_keys)
        }
        results: list[dict[str, Any]] = []
        for golden in golden_rows:
            result = existing.get(golden["question_id"])
            if result is None:
                result = await self.run_case(golden)
            results.append(result)
            _write_jsonl_atomic(results_path, results)
        self._write_aggregates(results)
        return results

    def _write_aggregates(self, results: list[dict[str, Any]]) -> None:
        metrics = evaluate_retrieval(results)
        diagnoses = [
            {**diagnose_case(case), "execution_status": case["execution_status"]}
            for case in results
            if case["question_id"] in CANONICAL_QUESTION_IDS
        ]
        trace_count = sum(
            case.get("execution_status") == "completed" and case.get("trace") is not None
            for case in results
        )
        summary = {
            "dataset_sha256": self._dataset_sha256,
            "record_count": len(results),
            "completed_count": sum(
                case.get("execution_status") == "completed" for case in results
            ),
            "trace_completeness": {
                "numerator": trace_count,
                "denominator": len(results),
                "value": None if not results else trace_count / len(results),
            },
            "cache_disabled_declared": os.environ.get(
                "ENABLE_LLM_CACHE", ""
            ).strip().lower()
            == "false",
            "missing_or_failed_question_ids": [
                case["question_id"]
                for case in results
                if case.get("execution_status") != "completed"
            ],
        }
        _write_jsonl_atomic(self._output_dir / "baseline_diagnosis.jsonl", diagnoses)
        _write_json_atomic(self._output_dir / "retrieval_metrics.json", metrics)
        _write_json_atomic(self._output_dir / "baseline_summary.json", summary)


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {"code": "NON_JSON_RESPONSE"}
    return payload if isinstance(payload, dict) else {"code": "INVALID_JSON_RESPONSE"}


def verify(output_dir: Path, *, dataset_sha256: str) -> int:
    results = _load_jsonl(output_dir / "baseline_results.jsonl")
    diagnoses = _load_jsonl(output_dir / "baseline_diagnosis.jsonl")
    summary_path = output_dir / "baseline_summary.json"
    if not summary_path.exists():
        print("verification failed: baseline_summary.json missing")
        return 1
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    valid = (
        len(results) == 64
        and len(diagnoses) == 20
        and all(row.get("dataset_sha256") == dataset_sha256 for row in results)
        and summary.get("trace_completeness", {}).get("value") == 1.0
        and summary.get("cache_disabled_declared") is True
    )
    print(
        f"records={len(results)} fixed20={len(diagnoses)} "
        f"trace_completeness={summary.get('trace_completeness', {}).get('value')}"
    )
    return 0 if valid else 1


async def _run(args: argparse.Namespace) -> int:
    golden_path = Path(args.golden).resolve()
    dataset_sha256 = _sha256(golden_path)
    output_dir = Path(args.output_dir).resolve()
    if args.verify_only:
        return verify(output_dir, dataset_sha256=dataset_sha256)
    if os.environ.get("ENABLE_LLM_CACHE", "").strip().lower() != "false":
        raise ValueError("ENABLE_LLM_CACHE=false is required for the Phase 10A baseline")
    service_key = os.environ.get("SERVICE_API_KEY", "").strip()
    admin_key = os.environ.get("ADMIN_API_KEY", "").strip()
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"), timeout=args.timeout
    ) as client:
        runner = Phase10BaselineRunner(
            client=client,
            knowledge_base_id=args.kb_id,
            expected_generation_id=args.expected_generation_id,
            service_api_key=service_key,
            admin_api_key=admin_key,
            dataset_sha256=dataset_sha256,
            output_dir=output_dir,
            explicit_generation=args.explicit_generation,
        )
        results = await runner.run(_load_jsonl(golden_path))
    completed = sum(row.get("execution_status") == "completed" for row in results)
    print(f"completed={completed}/{len(results)}")
    return 0 if completed == len(results) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8111")
    parser.add_argument("--kb-id", required=False)
    parser.add_argument("--expected-generation-id", required=False)
    parser.add_argument(
        "--golden",
        default=str(PROJECT_ROOT / "evaluation/phase10/expanded_golden_set.jsonl"),
    )
    parser.add_argument(
        "--output-dir", default=str(PROJECT_ROOT / "evaluation/phase10")
    )
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--explicit-generation", action="store_true")
    args = parser.parse_args()
    if not args.verify_only and (not args.kb_id or not args.expected_generation_id):
        parser.error("--kb-id and --expected-generation-id are required")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())

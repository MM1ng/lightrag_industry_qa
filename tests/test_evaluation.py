from __future__ import annotations

import json
from pathlib import Path

import pytest
from industrial_rag.citation_formatter import Citation
from industrial_rag.evaluation import load_golden_cases
from industrial_rag.lightrag_service import INSUFFICIENT_EVIDENCE_MESSAGE, QueryResult


def test_evaluate_cases_reports_ranked_retrieval_and_safe_refusal() -> None:
    """A relevant citation at rank two must affect recall and MRR predictably."""
    from industrial_rag.evaluation import GoldenCase, evaluate_cases

    expected = Citation("pump.pdf", 7, "pump-p7-c1")
    cases = (
        GoldenCase("answer", "轴承温度高怎么办？", True, (expected,)),
        GoldenCase("refuse", "火星维护周期？", False, ()),
    )

    def query(question: str) -> tuple[QueryResult, float]:
        if question == "火星维护周期？":
            return QueryResult(INSUFFICIENT_EVIDENCE_MESSAGE, (), "mix"), 0.2
        return (
            QueryResult("检查润滑。", (Citation("pump.pdf", 2, "pump-p2-c1"), expected), "mix"),
            0.1,
        )

    report = evaluate_cases(cases, query)

    assert report.retrieval_recall_at_1 == 0.0
    assert report.retrieval_recall_at_3 == 1.0
    assert report.retrieval_recall_at_5 == 1.0
    assert report.mean_reciprocal_rank == 0.5
    assert report.citation_presence_rate == 1.0
    assert report.citation_traceability_rate == 1.0
    assert report.no_evidence_refusal_rate == 1.0
    assert report.success_rate == 1.0
    assert report.latency_p50_ms == 100.0
    assert report.latency_p95_ms == 200.0


def test_evaluate_cases_records_failed_queries_and_invalid_refusals() -> None:
    """Failures and invented evidence must lower their own report metrics."""
    from industrial_rag.evaluation import GoldenCase, evaluate_cases

    expected = Citation("pump.pdf", 7, "pump-p7-c1")
    cases = (
        GoldenCase("missing", "手册问题", True, (expected,)),
        GoldenCase("failed", "服务失败", True, (expected,)),
        GoldenCase("unsafe-refusal", "火星问题", False, ()),
    )

    def query(question: str) -> tuple[QueryResult, float]:
        if question == "服务失败":
            raise RuntimeError("private upstream detail")
        if question == "火星问题":
            return QueryResult(
                "我猜是每周维护。", (Citation("pump.pdf", 2, "pump-p2-c1"),), "mix"
            ), 0.3
        return QueryResult("检查。", (), "mix"), 0.1

    report = evaluate_cases(cases, query)

    assert report.retrieval_recall_at_5 == 0.0
    assert report.citation_presence_rate == 0.0
    assert report.citation_traceability_rate == 0.0
    assert report.no_evidence_refusal_rate == 0.0
    assert report.success_rate == pytest.approx(2 / 3)
    assert report.cases[1].error_type == "RuntimeError"
    assert report.cases[1].latency_ms is None


def test_evaluate_cases_keeps_metrics_attached_to_the_originating_case() -> None:
    """A no-evidence case before an evidence case must not shift metric results."""
    from industrial_rag.evaluation import GoldenCase, evaluate_cases

    expected = Citation("pump.pdf", 7, "pump-p7-c1")
    cases = (
        GoldenCase("refuse", "火星问题", False, ()),
        GoldenCase("answer", "启动前检查什么？", True, (expected,)),
    )

    def query(question: str) -> tuple[QueryResult, float]:
        if question == "火星问题":
            return QueryResult(INSUFFICIENT_EVIDENCE_MESSAGE, (), "mix"), 0.1
        return QueryResult("检查阀门。", (expected,), "mix"), 0.2

    report = evaluate_cases(cases, query)

    assert report.retrieval_recall_at_1 == 1.0
    assert report.no_evidence_refusal_rate == 1.0


def test_evaluate_main_requires_explicit_real_flag() -> None:
    """The evaluator must never call a configured model by accident."""
    from scripts import evaluate

    with pytest.raises(SystemExit) as error:
        evaluate.main(["--golden", "golden.jsonl", "--output", "report.json"])

    assert error.value.code == 2


def test_evaluate_main_writes_report_with_injected_runtime(tmp_path: Path) -> None:
    """The CLI writes the real evaluator report without starting LightRAG in tests."""
    from scripts import evaluate

    golden = tmp_path / "golden.jsonl"
    output = tmp_path / "report.json"
    golden.write_text(
        '{"id":"answer","question":"启动前检查什么？","expects_evidence":true,'
        '"expected_citations":[{"source_file":"pump.pdf","page_number":7,'
        '"chunk_id":"pump-p7-c1"}]}\n',
        encoding="utf-8",
    )
    runtime = _FakeEvaluationRuntime()

    exit_code = evaluate.main(
        ["--real", "--golden", str(golden), "--output", str(output)],
        runtime_factory=lambda: runtime,
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["success_rate"] == 1.0
    assert report["retrieval_recall_at_1"] == 1.0
    assert runtime.closed is True


class _FakeEvaluationRuntime:
    def __init__(self) -> None:
        self.closed = False

    def query(
        self, question: str, *, mode: str, timeout: float = 180.0
    ) -> tuple[QueryResult, float]:
        assert question == "启动前检查什么？"
        assert mode == "mix"
        assert timeout == 180.0
        citation = Citation("pump.pdf", 7, "pump-p7-c1")
        return QueryResult("检查阀门。", (citation,), "mix"), 0.1

    def close(self) -> None:
        self.closed = True


def test_load_golden_cases_preserves_expected_citations(tmp_path: Path) -> None:
    """A loader regression must not discard the verified source chunk."""
    path = tmp_path / "golden.jsonl"
    path.write_text(
        '{"id":"startup","question":"启动前检查什么？","expects_evidence":true,'
        '"expected_citations":[{"source_file":"pump.pdf","page_number":7,'
        '"chunk_id":"pump-p7-c1"}]}\n',
        encoding="utf-8",
    )

    cases = load_golden_cases(path)

    assert cases[0].case_id == "startup"
    assert cases[0].expected_citations[0].chunk_id == "pump-p7-c1"


def test_load_golden_cases_rejects_evidence_case_without_expected_citation(
    tmp_path: Path,
) -> None:
    """An evidence-required case without a verified target cannot be evaluated."""
    path = tmp_path / "golden.jsonl"
    path.write_text(
        '{"id":"bad","question":"问题","expects_evidence":true,"expected_citations":[]}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected_citations"):
        load_golden_cases(path)

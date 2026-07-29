"""Deterministic golden-set contracts for RAG evaluation."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import TypeAlias

from industrial_rag.citation_formatter import Citation
from industrial_rag.lightrag_service import INSUFFICIENT_EVIDENCE_MESSAGE, QueryResult

CitationIdentity: TypeAlias = tuple[str, int, str]
QueryCallable: TypeAlias = Callable[[str], tuple[QueryResult, float]]


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """One manually verified question and its expected retrieval evidence."""

    case_id: str
    question: str
    expects_evidence: bool
    expected_citations: tuple[Citation, ...]

    def __post_init__(self) -> None:
        if not self.case_id.strip() or not self.question.strip():
            raise ValueError("golden case id and question are required")
        if self.expects_evidence != bool(self.expected_citations):
            raise ValueError("expected_citations must match expects_evidence")


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Safe, per-case outcome recorded in an evaluation report."""

    case_id: str
    completed: bool
    citations: tuple[Citation, ...]
    latency_ms: float | None
    error_type: str | None
    first_relevant_rank: int | None = None
    refusal_passed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.case_id,
            "completed": self.completed,
            "citations": [citation.display for citation in self.citations],
            "latency_ms": self.latency_ms,
            "error_type": self.error_type,
            "first_relevant_rank": self.first_relevant_rank,
            "refusal_passed": self.refusal_passed,
        }


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Aggregate deterministic metrics and their safe per-case evidence."""

    cases: tuple[CaseResult, ...]
    retrieval_recall_at_1: float | None
    retrieval_recall_at_3: float | None
    retrieval_recall_at_5: float | None
    mean_reciprocal_rank: float | None
    citation_presence_rate: float | None
    citation_traceability_rate: float | None
    no_evidence_refusal_rate: float | None
    success_rate: float
    latency_p50_ms: float | None
    latency_p95_ms: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "case_count": len(self.cases),
            "retrieval_recall_at_1": self.retrieval_recall_at_1,
            "retrieval_recall_at_3": self.retrieval_recall_at_3,
            "retrieval_recall_at_5": self.retrieval_recall_at_5,
            "mean_reciprocal_rank": self.mean_reciprocal_rank,
            "citation_presence_rate": self.citation_presence_rate,
            "citation_traceability_rate": self.citation_traceability_rate,
            "no_evidence_refusal_rate": self.no_evidence_refusal_rate,
            "success_rate": self.success_rate,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "cases": [case.to_dict() for case in self.cases],
        }


def load_golden_cases(path: Path) -> tuple[GoldenCase, ...]:
    """Load a strict JSONL golden set without accepting ambiguous evidence."""

    if not path.is_file():
        raise FileNotFoundError(f"golden file does not exist: {path}")

    cases: list[GoldenCase] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"line {line_number}: invalid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError(f"line {line_number}: case must be an object")

        case_id = payload.get("id")
        question = payload.get("question")
        expects_evidence = payload.get("expects_evidence")
        raw_citations = payload.get("expected_citations")
        if not isinstance(case_id, str) or not isinstance(question, str):
            raise ValueError(f"line {line_number}: id and question must be strings")
        if not isinstance(expects_evidence, bool) or not isinstance(raw_citations, list):
            raise ValueError(f"line {line_number}: invalid evaluation fields")
        if case_id in seen_ids:
            raise ValueError(f"line {line_number}: duplicate id {case_id}")

        citations = tuple(_parse_citation(item, line_number) for item in raw_citations)
        try:
            case = GoldenCase(case_id, question, expects_evidence, citations)
        except ValueError as error:
            raise ValueError(f"line {line_number}: {error}") from error
        cases.append(case)
        seen_ids.add(case_id)

    if not cases:
        raise ValueError("golden file contains no cases")
    return tuple(cases)


def evaluate_cases(cases: tuple[GoldenCase, ...], query: QueryCallable) -> EvaluationReport:
    """Run deterministic retrieval, refusal, availability and latency checks."""

    results: list[CaseResult] = []
    completed_latencies_ms: list[float] = []
    for case in cases:
        try:
            result, seconds = query(case.question)
        except Exception as error:
            results.append(CaseResult(case.case_id, False, (), None, type(error).__name__))
            continue

        latency_ms = round(seconds * 1000, 3)
        completed_latencies_ms.append(latency_ms)
        expected = {_identity(citation) for citation in case.expected_citations}
        first_relevant_rank = next(
            (
                index
                for index, citation in enumerate(result.citations, start=1)
                if _identity(citation) in expected
            ),
            None,
        )
        refusal_passed = (
            not case.expects_evidence
            and result.answer == INSUFFICIENT_EVIDENCE_MESSAGE
            and not result.citations
        )
        results.append(
            CaseResult(
                case.case_id,
                True,
                result.citations,
                latency_ms,
                None,
                first_relevant_rank,
                refusal_passed,
            )
        )

    return _build_report(cases, tuple(results), completed_latencies_ms)


def _build_report(
    cases: tuple[GoldenCase, ...],
    results: tuple[CaseResult, ...],
    completed_latencies_ms: list[float],
) -> EvaluationReport:
    paired_cases = tuple(zip(cases, results, strict=True))
    evidence = tuple((case, result) for case, result in paired_cases if case.expects_evidence)
    no_evidence = tuple(
        result for case, result in paired_cases if not case.expects_evidence
    )

    return EvaluationReport(
        cases=results,
        retrieval_recall_at_1=_recall_at_k(evidence, 1),
        retrieval_recall_at_3=_recall_at_k(evidence, 3),
        retrieval_recall_at_5=_recall_at_k(evidence, 5),
        mean_reciprocal_rank=_mean_reciprocal_rank(evidence),
        citation_presence_rate=_rate(sum(bool(result.citations) for _, result in evidence), len(evidence)),
        citation_traceability_rate=_rate(
            sum(result.first_relevant_rank is not None for _, result in evidence), len(evidence)
        ),
        no_evidence_refusal_rate=_rate(
            sum(result.refusal_passed for result in no_evidence), len(no_evidence)
        ),
        success_rate=_rate(sum(result.completed for result in results), len(results)) or 0.0,
        latency_p50_ms=_nearest_rank(completed_latencies_ms, 0.5),
        latency_p95_ms=_nearest_rank(completed_latencies_ms, 0.95),
    )


def _recall_at_k(evidence: tuple[tuple[GoldenCase, CaseResult], ...], k: int) -> float | None:
    expected_count = sum(len(case.expected_citations) for case, _ in evidence)
    matched_count = sum(
        sum(
            _identity(expected) in {_identity(citation) for citation in result.citations[:k]}
            for expected in case.expected_citations
        )
        for case, result in evidence
    )
    return _rate(matched_count, expected_count)


def _mean_reciprocal_rank(
    evidence: tuple[tuple[GoldenCase, CaseResult], ...],
) -> float | None:
    return _rate(
        sum(0.0 if result.first_relevant_rank is None else 1 / result.first_relevant_rank for _, result in evidence),
        len(evidence),
    )


def _rate(numerator: float | int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[ceil(percentile * len(ordered)) - 1]


def _identity(citation: Citation) -> CitationIdentity:
    return (citation.source_file, citation.page_number, citation.chunk_id)


def _parse_citation(value: object, line_number: int) -> Citation:
    if not isinstance(value, dict):
        raise ValueError(f"line {line_number}: expected_citations must contain objects")
    source_file = value.get("source_file")
    page_number = value.get("page_number")
    chunk_id = value.get("chunk_id")
    if (
        not isinstance(source_file, str)
        or not isinstance(page_number, int)
        or isinstance(page_number, bool)
        or not isinstance(chunk_id, str)
    ):
        raise ValueError(f"line {line_number}: invalid expected_citations")
    try:
        return Citation(source_file, page_number, chunk_id)
    except ValueError as error:
        raise ValueError(f"line {line_number}: invalid expected_citations: {error}") from error

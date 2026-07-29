"""Deterministic golden-set contracts for RAG evaluation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from industrial_rag.citation_formatter import Citation


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

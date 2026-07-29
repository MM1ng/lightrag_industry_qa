from __future__ import annotations

from pathlib import Path

import pytest
from industrial_rag.evaluation import load_golden_cases


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

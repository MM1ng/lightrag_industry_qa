from __future__ import annotations

from pathlib import Path

import pytest

from industrial_energy_agent.rag.document_parser import AutoDocumentParser
from industrial_energy_agent.rag.parsers.mineru_parser import MinerUParser
from industrial_energy_agent.rag.parsers.pymupdf_parser import PyMuPDFParser

EXPECTED_PAGES = {
    "2196-ANSI-Manual-Chinese.pdf": 55,
    "t1739cn.pdf": 62,
}


def test_real_manuals_preserve_pages_hashes_and_parser_provenance() -> None:
    project_root = Path(__file__).resolve().parents[3]
    manual_root = project_root / "data" / "manuals"
    paths = sorted(manual_root.glob("*.pdf"))
    if not paths:
        pytest.skip("real manuals are not present in this checkout")
    assert {path.name for path in paths} == set(EXPECTED_PAGES)
    parser = AutoDocumentParser(MinerUParser(), PyMuPDFParser())

    for path in paths:
        result = parser.parse(path)

        assert result.page_count == EXPECTED_PAGES[path.name]
        assert len(result.pages) == result.page_count
        assert len(result.source_sha256) == 64
        assert result.parser_name == "pymupdf"
        assert result.warnings[0].code == "MINERU_DOCUMENT_FALLBACK"
        assert result.chunks
        assert all(chunk.source_file == path.name for chunk in result.chunks)
        assert all(chunk.source_sha256 == result.source_sha256 for chunk in result.chunks)
        assert all(chunk.page_start == chunk.page_end for chunk in result.chunks)
        assert all(1 <= chunk.page_number <= result.page_count for chunk in result.chunks)

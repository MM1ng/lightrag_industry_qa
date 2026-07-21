from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf
import pytest
from scripts import parse_manuals


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_parse_directory_writes_traceable_outputs_without_changing_source(
    tmp_path: Path,
) -> None:
    manual_dir = tmp_path / "manuals"
    output_dir = tmp_path / "processed"
    manual_dir.mkdir()
    pdf_path = manual_dir / "pump-manual.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Pump maintenance manual")
    document.save(pdf_path)
    document.close()
    before_hash = _sha256(pdf_path)

    manifest = parse_manuals.parse_manual_directory(
        manual_dir=manual_dir,
        output_dir=output_dir,
        parser_name="pymupdf",
    )

    assert _sha256(pdf_path) == before_hash
    assert manifest["document_count"] == 1
    entry = manifest["documents"][0]
    assert entry["source_sha256"] == before_hash
    chunks_path = output_dir / entry["chunks_file"]
    report_path = output_dir / entry["report_file"]
    assert chunks_path.is_file()
    assert report_path.is_file()
    chunks = [json.loads(line) for line in chunks_path.read_text(encoding="utf-8").splitlines()]
    assert chunks[0]["page_number"] == 1
    assert chunks[0]["source_sha256"] == before_hash
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["page_count"] == 1
    checked_in_manifest = json.loads(
        (output_dir / "manuals_manifest.json").read_text(encoding="utf-8")
    )
    assert checked_in_manifest == manifest


def test_cli_defaults_to_auto_parser() -> None:
    args = parse_manuals.parse_args([])

    assert args.parser == "auto"


def test_parse_directory_rejects_output_inside_manual_source(tmp_path: Path) -> None:
    manual_dir = tmp_path / "manuals"
    manual_dir.mkdir()

    with pytest.raises(RuntimeError, match="outside the manual source"):
        parse_manuals.parse_manual_directory(
            manual_dir=manual_dir,
            output_dir=manual_dir / "generated",
            parser_name="pymupdf",
        )

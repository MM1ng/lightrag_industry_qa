"""Parse registered PDF manuals into traceable page-bound chunks."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from industrial_energy_agent.rag.document_parser import (
    AutoDocumentParser,
    DocumentParser,
    ParsedDocument,
)
from industrial_energy_agent.rag.parsers.mineru_parser import MinerUParser
from industrial_energy_agent.rag.parsers.pymupdf_parser import PyMuPDFParser

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANUAL_DIR = PROJECT_ROOT / "data" / "manuals"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "processed" / "manuals"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parser", choices=("auto", "pymupdf", "mineru"), default="auto")
    parser.add_argument("--manual-dir", type=Path, default=DEFAULT_MANUAL_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parser(name: str) -> DocumentParser:
    mineru = MinerUParser()
    pymupdf = PyMuPDFParser()
    if name == "auto":
        return AutoDocumentParser(mineru, pymupdf)
    if name == "mineru":
        return mineru
    return pymupdf


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _write_document_outputs(output_dir: Path, result: ParsedDocument) -> dict[str, Any]:
    chunks_file = Path(result.document_id) / "chunks.jsonl"
    report_file = Path(result.document_id) / "parse_report.json"
    chunk_lines = "".join(
        f"{json.dumps(chunk.model_dump(mode='json'), ensure_ascii=False, sort_keys=True)}\n"
        for chunk in result.chunks
    )
    _atomic_write_text(output_dir / chunks_file, chunk_lines)
    report = result.model_dump(mode="json", exclude={"chunks"})
    report["chunk_count"] = len(result.chunks)
    _atomic_write_text(
        output_dir / report_file,
        f"{json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)}\n",
    )
    return {
        "source_file": result.source_file,
        "document_id": result.document_id,
        "document_title": result.document_title,
        "source_sha256": result.source_sha256,
        "parser_name": result.parser_name,
        "parser_version": result.parser_version,
        "page_count": result.page_count,
        "chunk_count": len(result.chunks),
        "chunks_file": chunks_file.as_posix(),
        "report_file": report_file.as_posix(),
    }


def parse_manual_directory(
    *,
    manual_dir: Path,
    output_dir: Path,
    parser_name: str = "auto",
) -> dict[str, Any]:
    manual_root = manual_dir.resolve()
    output_root = output_dir.resolve()
    if not manual_root.is_dir():
        raise RuntimeError("Manual directory does not exist")
    if output_root == manual_root or manual_root in output_root.parents:
        raise RuntimeError("Output directory must remain outside the manual source directory")
    paths = sorted(path for path in manual_root.rglob("*.pdf") if path.is_file())
    if not paths:
        raise RuntimeError("No PDF manuals were found")
    selected_parser = _parser(parser_name)
    entries: list[dict[str, Any]] = []
    for path in paths:
        before_hash = _sha256_file(path)
        result = selected_parser.parse(path)
        after_hash = _sha256_file(path)
        if before_hash != after_hash or result.source_sha256 != before_hash:
            raise RuntimeError("Source PDF changed during parsing")
        entries.append(_write_document_outputs(output_root, result))
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "requested_parser": parser_name,
        "document_count": len(entries),
        "documents": entries,
    }
    _atomic_write_text(
        output_root / "manuals_manifest.json",
        f"{json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)}\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = parse_manual_directory(
        manual_dir=args.manual_dir,
        output_dir=args.output_dir,
        parser_name=args.parser,
    )
    for document in manifest["documents"]:
        print(
            f"PASS manual={document['source_file']} pages={document['page_count']} "
            f"chunks={document['chunk_count']} parser={document['parser_name']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

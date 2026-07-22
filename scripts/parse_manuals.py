"""Parse exactly the two local pump PDFs into one page-aware JSONL file."""

from __future__ import annotations

import hashlib
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from industrial_rag.document_parser import parse_manuals, scan_pdf_files  # noqa: E402

MANUAL_DIR = PROJECT_ROOT / "data" / "manuals"
OUTPUT_PATH = PROJECT_ROOT / "data" / "processed" / "documents.jsonl"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    pdf_files = scan_pdf_files(MANUAL_DIR)
    if len(pdf_files) != 2:
        raise RuntimeError(f"预期 data/manuals 中有 2 份 PDF，实际找到 {len(pdf_files)} 份")
    original_hashes = {path: _sha256(path) for path in pdf_files}
    chunks = parse_manuals(MANUAL_DIR, OUTPUT_PATH)
    if {path: _sha256(path) for path in pdf_files} != original_hashes:
        raise RuntimeError("源 PDF 在解析期间发生变化")
    chunk_counts = Counter(chunk.source_file for chunk in chunks)
    page_counts = {
        source: max(chunk.page_number for chunk in chunks if chunk.source_file == source)
        for source in chunk_counts
    }
    for source in sorted(chunk_counts, key=str.casefold):
        print(f"PASS PDF={source} pages={page_counts[source]} chunks={chunk_counts[source]}")
    print(f"PASS output={OUTPUT_PATH} records={len(chunks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

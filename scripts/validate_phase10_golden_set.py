"""Validate Phase 10A golden annotations and optionally refresh the hash manifest."""

from __future__ import annotations

import argparse
import json
from collections import Counter

from build_phase10_golden_set import (
    CHILD_PATHS,
    GOLDEN_PATH,
    MANIFEST_PATH,
    PROJECT_ROOT,
    build_manifest,
)


def _load_chunks() -> dict[tuple[str, str], dict]:
    chunks: dict[tuple[str, str], dict] = {}
    for relative_path in CHILD_PATHS:
        for line in (PROJECT_ROOT / relative_path).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            chunks[(row["document_name"], row["chunk_id"])] = row
    return chunks


def validate(rows: list[dict]) -> list[str]:
    errors: list[str] = []
    chunks = _load_chunks()
    if len(rows) != 64:
        errors.append(f"record_count={len(rows)} expected=64")
    if len({row.get("question_id") for row in rows}) != len(rows):
        errors.append("question_id values are not unique")
    if Counter(row.get("split") for row in rows) != {
        "development": 36,
        "validation": 16,
        "holdout": 12,
    }:
        errors.append("split distribution is not 36/16/12")
    for row in rows:
        question_id = str(row.get("question_id"))
        evidence = row.get("expected_evidence")
        points = row.get("expected_answer_points")
        if not isinstance(evidence, list) or not isinstance(points, list):
            errors.append(f"{question_id}: evidence/answer points must be lists")
            continue
        if not row.get("answerable"):
            if evidence or points or not row.get("negative_reason"):
                errors.append(f"{question_id}: invalid negative annotation")
            continue
        if not evidence or not points:
            errors.append(f"{question_id}: answerable question lacks annotation")
            continue
        evidence_ids = {item.get("evidence_id") for item in evidence}
        if len(evidence_ids) != len(evidence):
            errors.append(f"{question_id}: duplicate evidence_id")
        if not any(item.get("role") == "primary" for item in evidence):
            errors.append(f"{question_id}: primary evidence missing")
        for item in evidence:
            key = (item.get("document_name"), item.get("chunk_id"))
            chunk = chunks.get(key)
            if chunk is None:
                errors.append(f"{question_id}: unknown chunk {key}")
                continue
            page = item.get("page_number")
            if not isinstance(page, int) or not chunk["page_start"] <= page <= chunk["page_end"]:
                errors.append(f"{question_id}: page outside chunk range")
            if item.get("evidence_text") not in chunk["content"]:
                errors.append(f"{question_id}: evidence excerpt is not verbatim")
            expected_grade = 2 if item.get("role") == "primary" else 1
            if item.get("relevance_grade") != expected_grade:
                errors.append(f"{question_id}: invalid role/grade pairing")
        for point in points:
            supported_by = point.get("supported_by")
            if not supported_by or not set(supported_by) <= evidence_ids:
                errors.append(f"{question_id}: answer point has invalid support")
        if row.get("question_type") in {"cross_page", "multi_evidence"} and len(
            {item.get("chunk_id") for item in evidence}
        ) < 2:
            errors.append(f"{question_id}: multi-evidence type has fewer than two chunks")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args()
    rows = [
        json.loads(line)
        for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    errors = validate(rows)
    if errors:
        for error in errors:
            print(f"ERROR {error}")
        print(f"records={len(rows)} invalid={len(errors)}")
        return 1
    if args.write_manifest:
        MANIFEST_PATH.write_text(
            json.dumps(build_manifest(rows), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(f"records={len(rows)} invalid=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Audit duplicate child chunk IDs without mutating source artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def _content_hash(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for source in args.source:
        for order, line in enumerate(source.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            item = json.loads(line)
            groups[str(item.get("chunk_id"))].append(
                {
                    "source_file": str(source),
                    "chunk_id": item.get("chunk_id"),
                    "document_id": item.get("document_id"),
                    "document_name": item.get("document_name"),
                    "page_start": item.get("page_start"),
                    "page_end": item.get("page_end"),
                    "chunk_order": order,
                    "section_path": item.get("section_path", []),
                    "content_sha256": _content_hash(item.get("content")),
                }
            )
    duplicate_groups = []
    for chunk_id, occurrences in sorted(groups.items()):
        if len(occurrences) < 2:
            continue
        content_hashes = {str(item["content_sha256"]) for item in occurrences}
        documents = {str(item["document_id"]) for item in occurrences}
        positions = {(item["page_start"], item["page_end"], item["chunk_order"]) for item in occurrences}
        if len(documents) > 1:
            category = "cross_document_collision"
        elif len(content_hashes) > 1:
            category = "same_id_different_content"
        elif len(positions) > 1:
            category = "same_content_different_position"
        else:
            category = "exact_duplicate_record"
        duplicate_groups.append(
            {
                "chunk_id": chunk_id,
                "occurrence_count": len(occurrences),
                "document_ids": sorted(documents),
                "document_names": sorted({str(item["document_name"]) for item in occurrences}),
                "content_sha256": sorted(content_hashes),
                "content_identical": len(content_hashes) == 1,
                "metadata_identical": len({json.dumps(item, sort_keys=True, ensure_ascii=False) for item in occurrences}) == 1,
                "cross_document": len(documents) > 1,
                "cross_page": len({(item["page_start"], item["page_end"]) for item in occurrences}) > 1,
                "category": category,
                "likely_root_cause": "parser_duplicate_emission" if category == "exact_duplicate_record" else "chunk_identity_missing_position_or_content",
                "occurrences": occurrences,
            }
        )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "duplicate_chunk_groups.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in duplicate_groups) + "\n",
        encoding="utf-8",
    )
    summary = {
        "source_files": [str(path) for path in args.source],
        "record_count": sum(len(value) for value in groups.values()),
        "unique_chunk_id_count": len(groups),
        "duplicate_group_count": len(duplicate_groups),
        "duplicate_instance_count": sum(item["occurrence_count"] - 1 for item in duplicate_groups),
        "categories": {
            category: sum(item["category"] == category for item in duplicate_groups)
            for category in sorted({item["category"] for item in duplicate_groups})
        },
        "same_id_different_content_count": sum(
            item["category"] == "same_id_different_content" for item in duplicate_groups
        ),
        "cross_document_collision_count": sum(
            item["category"] == "cross_document_collision" for item in duplicate_groups
        ),
        "source_mutated": False,
    }
    (args.output / "duplicate_chunk_audit.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

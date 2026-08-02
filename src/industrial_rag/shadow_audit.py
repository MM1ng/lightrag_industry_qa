"""Non-blocking citation shadow audit (Phase 6).

Observes returned citations after an answer is produced. It never modifies the
answer, never triggers regeneration/refusal, never calls the LLM and never
reads gold answers in production. Failures only produce a warning record.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class CitationShadowAudit:
    """Deterministic structural audit of one answer's citations."""

    request_id: str
    question_id: str | None
    kb_id: str | None
    generation: str | None
    citations: tuple[dict[str, Any], ...] = ()
    context_chunk_ids: tuple[str, ...] = ()
    retrieved_chunk_ids: tuple[str, ...] = ()
    context_registry: tuple[tuple[str, str, int], ...] = ()
    timestamp: str | None = None

    @property
    def record(self) -> dict[str, Any]:
        emitted = len(self.citations)
        invalid_chunk = 0
        invalid_page = 0
        invalid_document = 0
        duplicate_citation = 0
        seen: set[tuple[str, int]] = set()
        for citation in self.citations:
            chunk_id = str(citation.get("chunk_id") or "")
            page = citation.get("page")
            document = str(citation.get("document_name") or citation.get("source_file") or "")
            if not chunk_id or (
                self.context_chunk_ids and chunk_id not in self.context_chunk_ids
            ):
                invalid_chunk += 1
            registry_entry = next(
                (entry for entry in self.context_registry if entry[0] == chunk_id),
                None,
            )
            if registry_entry is not None:
                expected_document, expected_page = registry_entry[1], registry_entry[2]
                if document != expected_document:
                    invalid_document += 1
                if page != expected_page:
                    invalid_page += 1
            if not isinstance(page, int) or page < 1:
                invalid_page += 1
            if not document:
                invalid_document += 1
            identity = (chunk_id, page if isinstance(page, int) else -1)
            if identity in seen:
                duplicate_citation += 1
            seen.add(identity)
        traceable = (
            emitted > 0
            and invalid_chunk == 0
            and invalid_page == 0
            and invalid_document == 0
            and duplicate_citation == 0
        )
        structural_valid = (
            invalid_chunk == 0
            and invalid_page == 0
            and invalid_document == 0
            and duplicate_citation == 0
        )
        return {
            "request_id": self.request_id,
            "question_id": self.question_id,
            "kb_id": self.kb_id,
            "generation": self.generation,
            "structural_valid": structural_valid,
            "invalid_chunk_count": invalid_chunk,
            "invalid_page_count": invalid_page,
            "invalid_document_count": invalid_document,
            "duplicate_citation_count": duplicate_citation,
            "emitted_citation_count": emitted,
            "answer_without_citation": int(emitted == 0),
            "citation_traceability": int(traceable),
            "cross_kb_reference": 0,
            "empty_citation_count": sum(
                1 for citation in self.citations if not citation
            ),
            "audit_status": (
                "ok" if structural_valid and emitted > 0 else "warning"
            ),
            "audit_mode": "shadow_non_blocking",
            "llm_called": False,
            "gold_used": False,
            "timestamp": self.timestamp or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }

    def as_json(self) -> str:
        return json.dumps(self.record, ensure_ascii=False, sort_keys=True)


class ShadowAuditRecorder:
    """Writes audit records to a JSONL sink (best effort, never blocks)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path

    def record(self, audit: CitationShadowAudit) -> dict[str, Any]:
        payload = audit.record
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            except OSError:
                pass  # shadow audit must never break the main path
        return payload

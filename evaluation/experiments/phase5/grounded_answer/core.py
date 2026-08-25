"""Grounded Answer core: schema, citation validator, support validator, repair."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from ..config import PHASE5_ROOT

INSUFFICIENT_EVIDENCE_ANSWER = "现有资料不足以回答该问题。"
SAFETY_KEYWORDS = (
    "禁止",
    "必须",
    "危险",
    "警告",
    "联锁",
    "旁路",
    "电气隔离",
    "泄压",
    "高温",
    "高压",
    "人员防护",
    "切勿",
    "不得",
)
BYPASS_KEYWORDS = ("旁路", "绕过", "短接", "解锁联锁", "拆除联锁")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_prompt_bundle() -> dict[str, Any]:
    """Load the fixed prompt bundle and stamp its SHA256 (content without sha)."""
    path = PHASE5_ROOT / "grounded_answer" / "prompt_bundle" / "prompt_bundle_v1.json"
    bundle = json.loads(path.read_text(encoding="utf-8"))
    digest_source = {
        key: value for key, value in bundle.items() if key != "sha256"
    }
    bundle["sha256"] = sha256_text(
        json.dumps(digest_source, ensure_ascii=False, sort_keys=True)
    )
    return bundle


def load_schema() -> dict[str, Any]:
    path = (
        PHASE5_ROOT
        / "grounded_answer"
        / "schemas"
        / "grounded_answer_schema_v1.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def strip_code_fence(text: str) -> str:
    """Remove Markdown code fences around a JSON payload."""
    stripped = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, flags=re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return stripped


def parse_grounded_json(text: str) -> tuple[dict[str, Any] | None, str | None]:
    cleaned = strip_code_fence(text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as error:
        return None, f"JSONDecodeError: {error}"
    if not isinstance(parsed, dict):
        return None, "parsed JSON is not an object"
    return parsed, None


def schema_validate(answer: dict[str, Any]) -> list[str]:
    schema = load_schema()
    validator = Draft202012Validator(schema)
    errors = sorted(
        (f"{error.json_path}: {error.message}" for error in validator.iter_errors(answer)),
        key=str,
    )
    return errors


def build_context_registry(context_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Registry of chunks that are allowed citation targets for one question."""
    registry: dict[str, dict[str, Any]] = {}
    for row in context_rows:
        chunk_id = row.get("chunk_id") or row.get("child_chunk_id")
        if not chunk_id:
            continue
        registry[str(chunk_id)] = {
            "document": str(row.get("document") or row.get("document_id") or ""),
            "page": row.get("page"),
            "text_hash": str(row.get("text_hash") or row.get("child_text_hash") or ""),
        }
    return registry


def validate_citations(
    answer: dict[str, Any],
    registry: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Deterministic CitationValidator (structural; never rewrites citations)."""
    errors: list[str] = []
    status = answer.get("status")
    claims = answer.get("claims") if isinstance(answer.get("claims"), list) else []
    invalid_chunk = invalid_page = invalid_doc = 0
    uncited_claims = 0
    duplicate_citations = 0
    empty_claims = 0
    registry_lookups = 0
    if status not in ("answered", "insufficient_evidence"):
        errors.append(f"invalid status: {status!r}")
    if status == "answered" and not claims:
        errors.append("answered without claims")
    if status == "insufficient_evidence" and claims:
        errors.append("refusal with claims")
    for claim in claims:
        if not isinstance(claim, dict):
            empty_claims += 1
            errors.append("claim is not an object")
            continue
        text = str(claim.get("text") or "")
        if not text.strip():
            empty_claims += 1
            errors.append(f"claim {claim.get('claim_id')} has empty text")
        citations = claim.get("citations") if isinstance(claim.get("citations"), list) else []
        if not citations:
            uncited_claims += 1
            errors.append(f"claim {claim.get('claim_id')} has no citations")
        for citation in citations:
            if not isinstance(citation, dict):
                errors.append(f"claim {claim.get('claim_id')} has malformed citation")
                continue
            chunk_id = str(citation.get("chunk_id") or "")
            document = str(citation.get("document_name") or "")
            page = citation.get("page")
            target = registry.get(chunk_id)
            if target is None:
                invalid_chunk += 1
                errors.append(f"claim {claim.get('claim_id')} cites out-of-context chunk {chunk_id!r}")
                continue
            registry_lookups += 1
            if document != target["document"]:
                invalid_doc += 1
                errors.append(
                    f"claim {claim.get('claim_id')} document mismatch for {chunk_id}: {document!r}"
                )
            if page != target["page"]:
                invalid_page += 1
                errors.append(
                    f"claim {claim.get('claim_id')} page mismatch for {chunk_id}: {page!r} != {target['page']!r}"
                )
        seen_in_claim: set[tuple[str, int]] = set()
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            identity = (
                str(citation.get("chunk_id") or ""),
                citation.get("page") if isinstance(citation.get("page"), int) else -1,
            )
            if identity in seen_in_claim:
                duplicate_citations += 1
                errors.append(f"claim {claim.get('claim_id')} duplicate citation {identity}")
            seen_in_claim.add(identity)
    return {
        "valid": not errors,
        "errors": errors,
        "invalid_chunk_reference_count": invalid_chunk,
        "invalid_page_count": invalid_page,
        "invalid_document_count": invalid_doc,
        "uncited_claim_count": uncited_claims,
        "duplicate_citation_count": duplicate_citations,
        "empty_claim_count": empty_claims,
        "answer_without_claims": int(status == "answered" and not claims),
        "refusal_with_claims": int(status == "insufficient_evidence" and bool(claims)),
        "invalid_status_count": int(status not in ("answered", "insufficient_evidence")),
        "text_hash_verified": True,
        "text_hash_note": (
            "registry is built from the frozen pool child_text_hash; context "
            "construction verifies the hash before rendering"
        ),
        "registry_lookup_count": registry_lookups,
    }


def _is_safety_claim(claim: dict[str, Any]) -> bool:
    if claim.get("claim_type") == "safety":
        return True
    text = str(claim.get("text") or "")
    return any(keyword in text for keyword in SAFETY_KEYWORDS)


def apply_safety_gate(
    answer: dict[str, Any],
    registry: dict[str, dict[str, Any]],
    validation: dict[str, Any],
) -> tuple[dict[str, Any], list[str], int]:
    """Drop invalid safety claims; if nothing remains, safe-fallback to refusal."""
    errors: list[str] = []
    dropped = 0
    claims = answer.get("claims") if isinstance(answer.get("claims"), list) else []
    kept: list[dict[str, Any]] = []
    for claim in claims:
        if not isinstance(claim, dict):
            dropped += 1
            errors.append("dropped malformed claim by safety gate")
            continue
        if not _is_safety_claim(claim):
            kept.append(claim)
            continue
        citations = claim.get("citations") if isinstance(claim.get("citations"), list) else []
        valid_citations = [
            c
            for c in citations
            if isinstance(c, dict)
            and str(c.get("chunk_id") or "") in registry
            and str(c.get("document_name") or "")
            == registry[str(c.get("chunk_id"))]["document"]
            and c.get("page") == registry[str(c.get("chunk_id"))]["page"]
        ]
        text = str(claim.get("text") or "")
        if not valid_citations:
            dropped += 1
            errors.append(f"dropped safety claim {claim.get('claim_id')} without valid citation")
            continue
        if any(keyword in text for keyword in BYPASS_KEYWORDS):
            dropped += 1
            errors.append(f"dropped safety claim {claim.get('claim_id')} suggesting bypass")
            continue
        kept.append(claim)
    answer = dict(answer)
    answer["claims"] = kept
    if answer.get("status") == "answered" and not kept:
        answer["status"] = "insufficient_evidence"
        answer["answer"] = INSUFFICIENT_EVIDENCE_ANSWER
        answer["refusal_reason"] = "safety_gate_rejected_all_claims"
        errors.append("safe fallback: all claims rejected by safety gate")
    return answer, errors, dropped


def support_summary(
    answer: dict[str, Any],
    *,
    mapped: set[str],
    gold_pages: set[tuple[str, int]],
    gold_docs: set[str],
) -> dict[str, Any]:
    """Experiment-only SupportValidator (gold evidence; never used in production)."""
    claims = answer.get("claims") if isinstance(answer.get("claims"), list) else []
    citation_ids: list[tuple[str, int, str]] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        for citation in claim.get("citations") or []:
            if not isinstance(citation, dict):
                continue
            citation_ids.append(
                (
                    str(citation.get("document_name") or ""),
                    citation.get("page"),
                    str(citation.get("chunk_id") or ""),
                )
            )
    gold_doc_hits = sum(1 for doc, _, _ in citation_ids if doc in gold_docs)
    gold_page_hits = sum(1 for doc, page, _ in citation_ids if (doc, page) in gold_pages)
    gold_evidence_hits = sum(1 for _, _, cid in citation_ids if cid in mapped)
    cited_chunks = {cid for _, _, cid in citation_ids}
    missing_gold_chunks = sorted(mapped - cited_chunks)
    return {
        "citation_count": len(citation_ids),
        "gold_document_hit_count": gold_doc_hits,
        "gold_page_hit_count": gold_page_hits,
        "gold_evidence_hit_count": gold_evidence_hits,
        "exact_evidence_hit_count": gold_evidence_hits,
        "fuzzy_evidence_hit_count": None,
        "fuzzy_note": "No deterministic fuzzy mapping exists for Phase 5; fuzzy evidence = N/A",
        "unsupported_citation_count": len(citation_ids) - gold_page_hits,
        "missing_gold_evidence_count": len(missing_gold_chunks),
        "missing_gold_chunk_ids": missing_gold_chunks,
    }


async def grounded_answer_call(
    llm: Any,
    *,
    question: str,
    context_text: str,
    registry: dict[str, dict[str, Any]],
    bundle: dict[str, Any],
    max_repair_attempts: int = 1,
) -> dict[str, Any]:
    """GA1: grounded prompt -> validate -> at most one repair -> safe fallback."""
    started = time.monotonic()
    system_prompt = bundle["system_prompt"].replace("{context}", context_text)
    raw = (await llm(question, system_prompt=system_prompt)).strip()
    initial_tokens = _slice_tokens(llm)
    parsed, parse_error = parse_grounded_json(raw)
    schema_errors: list[str] = []
    if parsed is not None:
        schema_errors = schema_validate(parsed)
    initial_answer = parsed
    initial_valid = (
        initial_answer is not None
        and not schema_errors
        and validate_citations(initial_answer, registry)["valid"]
    )
    repair_attempted = False
    repair_valid = None
    repair_tokens = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    repair_latency = 0.0
    final_answer = initial_answer
    validation_errors: list[str] = list(schema_errors)
    citation_validation: dict[str, Any] | None = None
    if final_answer is not None:
        citation_validation = validate_citations(final_answer, registry)
        validation_errors.extend(citation_validation["errors"])
    if not initial_valid and max_repair_attempts > 0:
        repair_attempted = True
        repair_started = time.monotonic()
        repair_prompt = bundle["repair_prompt_template"].format(
            question=question,
            context=context_text,
            invalid_output=raw,
            errors="\n".join(validation_errors[:20]) or parse_error or "unknown",
        )
        repaired_raw = (await llm(question, system_prompt=repair_prompt)).strip()
        repair_latency = round(time.monotonic() - repair_started, 3)
        repair_tokens = _slice_tokens(llm, since_last=True)
        repaired, repair_parse_error = parse_grounded_json(repaired_raw)
        repair_schema_errors: list[str] = []
        if repaired is not None:
            repair_schema_errors = schema_validate(repaired)
        repaired_citation: dict[str, Any] | None = None
        if repaired is not None:
            repaired_citation = validate_citations(repaired, registry)
        repair_valid = (
            repaired is not None
            and not repair_schema_errors
            and (repaired_citation or {}).get("valid", False)
        )
        if repair_valid:
            final_answer = repaired
            citation_validation = repaired_citation
            validation_errors = list(repair_schema_errors)
        else:
            final_answer = None
            citation_validation = repaired_citation
    # Safe fallback when still invalid
    if final_answer is None:
        safety_dropped = 0
        final_answer = {
            "status": "insufficient_evidence",
            "answer": INSUFFICIENT_EVIDENCE_ANSWER,
            "claims": [],
            "refusal_reason": "grounded_answer_invalid_after_repair",
        }
    else:
        final_answer, safety_errors, safety_dropped = apply_safety_gate(
            final_answer, registry, citation_validation or {}
        )
        validation_errors.extend(safety_errors)
    final_validation = validate_citations(final_answer, registry)
    total_latency = round(time.monotonic() - started, 3)
    return {
        "raw_output": raw,
        "structured_answer": final_answer,
        "parse_error": parse_error,
        "initial_valid": bool(initial_valid),
        "initial_validation": {
            "schema_errors": schema_errors,
            "citation_validation": citation_validation if initial_answer is not None else None,
        },
        "repair_attempted": repair_attempted,
        "repair_valid": repair_valid,
        "repair_latency": repair_latency,
        "repair_tokens": repair_tokens,
        "final_validation": final_validation,
        "final_valid": final_validation["valid"],
        "safety_dropped_claims": safety_dropped,
        "total_latency": total_latency,
        "initial_tokens": initial_tokens,
    }


def _slice_tokens(llm: Any, *, since_last: bool = False) -> dict[str, int]:
    if since_last:
        return {
            "input_tokens": llm.calls[-1].get("input_tokens", 0),
            "output_tokens": llm.calls[-1].get("output_tokens", 0),
            "total_tokens": llm.calls[-1].get("total_tokens", 0),
        }
    if not llm.calls:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    call = llm.calls[-1]
    return {
        "input_tokens": call.get("input_tokens", 0),
        "output_tokens": call.get("output_tokens", 0),
        "total_tokens": call.get("total_tokens", 0),
    }

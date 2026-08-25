"""Read-only Phase 10B-3J-J1S-R response-to-Trace certification helpers."""

from __future__ import annotations

from collections.abc import Mapping

REQUIRED_J1S_TRACE_FIELDS = (
    "structured_citation_flag",
    "json_mode_enabled",
    "source_registry_count",
    "source_registry_sha256",
    "requirement_registry_count",
    "requirement_registry_sha256",
    "provider_raw_response_sha256",
    "parsed_structured_output_sha256",
    "structured_output_valid",
    "structured_citation_fallback",
    "structured_citation_fallback_mode",
    "structured_citation_fallback_reason",
    "backend_generate_call_count",
    "backend_second_query_called",
)
_PUBLIC_INTERNAL_FIELDS = frozenset(
    {
        "source_registry",
        "requirement_registry",
        "config_sha",
        "provider_raw_response",
        "structured_citation_fallback_reason",
        "generation_id",
        "generation_id_internal",
    }
)


def build_linkage_record(
    saved: Mapping[str, object],
    trace: Mapping[str, object],
    *,
    expected_generation_id: str,
) -> dict[str, object]:
    """Derive a non-mutating linkage record from one saved response and Trace."""

    response = saved.get("response")
    if not isinstance(response, Mapping):
        raise RuntimeError("saved response is missing")
    response_request_id = response.get("request_id")
    response_trace_id = response.get("trace_id")
    response_generation_id = response.get("generation_id")
    trace_request_id = trace.get("request_id")
    trace_trace_id = trace.get("trace_id")
    trace_generation_id = trace.get("generation_id")
    required_fields_present = all(field in trace for field in REQUIRED_J1S_TRACE_FIELDS)
    source_sha = trace.get("source_registry_sha256")
    raw_sha = trace.get("provider_raw_response_sha256")
    parsed_sha = trace.get("parsed_structured_output_sha256")
    identity_mismatch = any(
        (
            not isinstance(response_request_id, str),
            not isinstance(response_trace_id, str),
            response_request_id != trace_request_id,
            response_trace_id != trace_trace_id,
            response_generation_id is not None
            and response_generation_id != expected_generation_id,
            trace_generation_id != expected_generation_id,
        )
    )
    ordinary_response_has_no_internal_fields = not bool(
        _PUBLIC_INTERNAL_FIELDS & set(response)
    )
    linked = (
        not identity_mismatch
        and required_fields_present
        and trace.get("structured_citation_flag") is True
        and trace.get("json_mode_enabled") is True
        and isinstance(trace.get("source_registry_count"), int)
        and bool(source_sha)
        and bool(raw_sha)
        and bool(parsed_sha)
        and trace.get("backend_generate_call_count") == 1
        and trace.get("backend_second_query_called") is False
        and ordinary_response_has_no_internal_fields
    )
    return {
        "question_id": saved.get("question_id"),
        "request_id": response_request_id,
        "trace_id": response_trace_id,
        "persisted_trace_lookup_key": "trace_id",
        "generation_id": trace_generation_id,
        "provider_raw_response_sha256": raw_sha,
        "source_registry_sha256": source_sha,
        "parsed_structured_output_sha256": parsed_sha,
        "response_present": True,
        "persisted_trace_present": True,
        "identity_mismatch": identity_mismatch,
        "linked": linked,
        "original_trace_version": trace.get("trace_version"),
        "required_j1s_fields_present": required_fields_present,
        "admin_projection_complete_after_fix": required_fields_present,
        "offline_contract_reconciled": linked,
        "historical_trace_mutated": False,
        "structured_output_valid": trace.get("structured_output_valid"),
        "structured_citation_fallback": trace.get("structured_citation_fallback"),
        "structured_citation_fallback_mode": trace.get(
            "structured_citation_fallback_mode"
        ),
        "structured_citation_fallback_reason": trace.get(
            "structured_citation_fallback_reason"
        ),
        "backend_generate_call_count": trace.get("backend_generate_call_count"),
        "backend_second_query_called": trace.get("backend_second_query_called"),
        "ordinary_response_has_no_internal_fields": ordinary_response_has_no_internal_fields,
    }

"""Guard Phase 10B-3J-J1S evaluation sequencing.

The network runner is intentionally not invoked by importing this module.  The
preflight guard is deterministic and must pass before a Development command is
allowed to create a 36-question result file.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def j1s_environment(base: Mapping[str, str]) -> dict[str, str]:
    """Return a non-secret environment with exactly the J1S flag enabled."""

    result = dict(base)
    result.update(
        {
            "QA_STRUCTURED_CITATION_OUTPUT_ENABLED": "true",
            "QA_CLAIM_CITATION_PRUNING_ENABLED": "false",
            "QA_CLAIM_EVIDENCE_ATTRIBUTION_ENABLED": "false",
            "QA_CITATION_REBINDING_ENABLED": "false",
            "QA_MINIMAL_CITATION_SELECTION_ENABLED": "false",
            "QA_UNSUPPORTED_CLAIM_ENFORCEMENT_ENABLED": "false",
            "QA_GROUNDING_FALSE_NEGATIVE_RECOVERY_ENABLED": "false",
            "QA_COVERAGE_AWARE_SELECTION_ENABLED": "false",
            "QA_PARTIAL_GENERATION_ENABLED": "false",
            "QA_SUPPLEMENTAL_RETRIEVAL_ENABLED": "false",
            "ENABLE_LLM_CACHE": "false",
        }
    )
    return result


_PREFLIGHT_TRUE = (
    "structured_citation_flag",
    "json_mode_enabled",
    "source_registry_present",
    "source_registry_identity_resolved",
    "candidate_generation_correct",
    "structured_output_valid",
)


def assert_preflight_allows_development(cases: Sequence[Mapping[str, object]]) -> None:
    """Fail closed unless each of the exactly three J1S-1 cases is proven safe."""

    if len(cases) != 3:
        raise RuntimeError(f"J1S-1 preflight requires exactly 3 cases, got {len(cases)}")
    failures: list[str] = []
    for index, case in enumerate(cases, 1):
        if case.get("http_status") != 200:
            failures.append(f"case {index}: http_status")
        if case.get("backend_generate_call_count") != 1:
            failures.append(f"case {index}: backend_generate_call_count")
        if case.get("backend_second_query_called") is not False:
            failures.append(f"case {index}: backend_second_query_called")
        if case.get("active_pointer_changed") is not False:
            failures.append(f"case {index}: active_pointer_changed")
        for name in _PREFLIGHT_TRUE:
            if case.get(name) is not True:
                failures.append(f"case {index}: {name}")
    if failures:
        raise RuntimeError("J1S-1 preflight failed: " + ", ".join(failures))

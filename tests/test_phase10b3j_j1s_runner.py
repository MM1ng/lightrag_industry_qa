from __future__ import annotations

import pytest
from scripts.run_phase10b3j_j1s import (
    assert_preflight_allows_development,
    j1s_environment,
)


def _case(*, valid: bool = True) -> dict[str, object]:
    return {
        "http_status": 200,
        "backend_generate_call_count": 1,
        "structured_citation_flag": True,
        "json_mode_enabled": True,
        "source_registry_present": True,
        "source_registry_identity_resolved": True,
        "candidate_generation_correct": True,
        "backend_second_query_called": False,
        "active_pointer_changed": False,
        "structured_output_valid": valid,
    }


def test_development_is_rejected_without_three_passing_preflight_cases() -> None:
    with pytest.raises(RuntimeError, match="J1S-1 preflight"):
        assert_preflight_allows_development((_case(), _case()))


def test_development_is_rejected_when_a_preflight_contract_field_fails() -> None:
    failed = _case()
    failed["backend_generate_call_count"] = 2

    with pytest.raises(RuntimeError, match="backend_generate_call_count"):
        assert_preflight_allows_development((_case(), _case(), failed))


def test_j1s_environment_enables_only_structured_citation_output() -> None:
    values = j1s_environment({"SERVICE_API_KEY": "not-a-secret"})

    assert values["QA_STRUCTURED_CITATION_OUTPUT_ENABLED"] == "true"
    assert values["QA_SUPPLEMENTAL_RETRIEVAL_ENABLED"] == "false"
    assert values["QA_CLAIM_CITATION_PRUNING_ENABLED"] == "false"
    assert values["QA_GROUNDING_FALSE_NEGATIVE_RECOVERY_ENABLED"] == "false"
    assert values["QA_COVERAGE_AWARE_SELECTION_ENABLED"] == "false"
    assert values["QA_PARTIAL_GENERATION_ENABLED"] == "false"

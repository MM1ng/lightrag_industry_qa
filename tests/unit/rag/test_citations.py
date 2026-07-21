from __future__ import annotations

import warnings

import pytest

from industrial_energy_agent.domain import (
    ManualCitation,
    SensorCitation,
    SourceType,
    SyntheticCitation,
)
from industrial_energy_agent.domain.errors import CitationValidationError
from industrial_energy_agent.rag.citations import (
    deduplicate_citations,
    format_citation,
    format_manual_citation,
)


def _manual_citation() -> ManualCitation:
    return ManualCitation(
        citation_id="manual:2196:p12:c3",
        source_file="2196-ANSI-Manual-Chinese.pdf",
        document_title="2196 ANSI 泵安装、运行与维护手册",
        page_number=12,
        section_title=None,
        chunk_id="manual-2196-p0012-c003-a1b2c3d4",
    )


def test_manual_citation_uses_the_server_owned_display_format() -> None:
    citation = _manual_citation()

    assert format_manual_citation(citation) == (
        "[2196 ANSI 泵安装、运行与维护手册\N{FULLWIDTH COMMA}第12页"
        "\N{FULLWIDTH COMMA}manual-2196-p0012-c003-a1b2c3d4]"
    )


def test_sensor_citation_format_contains_dataset_cycle_features_units_and_artifact() -> None:
    citation = SensorCitation(
        citation_id="sensor:1200:PS1__mean",
        dataset="UCI hydraulic_systems",
        cycle_id=1200,
        artifact_version="sha256:abc",
        features={"PS1__mean": 160.0},
        units={"PS1__mean": "bar"},
    )

    rendered = format_citation(citation)

    assert "UCI hydraulic_systems" in rendered
    assert "周期1200" in rendered
    assert "PS1__mean=160 bar" in rendered
    assert "sha256:abc" in rendered


def test_synthetic_citation_format_keeps_the_demo_marker_visible() -> None:
    citation = SyntheticCitation(
        citation_id="case:case-001",
        source_file="fault_cases.json",
        case_id="case-001",
    )

    rendered = format_citation(citation)

    assert "case-001" in rendered
    assert "synthetic_demo" in rendered


def test_formatter_rejects_free_form_model_output() -> None:
    with pytest.raises(CitationValidationError, match="validated citation"):
        format_citation("[某手册\N{FULLWIDTH COMMA}第999页\N{FULLWIDTH COMMA}invented-chunk]")  # type: ignore[arg-type]


def test_formatter_revalidates_source_specific_fields() -> None:
    invalid = SensorCitation.model_construct(
        citation_id="sensor:1200:PS1__mean",
        source_type=SourceType.SENSOR,
        dataset="UCI hydraulic_systems",
        cycle_id=1200,
        artifact_version="sha256:abc",
        features={"PS1__mean": 160.0},
        units={"PS1__mean": "bar"},
        page_number=999,
    )

    with (
        warnings.catch_warnings(record=True) as emitted,
        pytest.raises(CitationValidationError, match="source-specific"),
    ):
        format_citation(invalid)

    assert emitted == []


def test_deduplication_preserves_order_and_rejects_identity_collisions() -> None:
    first = _manual_citation()
    same = first.model_copy(deep=True)

    assert deduplicate_citations([first, same]) == [first]

    conflicting = first.model_copy(update={"page_number": 13})
    with pytest.raises(CitationValidationError, match="citation_id collision"):
        deduplicate_citations([first, conflicting])

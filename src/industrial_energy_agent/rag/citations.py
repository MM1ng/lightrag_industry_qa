"""Server-owned citation validation, formatting, and deduplication."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import TypeAdapter, ValidationError

from industrial_energy_agent.domain.errors import CitationValidationError
from industrial_energy_agent.domain.models import (
    Citation,
    ManualCitation,
    SensorCitation,
    SyntheticCitation,
)

_CITATION_ADAPTER: TypeAdapter[Citation] = TypeAdapter(Citation)
_CITATION_MODELS = (ManualCitation, SensorCitation, SyntheticCitation)
_DISPLAY_SEPARATOR = "\N{FULLWIDTH COMMA}"


def validate_citation(value: object) -> Citation:
    """Revalidate a model so constructed or mutated objects cannot bypass provenance rules."""

    if not isinstance(value, _CITATION_MODELS):
        raise CitationValidationError("Citation formatter requires a validated citation model.")
    try:
        return _CITATION_ADAPTER.validate_python(value.model_dump(mode="python", warnings="none"))
    except ValidationError:
        raise CitationValidationError("Citation contains invalid source-specific fields.") from None


def format_manual_citation(value: ManualCitation) -> str:
    """Render a physical-page citation using only validated server-side fields."""

    citation = validate_citation(value)
    if not isinstance(citation, ManualCitation):
        raise CitationValidationError("Expected a validated manual citation.")
    return (
        f"[{citation.document_title}{_DISPLAY_SEPARATOR}第{citation.page_number}页"
        f"{_DISPLAY_SEPARATOR}{citation.chunk_id}]"
    )


def format_sensor_citation(value: SensorCitation) -> str:
    """Render all sensor feature values with their units and artifact identity."""

    citation = validate_citation(value)
    if not isinstance(citation, SensorCitation):
        raise CitationValidationError("Expected a validated sensor citation.")
    rendered_features = "/".join(
        f"{name}={citation.features[name]:g} {citation.units[name]}"
        for name in sorted(citation.features)
    )
    return (
        f"[{citation.dataset}{_DISPLAY_SEPARATOR}周期{citation.cycle_id}"
        f"{_DISPLAY_SEPARATOR}{rendered_features}{_DISPLAY_SEPARATOR}"
        f"{citation.artifact_version}]"
    )


def format_synthetic_citation(value: SyntheticCitation) -> str:
    """Render a synthetic citation while keeping its demo provenance visible."""

    citation = validate_citation(value)
    if not isinstance(citation, SyntheticCitation):
        raise CitationValidationError("Expected a validated synthetic citation.")
    identifiers = [
        identifier
        for identifier in (citation.entity_id, citation.case_id)
        if identifier is not None
    ]
    return f"[{citation.data_type}{_DISPLAY_SEPARATOR}{'/'.join(identifiers)}]"


def format_citation(value: Citation) -> str:
    """Dispatch formatting by the validated citation discriminator."""

    citation = validate_citation(value)
    if isinstance(citation, ManualCitation):
        return format_manual_citation(citation)
    if isinstance(citation, SensorCitation):
        return format_sensor_citation(citation)
    return format_synthetic_citation(citation)


def deduplicate_citations(values: Sequence[Citation]) -> list[Citation]:
    """Preserve order while rejecting conflicting uses of a citation identity."""

    result: list[Citation] = []
    by_id: dict[str, Citation] = {}
    for value in values:
        citation = validate_citation(value)
        existing = by_id.get(citation.citation_id)
        if existing is None:
            by_id[citation.citation_id] = citation
            result.append(citation)
            continue
        if existing.model_dump(mode="json") != citation.model_dump(mode="json"):
            raise CitationValidationError(f"citation_id collision for {citation.citation_id!r}")
    return result

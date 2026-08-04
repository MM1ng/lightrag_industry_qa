"""Safe parser for provider structured-answer payloads.

Parsing is intentionally permissive at the boundary and strict in the
subsequent support validator.  A malformed provider response returns a
``StructuredAnswer`` with ``parse_error`` so callers can use the existing
answer path without a second model request.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from .evidence_answer_schema import StructuredAnswer, StructuredAnswerPoint

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.I | re.S)


def _tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def parse_structured_answer(payload: Any, *, fallback_answer: str = "") -> StructuredAnswer:
    """Parse JSON/dict payload without accepting generated evidence content.

    Evidence IDs are treated as opaque references and are never created or
    repaired by this function; unknown IDs are removed later by validation.
    """

    value = payload
    if isinstance(payload, str):
        candidate = _FENCE.match(payload)
        raw = candidate.group(1) if candidate else payload
        try:
            value = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return StructuredAnswer(fallback_answer or payload, (), "success", "invalid_json")
    if not isinstance(value, Mapping):
        return StructuredAnswer(fallback_answer, (), "success", "payload_not_object")
    answer = str(value.get("answer", fallback_answer) or "")
    status = str(value.get("status", "success"))
    if status not in {"success", "partial_answer", "insufficient_evidence", "safety_blocked"}:
        status = "success"
    raw_points = value.get("answer_points", value.get("points", ()))
    if not isinstance(raw_points, (list, tuple)):
        return StructuredAnswer(answer, (), status, "answer_points_not_array")
    points: list[StructuredAnswerPoint] = []
    malformed = 0
    for index, raw_point in enumerate(raw_points, 1):
        if not isinstance(raw_point, Mapping):
            malformed += 1
            continue
        text = str(raw_point.get("text", raw_point.get("content", "")) or "").strip()
        point_id = str(raw_point.get("point_id", f"P{index}") or f"P{index}").strip()
        if not text or not point_id:
            malformed += 1
            continue
        try:
            step_index = raw_point.get("step_index")
            step_index = int(step_index) if step_index is not None else None
        except (TypeError, ValueError):
            step_index = None
        points.append(
            StructuredAnswerPoint(
                point_id=point_id,
                text=text,
                evidence_ids=_tuple(raw_point.get("evidence_ids", raw_point.get("evidence", ()))),
                object=str(raw_point["object"]) if raw_point.get("object") is not None else None,
                parameter=str(raw_point["parameter"]) if raw_point.get("parameter") is not None else None,
                numeric_values=_tuple(raw_point.get("numeric_values", raw_point.get("values"))),
                units=_tuple(raw_point.get("units", raw_point.get("unit"))),
                conditions=_tuple(raw_point.get("conditions", raw_point.get("condition"))),
                model=str(raw_point["model"]) if raw_point.get("model") is not None else None,
                negated=bool(raw_point.get("negated", False)),
                step_index=step_index,
                step_relation=(str(raw_point["step_relation"]) if raw_point.get("step_relation") is not None else None),
            )
        )
    return StructuredAnswer(answer, tuple(points), status, "malformed_points" if malformed else None)


# Short alias for callers that prefer ``parse_answer``.
parse_answer = parse_structured_answer


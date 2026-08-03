"""Safe keyword highlighting model (HTML escaping is left to Streamlit)."""

from __future__ import annotations

import re
from html import escape


def highlight_terms(text: str, terms: list[str]) -> str:
    """Mark reliable literal terms without interpreting input as HTML."""
    escaped_text = escape(text)
    if not escaped_text or not terms:
        return escaped_text
    pattern = re.compile("|".join(re.escape(escape(term)) for term in terms if term), re.IGNORECASE)
    return pattern.sub(lambda match: f"**{match.group(0)}**", escaped_text)

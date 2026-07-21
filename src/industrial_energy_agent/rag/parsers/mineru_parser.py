"""Lazy MinerU availability boundary with safe whole-document fallback semantics."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

from industrial_energy_agent.rag.document_parser import (
    ParsedDocument,
    ParserUnavailableError,
)


class MinerUParser:
    """Detect MinerU without importing it during normal application startup."""

    name = "mineru"

    def is_available(self) -> bool:
        return (
            importlib.util.find_spec("mineru") is not None
            or importlib.util.find_spec("magic_pdf") is not None
            or shutil.which("mineru") is not None
            or shutil.which("magic-pdf") is not None
        )

    def parse(self, path: Path) -> ParsedDocument:
        if not self.is_available():
            raise ParserUnavailableError("MinerU is unavailable")
        raise ParserUnavailableError(
            "The installed MinerU entrypoint has not passed this project's compatibility probe"
        )

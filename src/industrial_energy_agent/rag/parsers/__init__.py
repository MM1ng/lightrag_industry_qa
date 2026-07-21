"""Optional and fallback manual parser implementations."""

from industrial_energy_agent.rag.parsers.mineru_parser import MinerUParser
from industrial_energy_agent.rag.parsers.pymupdf_parser import PyMuPDFParser

__all__ = ["MinerUParser", "PyMuPDFParser"]

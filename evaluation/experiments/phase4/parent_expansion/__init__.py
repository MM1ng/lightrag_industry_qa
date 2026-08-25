"""Phase 4C: deterministic Parent Expansion ablation over the frozen PyMuPDF index."""

import sys
from pathlib import Path

_PROJECT_SRC = Path(__file__).resolve().parents[4] / "src"
if str(_PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(_PROJECT_SRC))

__all__ = ["config"]

"""Phase 4D: frozen-candidate Rerank ablation (provider-neutral)."""

import sys
from pathlib import Path

_PROJECT_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(_PROJECT_SRC))

__all__ = ["config", "reranker"]

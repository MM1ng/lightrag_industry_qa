"""Phase 3A parser-backend comparison experiment package."""

import sys
from pathlib import Path

# The conda env may carry an editable install pointing at an old worktree.
# The experiment must always use the CURRENT project source, never worktree code.
_CURRENT_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_CURRENT_SRC) not in sys.path:
    sys.path.insert(0, str(_CURRENT_SRC))

__all__ = ["config", "common", "quality", "metrics"]

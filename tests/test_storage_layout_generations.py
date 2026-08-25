from __future__ import annotations

from pathlib import Path

from industrial_rag.storage_layout import (
    kb_nano_workspace,
    kb_qdrant_generation_workspace,
)

KB_ID = "a" * 32


def test_vector_backend_workspaces_are_complete_and_physically_isolated(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("KB_DATA_ROOT", str(tmp_path))

    nano_workspace = kb_nano_workspace(KB_ID)
    qdrant_workspace = kb_qdrant_generation_workspace(KB_ID, "g20260731abc")

    assert nano_workspace == tmp_path / KB_ID / "nano" / "workspace"
    assert qdrant_workspace == (
        tmp_path / KB_ID / "qdrant" / "generations" / "g20260731abc" / "workspace"
    )
    assert nano_workspace != qdrant_workspace

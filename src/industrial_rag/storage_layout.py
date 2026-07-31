"""Storage layout: generate isolated paths for each knowledge base.

All paths are derived deterministically from the KB id — never from
user-supplied names.  Added safety checks prevent traversal.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Root under which all KB data lives
_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "knowledge_bases"

# Characters allowed in generated directory names
_SAFE_ID = re.compile(r"^[a-f0-9]{8,64}$")


def _validate_kb_id(kb_id: str) -> None:
    """Refuse any kb_id that does not look like a hex uuid."""
    if not _SAFE_ID.match(kb_id):
        raise ValueError(f"Invalid knowledge_base_id: {kb_id!r}")


def data_root() -> Path:
    env_root = os.environ.get("KB_DATA_ROOT", "").strip()
    if env_root:
        return Path(env_root)
    return _DATA_ROOT


def kb_base_dir(kb_id: str) -> Path:
    _validate_kb_id(kb_id)
    return data_root() / kb_id


def kb_workspace_dir(kb_id: str) -> Path:
    return kb_base_dir(kb_id) / "lightrag"


def kb_uploads_dir(kb_id: str) -> Path:
    return kb_base_dir(kb_id) / "uploads"


def kb_parsed_dir(kb_id: str) -> Path:
    return kb_base_dir(kb_id) / "parsed"


def kb_parsed_documents_dir(kb_id: str) -> Path:
    return kb_parsed_dir(kb_id) / "documents"


def kb_parent_chunks_dir(kb_id: str) -> Path:
    return kb_parsed_dir(kb_id) / "parent_chunks"


def kb_child_chunks_dir(kb_id: str) -> Path:
    return kb_parsed_dir(kb_id) / "child_chunks"


def kb_manifests_dir(kb_id: str) -> Path:
    return kb_parsed_dir(kb_id) / "manifests"


def kb_tasks_dir(kb_id: str) -> Path:
    return kb_base_dir(kb_id) / "tasks"


def kb_temp_dir(kb_id: str) -> Path:
    return kb_base_dir(kb_id) / "tmp"


def document_stored_path(kb_id: str, stored_file_name: str) -> Path:
    """Return the safe stored-file path for a document upload."""
    _validate_kb_id(kb_id)
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", stored_file_name)
    return kb_uploads_dir(kb_id) / safe_name


# ---------------------------------------------------------------------------
# Safety checks for delete operations
# ---------------------------------------------------------------------------


def is_safe_to_delete(path: Path, *, kb_id: str) -> bool:
    """Return True only when ``path`` is inside the designated KB data tree
    and the KB id appears in the resolved path."""
    root = data_root().resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return False  # outside KB data root
    # Must contain the kb_id in the path
    if kb_id not in str(resolved):
        return False
    # Must not be the root itself
    if resolved == root:
        return False
    # Must not be the project root
    if resolved == Path(__file__).resolve().parents[2]:
        return False
    return True


def is_safe_path_for_write(path: Path) -> bool:
    """Reject writes that traverse outside the project data directory."""
    root = data_root().resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return False
    return True

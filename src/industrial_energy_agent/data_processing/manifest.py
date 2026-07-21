"""Streaming, immutable manifests for protected source files."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

_MANIFEST_SCHEMA_VERSION = 1
_HASH_CHUNK_SIZE = 1024 * 1024
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True, order=True)
class ManifestEntry:
    """A stable path/size/content identity for one regular file."""

    relative_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ManifestDifference:
    """Path-localized differences between two manifests."""

    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]

    @property
    def is_unchanged(self) -> bool:
        return not (self.added or self.removed or self.changed)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(
    source_root: Path | str,
    *,
    relative_to: Path | str | None = None,
) -> list[ManifestEntry]:
    """Return a sorted manifest without writing anywhere under ``source_root``."""

    source = Path(source_root).resolve(strict=True)
    if not source.is_dir():
        raise ValueError(f"manifest source is not a directory: {source}")

    base = source if relative_to is None else Path(relative_to).resolve(strict=True)
    if not source.is_relative_to(base):
        raise ValueError("manifest source must be contained by relative_to")

    entries: list[ManifestEntry] = []
    for path in source.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(source):
            raise ValueError(f"manifest file escapes source root: {path}")
        entries.append(
            ManifestEntry(
                relative_path=resolved.relative_to(base).as_posix(),
                size_bytes=resolved.stat().st_size,
                sha256=_sha256_file(resolved),
            )
        )
    return sorted(entries, key=lambda entry: entry.relative_path)


def _manifest_payload(entries: Sequence[ManifestEntry]) -> dict[str, object]:
    paths = [entry.relative_path for entry in entries]
    if len(paths) != len(set(paths)):
        raise ValueError("manifest contains duplicate relative paths")
    return {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "files": [
            {
                "relative_path": entry.relative_path,
                "size_bytes": entry.size_bytes,
                "sha256": entry.sha256,
            }
            for entry in sorted(entries, key=lambda item: item.relative_path)
        ],
    }


def _is_within(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def write_manifest_atomic(
    entries: Sequence[ManifestEntry],
    destination: Path | str,
    *,
    protected_roots: Iterable[Path | str] = (),
) -> None:
    """Atomically write a manifest outside every protected source root."""

    target = Path(destination)
    resolved_target = target.resolve(strict=False)
    for protected_root in protected_roots:
        protected = Path(protected_root).resolve(strict=True)
        if _is_within(resolved_target, protected):
            raise ValueError("manifest destination is inside a protected source")

    payload = _manifest_payload(entries)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)

    temporary_path: Path | None = None
    try:
        descriptor, raw_temporary_path = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(raw_temporary_path)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def load_manifest(path: Path | str) -> tuple[ManifestEntry, ...]:
    """Load and strictly validate a versioned manifest."""

    payload: object = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest root must be an object")
    if payload.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported manifest schema version")
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raise ValueError("manifest files must be a list")
    entries = tuple(_load_manifest_entry(item) for item in raw_files)
    if len({entry.relative_path for entry in entries}) != len(entries):
        raise ValueError("manifest contains duplicate relative paths")
    return tuple(sorted(entries, key=lambda entry: entry.relative_path))


def _load_manifest_entry(item: object) -> ManifestEntry:
    if not isinstance(item, dict):
        raise ValueError("manifest file entry must be an object")
    if not {"relative_path", "size_bytes", "sha256"}.issubset(item):
        raise ValueError("manifest file entry is missing required fields")

    relative_path = item["relative_path"]
    size_bytes = item["size_bytes"]
    sha256 = item["sha256"]
    if not isinstance(relative_path, str) or not _is_normalized_relative_path(relative_path):
        raise ValueError("manifest file entry relative path is unsafe or not normalized")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int):
        raise ValueError("manifest file entry size_bytes must be an integer")
    if size_bytes < 0:
        raise ValueError("manifest file entry size_bytes must be non-negative")
    if not isinstance(sha256, str) or _SHA256_PATTERN.fullmatch(sha256) is None:
        raise ValueError("manifest file entry sha256 must be 64 lowercase hex characters")
    return ManifestEntry(relative_path, size_bytes, sha256)


def _is_normalized_relative_path(value: str) -> bool:
    if not value or "\\" in value or "\x00" in value:
        return False
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        return False
    if any(part in {"", ".", ".."} for part in value.split("/")):
        return False
    return value == posix_path.as_posix()


def compare_manifests(
    before: Sequence[ManifestEntry],
    after: Sequence[ManifestEntry],
) -> ManifestDifference:
    """Compare manifests without exposing file contents."""

    before_by_path = {entry.relative_path: entry for entry in before}
    after_by_path = {entry.relative_path: entry for entry in after}
    before_paths = set(before_by_path)
    after_paths = set(after_by_path)
    return ManifestDifference(
        added=tuple(sorted(after_paths - before_paths)),
        removed=tuple(sorted(before_paths - after_paths)),
        changed=tuple(
            sorted(
                path
                for path in before_paths & after_paths
                if before_by_path[path] != after_by_path[path]
            )
        ),
    )

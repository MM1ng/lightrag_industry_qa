from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from industrial_energy_agent.data_processing.manifest import (
    ManifestEntry,
    build_manifest,
    compare_manifests,
    load_manifest,
    write_manifest_atomic,
)


def test_manifest_records_normalized_relative_path_size_and_sha256(tmp_path: Path) -> None:
    source = tmp_path / "source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    payload = b"abc"
    (nested / "a.txt").write_bytes(payload)

    entry = build_manifest(source)[0]

    assert entry.relative_path == "nested/a.txt"
    assert entry.size_bytes == 3
    assert entry.sha256 == hashlib.sha256(payload).hexdigest()


def test_manifest_traverses_regular_files_only_in_stable_order(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "empty-directory").mkdir(parents=True)
    (source / "z.txt").write_bytes(b"z")
    (source / "a.txt").write_bytes(b"a")

    entries = build_manifest(source)

    assert [entry.relative_path for entry in entries] == ["a.txt", "z.txt"]


def test_manifest_can_record_paths_relative_to_a_project_root(tmp_path: Path) -> None:
    source = tmp_path / "data" / "manuals"
    source.mkdir(parents=True)
    (source / "manual.pdf").write_bytes(b"pdf")

    entry = build_manifest(source, relative_to=tmp_path)[0]

    assert entry.relative_path == "data/manuals/manual.pdf"


def test_atomic_manifest_round_trip_preserves_entries(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_bytes(b"abc")
    destination = tmp_path / "processed" / "source_before.json"
    entries = build_manifest(source)

    write_manifest_atomic(entries, destination, protected_roots=(source,))

    assert load_manifest(destination) == tuple(entries)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert not list(destination.parent.glob("*.tmp"))


def _write_manifest_payload(tmp_path: Path, file_entry: object) -> Path:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "files": [file_entry]}),
        encoding="utf-8",
    )
    return manifest


@pytest.mark.parametrize("relative_path", ["../secret.txt", "/secret.txt", "C:/secret.txt"])
def test_manifest_load_rejects_unsafe_paths(
    tmp_path: Path,
    relative_path: str,
) -> None:
    manifest = _write_manifest_payload(
        tmp_path,
        {"relative_path": relative_path, "size_bytes": 3, "sha256": "a" * 64},
    )

    with pytest.raises(ValueError, match="relative path"):
        load_manifest(manifest)


def test_manifest_load_rejects_negative_size(tmp_path: Path) -> None:
    manifest = _write_manifest_payload(
        tmp_path,
        {"relative_path": "a.txt", "size_bytes": -1, "sha256": "a" * 64},
    )

    with pytest.raises(ValueError, match="size_bytes"):
        load_manifest(manifest)


@pytest.mark.parametrize("sha256", ["abc", "A" * 64, "g" * 64])
def test_manifest_load_rejects_invalid_sha256(tmp_path: Path, sha256: str) -> None:
    manifest = _write_manifest_payload(
        tmp_path,
        {"relative_path": "a.txt", "size_bytes": 3, "sha256": sha256},
    )

    with pytest.raises(ValueError, match="sha256"):
        load_manifest(manifest)


@pytest.mark.parametrize(
    "file_entry",
    [
        None,
        "not-an-object",
        {},
        {"relative_path": "a.txt", "size_bytes": 3},
        {"relative_path": "a.txt", "size_bytes": True, "sha256": "a" * 64},
    ],
)
def test_manifest_load_rejects_malformed_file_entries(
    tmp_path: Path,
    file_entry: object,
) -> None:
    manifest = _write_manifest_payload(tmp_path, file_entry)

    with pytest.raises(ValueError, match="file entry"):
        load_manifest(manifest)


def test_atomic_manifest_refuses_to_write_under_a_protected_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    entries = [ManifestEntry(relative_path="a.txt", size_bytes=3, sha256="a" * 64)]

    with pytest.raises(ValueError, match="protected source"):
        write_manifest_atomic(
            entries,
            source / "manifest.json",
            protected_roots=(source,),
        )


def test_manifest_comparison_localizes_added_removed_and_changed_files() -> None:
    before = (
        ManifestEntry("same.txt", 1, "a" * 64),
        ManifestEntry("changed.txt", 1, "b" * 64),
        ManifestEntry("removed.txt", 1, "c" * 64),
    )
    after = (
        ManifestEntry("same.txt", 1, "a" * 64),
        ManifestEntry("changed.txt", 2, "d" * 64),
        ManifestEntry("added.txt", 1, "e" * 64),
    )

    difference = compare_manifests(before, after)

    assert difference.is_unchanged is False
    assert difference.added == ("added.txt",)
    assert difference.removed == ("removed.txt",)
    assert difference.changed == ("changed.txt",)

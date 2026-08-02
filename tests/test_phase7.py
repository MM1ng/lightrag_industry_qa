"""Phase 7: release packaging, closeout and rehearsal tests (offline)."""

from __future__ import annotations

import hashlib
import json
import zipfile

from evaluation.experiments.phase7.config import (
    CANDIDATE_POOL_PATH,
    CANDIDATE_POOL_SHA256,
    DIST_DIR,
    PHASE7_ROOT,
    RC_VERSION,
    RC_ZIP_NAME,
)
from industrial_rag.version import APP_VERSION, version_info


def _load(name: str) -> dict:
    return json.loads((PHASE7_ROOT / name).read_text(encoding="utf-8"))


def _load_closeout(name: str) -> dict:
    return json.loads(
        (PHASE7_ROOT.parent / "phase6b" / "closeout" / name).read_text(encoding="utf-8")
    )


def _load_jsonl(name: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (PHASE7_ROOT / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_phase7_pool_sha256() -> None:
    assert (
        hashlib.sha256(CANDIDATE_POOL_PATH.read_bytes()).hexdigest()
        == CANDIDATE_POOL_SHA256
    )


def test_closeout_32_to_29_mapping_complete() -> None:
    reconciliation = _load_closeout("release_gate_reconciliation.json")
    assert reconciliation["phase6_gate_count"] == 32
    assert reconciliation["phase6b_gate_count"] == 29
    assert reconciliation["omitted_phase6_gates"] == []
    assert reconciliation["all_original_hard_gates_accounted_for"] is True
    mapped_ids = [
        gate_id
        for entry in reconciliation["mapping"]
        for gate_id in entry["phase6_gate_ids"]
    ]
    assert len(mapped_ids) == 32
    assert len(set(mapped_ids)) == 32


def test_closeout_canonical_baselines_layered() -> None:
    baselines = _load_closeout("canonical_baselines.json")
    acc = baselines["answer_citation_accuracy"]
    assert acc["historical_harness_v0"]["value"] == 0.8333
    assert acc["historical_harness_v0"]["used_for_release_comparison"] is False
    assert acc["canonical_harness_v1"]["value"] == 0.6458
    assert acc["official_fastapi_v1"]["value"] == 0.7708
    diff = baselines["gate_difference"]
    assert diff["candidate_minus_baseline"] == 0.1250
    assert diff["baseline_minus_candidate"] == -0.1250
    assert diff["maximum_allowed_drop"] == 0.0200
    assert diff["passed"] is True


def test_closeout_authoritative_path() -> None:
    auth = _load_closeout("authoritative_path.json")
    assert auth["authoritative_release_path"] == "official_fastapi"
    assert auth["harness_is_input_equivalent_to_fastapi"] is False
    assert auth["official_path_required_for_future_release_gates"] is True


def test_c007_tech_debt_registered() -> None:
    path = PHASE7_ROOT.parent / "phase6b" / "tech_debt" / "OFFICIAL-PATH-CONTEXT-001.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "C007" in text
    assert "官方 FastAPI 仍为权威发布路径" in text


def test_model_identity_fields_spec() -> None:
    spec = _load_closeout("model_identity_fields.json")
    fields = spec["fields"]
    for name in (
        "requested_model",
        "configured_model",
        "provider_reported_model",
        "provider_reported_model_available",
        "fallback_enabled",
        "fallback_detected",
    ):
        assert name in fields
    assert fields["actual_model"]["deprecated"] is True


def test_single_version_source_and_rc_format() -> None:
    assert APP_VERSION == "0.1.0-rc.1"
    assert APP_VERSION == RC_VERSION
    parts = APP_VERSION.split("-")
    assert parts[0].count(".") == 2
    assert parts[1] == "rc.1"
    info = version_info()
    assert info["app_version"] == APP_VERSION
    assert info["release_channel"] == "rc"
    assert len(info["git_commit"]) == 40


def test_package_include_exclude_and_zip() -> None:
    include = _load("package/include_manifest.json")
    exclude = _load("package/exclude_manifest.json")
    checksum = _load("package/checksum_manifest.json")
    zip_path = DIST_DIR / RC_ZIP_NAME
    assert zip_path.is_file()
    assert checksum["total_file_count"] == include["included_file_count"]
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        assert names
        assert not any(name.startswith("/") or name.startswith("..") for name in names)
        assert "phase3-uncommitted-backup.patch" not in names
        assert ".env" not in names
    assert checksum["zip"]["sha256"] == hashlib.sha256(zip_path.read_bytes()).hexdigest()
    assert "phase3-uncommitted-backup.patch" in exclude["exclude_patterns"]


def test_secret_scan_clean() -> None:
    scan = _load("security/secret_scan.json")
    assert scan["confirmed_secret_count"] == 0
    assert scan["passed"] is True


def test_rehearsal_all_passed() -> None:
    summary = _load("rehearsal/summary.json")
    assert summary["passed"] is True
    for stage, result in summary["results"].items():
        assert result["passed"] is True, stage


def test_acceptance_gates_passed() -> None:
    gates = _load("acceptance/release_gates.json")
    assert gates["passed"] is True
    assert gates["release_gates"]["golden_subset_complete_20"] is True
    assert gates["release_gates"]["n001_n002_refused"] is True
    assert gates["release_gates"]["http_success_rate"] == 1.0
    assert gates["release_gates"]["error_rate"] == 0.0


def test_acceptance_golden_subset_counts() -> None:
    rows = _load_jsonl("acceptance/golden_subset_results.jsonl")
    assert len(rows) == 20
    assert all(r["request_id"] and r["trace_id"] for r in rows)
    assert all(r["provider_reported_model"] is None for r in rows)
    assert all(r["provider_reported_model_available"] is False for r in rows)
    assert all(r["configured_model"] == "qwen-plus-2025-07-28" for r in rows)


def test_env_example_has_no_real_secrets() -> None:
    text = (PHASE7_ROOT.parents[2] / ".env.example").read_text(encoding="utf-8")
    assert "DASHSCOPE_API_KEY=" in text
    assert "sk-" not in text
    assert "your-bailian" not in text


def test_scripts_exist_and_contain_no_secret_values() -> None:
    for name in (
        "check_env.ps1",
        "start_qdrant.ps1",
        "start_api.ps1",
        "start_ui.ps1",
        "stop_local.ps1",
        "check_local.ps1",
    ):
        path = PHASE7_ROOT.parents[2] / "scripts" / name
        assert path.is_file(), name
        text = path.read_text(encoding="utf-8")
        assert "sk-" not in text

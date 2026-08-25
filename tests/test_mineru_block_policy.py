"""Phase 3A-P: deterministic MinerU adapter and fixed-model gate tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from evaluation.experiments.parser_backend.common import read_json, read_jsonl
from evaluation.experiments.parser_backend.config import PDF_NAMES
from evaluation.experiments.parser_backend.fixed_model_gate import (
    GOLDEN_SHA256,
    assert_consistency,
    load_frozen_config,
)
from evaluation.experiments.parser_backend.mineru_adapter import (
    MinerUBlockPolicy,
    blocks_from_clean_pages,
    normalize_text,
    table_to_embedding_text,
)
from industrial_rag.parser_models import BlockType, ContentType

EXPERIMENT_ROOT = Path(__file__).resolve().parents[1] / "evaluation" / "experiments" / "parser_backend"
P1_RAW_ROOT = EXPERIMENT_ROOT / "P1"
P1_CLEAN_ROOT = EXPERIMENT_ROOT / "fixed_model" / "P1_mineru"


# ---------------------------------------------------------------------------
# Deterministic normalization and table conversion
# ---------------------------------------------------------------------------


def test_normalize_text_is_deterministic_and_conservative() -> None:
    assert normalize_text("a\r\n b  c \n\n\n d") == "a\n b c\n\n d"
    full_width = "\uff21\uff22\uff23"
    assert normalize_text(full_width) == full_width  # NFC must not alter full-width chars
    assert normalize_text("") == ""


def test_table_to_embedding_text_preserves_ocr_and_structure() -> None:
    html = (
        '<table><tr><td rowspan=1 colspan=2>泵型号</td></tr>'
        '<tr><td>威氏</td><td>ACO</td></tr>'
        '<tr><td rowspan=2 colspan=1>飞利浦</td><td>32</td></tr>'
        '<tr><td>100</td></tr></table>'
    )
    text = table_to_embedding_text(html, caption="表4")
    assert "表格标题：表4" in text
    assert "威氏 | ACO" in text
    assert "AC0" not in text  # OCR error must NOT be auto-corrected
    assert text.count("行") >= 3


def test_table_to_embedding_text_keeps_raw_html_intact() -> None:
    html = '<table><tr><td>&quot;5/16&quot;</td></tr></table>'
    text = table_to_embedding_text(html)
    assert '"5/16"' in text


# ---------------------------------------------------------------------------
# Block policy filtering
# ---------------------------------------------------------------------------


def _item(btype: str, page_idx: int, text: str | None = None, **extra) -> dict:
    item: dict = {"type": btype, "page_idx": page_idx, "bbox": [0, 0, 0, 0]}
    if text is not None:
        item["text"] = text
    item.update(extra)
    return item


def test_policy_filters_layout_boilerplate_and_keeps_body() -> None:
    items = [
        _item("header", 0, "安装、操作及维护手册"),
        _item("footer", 0, "SUMMIT PUMP 2196 型"),
        _item("page_number", 0, "1"),
        _item("text", 0, "正文第一段"),
        _item("text", 1, "正文第二段", text_level=2),
    ]
    policy = MinerUBlockPolicy.from_items("a.pdf", items)
    pages = policy.clean_pages()
    assert len(pages) == 2
    audit = policy.audit
    reasons = {entry["filter_reason"] for entry in audit}
    assert reasons == {"page_layout_boilerplate"}
    assert len(audit) == 3
    combined = "\n".join(p["markdown"] for p in pages)
    assert "正文第一段" in combined and "正文第二段" in combined


def test_policy_keeps_warning_and_unique_footnote_but_filters_repeated_footnote() -> None:
    items = [
        _item("text", 0, "警告！请勿操作。"),
        _item("page_footnote", 0, "Tagholm 1"),
        _item("page_footnote", 1, "Tagholm 1"),
        _item("page_footnote", 2, "Tagholm 1"),
        _item("page_footnote", 3, "唯一脚注内容"),
    ]
    policy = MinerUBlockPolicy.from_items("b.pdf", items)
    pages = policy.clean_pages()
    combined = "\n".join(p["markdown"] for p in pages)
    assert "警告！请勿操作。" in combined
    assert "唯一脚注内容" in combined
    assert "Tagholm 1" not in combined
    reasons = {entry["filter_reason"] for entry in policy.audit}
    assert "repeated_page_footnote_layout" in reasons


def test_policy_records_audit_trail_with_hash_and_reason() -> None:
    items = [_item("header", 3, "页眉文本")]
    policy = MinerUBlockPolicy.from_items("c.pdf", items)
    policy.clean_pages()
    assert policy.audit == [
        {
            "document": "c.pdf",
            "page_number": 4,
            "block_type": "header",
            "block_hash": hashlib.sha256("页眉文本".encode()).hexdigest()[:16],
            "filter_reason": "page_layout_boilerplate",
        }
    ]


def test_policy_keeps_table_raw_html_and_marks_image_only_table() -> None:
    html = "<table><tr><td>参数</td><td>值</td></tr></table>"
    items = [
        _item("table", 0, table_body=html, table_caption=["表1"]),
        _item("table", 1, table_body=None, table_caption=[]),
    ]
    policy = MinerUBlockPolicy.from_items("d.pdf", items)
    pages = policy.clean_pages()
    assert len(pages) == 1
    assert "表格标题：表1" in pages[0]["markdown"]
    table_blocks = [b for b in pages[0]["blocks"] if b["block_type"] == BlockType.table]
    assert table_blocks and table_blocks[0]["raw_text"] == html
    assert any(e["filter_reason"] == "table_without_text_body" for e in policy.audit)


def test_blocks_from_clean_pages_preserves_table_metadata() -> None:
    html = "<table><tr><td>a</td><td>b</td></tr></table>"
    pages = [
        {
            "page_number": 7,
            "markdown": "x",
            "blocks": [
                {
                    "block_type": BlockType.table,
                    "content_type": ContentType.parameter_table,
                    "raw_text": html,
                    "embedding_text": "列：a | b",
                    "digest": "abc",
                }
            ],
        }
    ]
    blocks = blocks_from_clean_pages(pages, "d.pdf")
    assert len(blocks) == 1
    assert blocks[0].block_type == BlockType.table
    assert blocks[0].metadata["raw_html"] == html
    assert blocks[0].metadata["embedding_text"] == "列：a | b"
    assert blocks[0].source_page == 7


# ---------------------------------------------------------------------------
# Frozen config / single-variable gate
# ---------------------------------------------------------------------------


def test_frozen_config_uses_fixed_model_and_disables_fallback() -> None:
    cfg = load_frozen_config()
    assert cfg["llm_model"] == "qwen-plus-2025-07-28"
    assert cfg["index_llm_model"] == cfg["query_llm_model"] == cfg["llm_model"]
    assert cfg["model_fallback_enabled"] is False
    assert cfg["enable_thinking"] is False
    assert cfg["embedding_model"] == "text-embedding-v4"
    assert cfg["embedding_dimension"] == 1024
    assert cfg["query_mode"] == "mix"
    assert cfg["top_k"] == 12 and cfg["chunk_top_k"] == 20 and cfg["evidence_limit"] == 3
    assert cfg["enable_rerank"] is False
    assert cfg["qdrant_distance"] == "COSINE"


def test_p0_p1_config_hashes_differ_only_in_parser_backend() -> None:
    hashes = assert_consistency()
    assert hashes["p0"] == hashes["p1"]
    assert hashes["p0"]["golden_set_hash"] == GOLDEN_SHA256
    assert hashes["p0"]["chunk_config_hash"] == hashes["p1"]["chunk_config_hash"]
    assert hashes["p0"]["query_llm_config_hash"] == hashes["p1"]["query_llm_config_hash"]
    assert hashes["p0"]["qdrant_schema_hash"] == hashes["p1"]["qdrant_schema_hash"]


def test_precheck_used_only_the_fixed_model_if_present() -> None:
    precheck = EXPERIMENT_ROOT / "fixed_model" / "precheck_report.json"
    if not precheck.is_file():
        pytest.skip("fixed-model precheck report absent")
    report = read_json(precheck)
    for group, stats in report["index_stats"].items():
        assert stats["model_set"] == ["qwen-plus-2025-07-28"]
    assert report["llm_summary"]["model_mismatches"] == 0
    assert report["llm_summary"]["errors"] == 0
    assert report["estimate"]["decision"] in {"proceed", "high_quota_risk", "blocked_insufficient_quota"}


# ---------------------------------------------------------------------------
# Artifact gates (raw vs clean, P1-clean integrity)
# ---------------------------------------------------------------------------


def _clean_present() -> bool:
    return all((P1_CLEAN_ROOT / pdf / "manifest.json").is_file() for pdf in PDF_NAMES)


@pytest.mark.skipif(not _clean_present(), reason="P1-clean artifacts absent")
def test_p1_clean_manifest_is_strict_and_records_filters() -> None:
    for pdf in PDF_NAMES:
        manifest = read_json(P1_CLEAN_ROOT / pdf / "manifest.json")
        assert manifest["parser_used"] == "mineru_online_clean"
        assert manifest["fallback_used"] is False
        assert manifest["raw_result_zip_sha256"]
        cleanup = read_json(P1_CLEAN_ROOT / pdf / "cleanup_manifest.json")
        assert len(cleanup["filtered_blocks"]) == manifest["filtered_block_count"]


@pytest.mark.skipif(not _clean_present(), reason="P1-clean artifacts absent")
def test_p1_clean_has_no_orphans_and_no_over_max_children() -> None:
    for pdf in PDF_NAMES:
        parents = read_jsonl(P1_CLEAN_ROOT / pdf / "parent_chunks.jsonl")
        children = read_jsonl(P1_CLEAN_ROOT / pdf / "child_chunks.jsonl")
        parent_ids = {p["parent_chunk_id"] for p in parents}
        assert all(c["parent_chunk_id"] in parent_ids for c in children)
        assert all(c["token_count"] <= 700 for c in children)


@pytest.mark.skipif(not _clean_present(), reason="P1-clean artifacts absent")
def test_p1_clean_preserves_tables_and_raw_html() -> None:
    for pdf in PDF_NAMES:
        tables = read_json(P1_CLEAN_ROOT / pdf / "tables_clean.json")
        assert tables, pdf
        assert all(t["raw_html"].startswith("<table") for t in tables)
        assert all(t["embedding_text"].strip() for t in tables)


@pytest.mark.skipif(not _clean_present(), reason="P1-clean artifacts absent")
def test_p1_raw_unchanged_after_clean_generation() -> None:
    # The raw zip/content_list hashes recorded in P1-clean must match disk.
    for pdf in PDF_NAMES:
        manifest = read_json(P1_CLEAN_ROOT / pdf / "manifest.json")
        raw_dir = P1_RAW_ROOT / pdf / "mineru_raw"
        assert hashlib.sha256((raw_dir / "content_list.json").read_bytes()).hexdigest() == manifest["raw_content_list_sha256"]
        assert hashlib.sha256((raw_dir / "result.zip").read_bytes()).hexdigest() == manifest["raw_result_zip_sha256"]


@pytest.mark.skipif(not _clean_present(), reason="P1-clean artifacts absent")
def test_historical_old_p0_marked_non_fixed_model() -> None:
    hist = EXPERIMENT_ROOT / "historical" / "previous_p0_non_fixed_model" / "status.json"
    if not hist.is_file():
        pytest.skip("historical dir absent")
    status = read_json(hist)
    assert status["status"] == "Historical non-fixed-model baseline"
    assert status["not_used_for_final_comparison"] is True

"""Phase 3A parser-backend comparison: deterministic gates and experiment checks.

Pure-function tests always run. Artifact-dependent tests are skipped when the
experiment directories are absent. Real MinerU calls are opt-in only.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from evaluation.experiments.parser_backend.common import percentile, read_json, read_jsonl
from evaluation.experiments.parser_backend.config import (
    CHUNKER_CONFIG,
    GOLDEN_SET_PATH,
    LLM_LOCK,
    PDF_FACTS,
    PDF_NAMES,
    RETRIEVAL,
    group_dir,
)
from evaluation.experiments.parser_backend.metrics import (
    build_evidence_mapping,
    category_breakdown,
    citation_metrics,
    load_gold,
    retrieval_metrics,
)
from evaluation.experiments.parser_backend.quality import (
    chunk_stats,
    page_stats,
    text_stats,
)
from industrial_rag.parser_models import (
    BlockType,
    ChildChunk,
    ParentChunk,
    ParsedBlock,
)
from industrial_rag.structured_chunker import ChunkerConfig

EXPERIMENT_ROOT = Path(__file__).resolve().parents[1] / "evaluation" / "experiments" / "parser_backend"
GOLDEN_SHA256 = "fc52600fcce019d7f3cab04e0d0306ce336c468873ba2aef44391cc863e37aaf"
FIXED_ROOT = EXPERIMENT_ROOT / "fixed_model"


# ---------------------------------------------------------------------------
# Fixed experiment variables (no network)
# ---------------------------------------------------------------------------


def test_pdf_facts_are_frozen_and_match_files() -> None:
    for pdf_name, facts in PDF_FACTS.items():
        path = Path(str(facts["path"]))
        assert path.is_file(), pdf_name
        assert path.stat().st_size == facts["size"], pdf_name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == facts["sha256"], pdf_name


def test_chunker_config_is_single_and_frozen() -> None:
    assert ChunkerConfig(strategy="pymupdf-v1") == CHUNKER_CONFIG
    assert CHUNKER_CONFIG.strategy == "pymupdf-v1"
    assert CHUNKER_CONFIG.child_max_tokens == 700
    assert CHUNKER_CONFIG.parent_target_tokens == 1500


def test_retrieval_and_model_lock_are_fixed() -> None:
    assert RETRIEVAL["mode"] == "mix"
    assert RETRIEVAL["top_k"] == 12
    assert RETRIEVAL["chunk_top_k"] == 20
    assert RETRIEVAL["enable_rerank"] is False
    assert RETRIEVAL["evidence_limit"] == 3
    assert LLM_LOCK["embedding_model"] == "text-embedding-v4"
    assert LLM_LOCK["embedding_dim"] == 1024
    assert LLM_LOCK["llm_model"]


def test_golden_set_is_not_modified() -> None:
    digest = hashlib.sha256(GOLDEN_SET_PATH.read_bytes()).hexdigest()
    assert digest == GOLDEN_SHA256
    cases = load_gold()
    assert len(cases) == 50
    assert {c.case_id for c in cases} >= {f"S{i:03d}" for i in range(1, 21)} | {
        f"D{i:03d}" for i in range(1, 21)
    } | {f"C{i:03d}" for i in range(1, 9)} | {"N001", "N002"}


# ---------------------------------------------------------------------------
# Quality statistics functions
# ---------------------------------------------------------------------------


def _fake_blocks() -> list[dict]:
    return [
        ParsedBlock("b1", BlockType.heading, "第1章 安装", 1).to_dict(),
        ParsedBlock("b2", BlockType.paragraph, "内容A", 1).to_dict(),
        ParsedBlock("b3", BlockType.paragraph, "内容B", 1).to_dict(),
    ]


def test_page_stats_calculates_coverage_and_missing_pages(tmp_path: Path) -> None:
    pdf = tmp_path / "fake.pdf"
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    doc.new_page()
    doc.save(str(pdf))
    doc.close()
    stats = page_stats(pdf, _fake_blocks())
    assert stats["pdf_pages"] == 3
    assert stats["valid_parsed_pages"] == 1
    assert stats["missing_pages"] == [2, 3]
    assert 0 < stats["page_coverage"] < 1


def test_text_stats_counts_paragraphs_and_duplicates() -> None:
    blocks = [
        ParsedBlock("a", BlockType.paragraph, "第一段\n第二段", 1),
        ParsedBlock("b", BlockType.paragraph, "第一段", 2),
    ]
    stats = text_stats(blocks)
    assert stats["paragraphs"] == 3
    assert stats["duplicate_paragraphs"] == 1
    assert stats["cjk_chars"] > 0
    assert stats["mojibake_chars"] == 0


def test_chunk_stats_token_distribution_and_duplicates() -> None:
    parent = ParentChunk(
        parent_chunk_id="p1",
        document_id="doc1",
        document_name="a.pdf",
        content="x" * 500,
        token_count=100,
        child_chunk_ids=("c1", "c2"),
    )
    children = [
        ChildChunk("c1", "p1", "doc1", "a.pdf", content="相同", token_count=2),
        ChildChunk("c2", "p1", "doc1", "a.pdf", content="相同", token_count=2),
        ChildChunk("c3", "p1", "doc1", "a.pdf", content="z" * 300, token_count=80),
    ]
    stats = chunk_stats([parent], children)
    assert stats["duplicate_occurrences"] >= 1
    assert stats["child_token_max"] == 80
    assert stats["child_token_p50"] >= 2
    assert stats["child_under_120_tokens"] == 3


def test_percentile_helper() -> None:
    assert percentile([1, 2, 3, 4], 0.5) == 3
    assert percentile([5], 0.95) == 5


# ---------------------------------------------------------------------------
# Metrics functions
# ---------------------------------------------------------------------------


def _gold_case(case_id: str, page: int = 9, chunk: str = "gold-chunk") -> object:
    from industrial_rag.citation_formatter import Citation
    from industrial_rag.evaluation import GoldenCase

    return GoldenCase(
        case_id,
        "问题？",
        True,
        (Citation("2196-ANSI-Manual-Chinese.pdf", page, chunk),),
    )


def test_retrieval_metrics_recall_is_cumulative_and_mrr() -> None:

    gold = (_gold_case("S001"), _gold_case("S002", page=10, chunk="g2"), _gold_case("S003", page=11, chunk="g3"))
    mapping = {
        "entries": [
            {"case_id": "S001", "mapped": True, "mapped_child_ids": ["c1"], "source_file": "2196-ANSI-Manual-Chinese.pdf", "page_number": 9},
            {"case_id": "S002", "mapped": True, "mapped_child_ids": ["c2"], "source_file": "2196-ANSI-Manual-Chinese.pdf", "page_number": 10},
            {"case_id": "S003", "mapped": True, "mapped_child_ids": ["c3"], "source_file": "2196-ANSI-Manual-Chinese.pdf", "page_number": 11},
        ]
    }
    rows = [
        {"case_id": "S001", "retrieved": [{"file": "2196-ANSI-Manual-Chinese.pdf", "page": 9, "chunk_id": "c1"}]},
        {"case_id": "S002", "retrieved": [
            {"file": "2196-ANSI-Manual-Chinese.pdf", "page": 3, "chunk_id": "x"},
            {"file": "2196-ANSI-Manual-Chinese.pdf", "page": 10, "chunk_id": "c2"},
        ]},
        {"case_id": "S003", "retrieved": []},
    ]
    m = retrieval_metrics(rows, gold=gold, mapping=mapping)
    assert m["recall_at_1"] == pytest.approx(round(1 / 3, 4), abs=1e-4)
    assert m["recall_at_3"] == pytest.approx(round(2 / 3, 4), abs=1e-4)
    assert m["recall_at_5"] == pytest.approx(round(2 / 3, 4), abs=1e-4)
    assert m["mrr"] == pytest.approx(round((1.0 + 0.5 + 0.0) / 3, 4), abs=1e-4)
    assert m["gold_page_recall"] == pytest.approx(round(2 / 3, 4), abs=1e-4)
    assert m["no_result_rate"] == pytest.approx(round(1 / 3, 4), abs=1e-4)


def test_citation_metrics_deterministic() -> None:

    gold = (_gold_case("S001"), _gold_case("S002", page=10, chunk="g2"))
    rows = [
        {
            "case_id": "S001",
            "citations": [
                {"source_file": "2196-ANSI-Manual-Chinese.pdf", "page_number": 9, "chunk_id": "x"},
                {"source_file": "t1739cn.pdf", "page_number": 1, "chunk_id": "y"},
            ],
        },
        {"case_id": "S002", "citations": [{"source_file": "2196-ANSI-Manual-Chinese.pdf", "page_number": 3, "chunk_id": "z"}]},
    ]
    m = citation_metrics(rows, gold=gold)
    assert m["citation_precision"] == pytest.approx((0.5 + 0.0) / 2)
    assert m["citation_recall"] == pytest.approx((1.0 + 0.0) / 2)
    assert m["citation_accuracy"] == pytest.approx(0.5)
    assert m["citation_traceability"] == 1.0


def test_evidence_mapping_is_parser_specific_and_does_not_touch_gold() -> None:
    children = [
        {
            "document_name": "2196-ANSI-Manual-Chinese.pdf",
            "page_start": 9,
            "chunk_id": "cchunk-p1",
            "content": "将泵存放在清洁、干燥的地方。至少每周用手旋转泵轴一次。",
            "embedding_content": "将泵存放在清洁、干燥的地方。至少每周用手旋转泵轴一次。",
        }
    ]
    mapping = build_evidence_mapping(children)
    assert mapping["total_gold_citations"] == 70
    assert mapping["mapped_citations"] > 0
    assert mapping["mapping_rate"] > 0
    digest = hashlib.sha256(GOLDEN_SET_PATH.read_bytes()).hexdigest()
    assert digest == GOLDEN_SHA256


def test_category_breakdown_uses_gold_pages() -> None:

    gold = (_gold_case("S001"),)
    rows = [
        {
            "case_id": "S001",
            "retrieved": [{"file": "2196-ANSI-Manual-Chinese.pdf", "page": 9, "chunk_id": "c1"}],
        }
    ]
    breakdown = category_breakdown(rows, {}, gold=gold, mapping={"entries": []})
    assert breakdown["参数查询"]["question_count"] == 1
    assert breakdown["参数查询"]["gold_page_recall"] == 1.0


# ---------------------------------------------------------------------------
# Experiment artifact gates (skipped when the experiment is not present)
# ---------------------------------------------------------------------------


def _artifacts_present() -> bool:
    return all((group_dir("0", pdf) / "manifest.json").is_file() for pdf in PDF_NAMES) and all(
        (group_dir("1", pdf) / "manifest.json").is_file() for pdf in PDF_NAMES
    )


def _results_present() -> bool:
    return (
        (FIXED_ROOT / "P0_pymupdf" / "results.jsonl").is_file()
        and (FIXED_ROOT / "P1_mineru" / "results.jsonl").is_file()
        and (FIXED_ROOT / "comparison" / "evidence_mapping_p1.json").is_file()
    )


@pytest.mark.skipif(not _artifacts_present(), reason="parser_backend experiment artifacts absent")
def test_p0_manifest_uses_pymupdf_without_fallback() -> None:
    for pdf in PDF_NAMES:
        manifest = read_json(group_dir("0", pdf) / "manifest.json")
        assert manifest["parser_requested"] == "pymupdf"
        assert manifest["parser_used"] == "pymupdf"
        assert manifest["fallback_used"] is False
        assert manifest["pdf_sha256"] == PDF_FACTS[pdf]["sha256"]


@pytest.mark.skipif(not _artifacts_present(), reason="parser_backend experiment artifacts absent")
def test_p1_manifest_is_strict_mineru_and_records_zip_hash() -> None:
    for pdf in PDF_NAMES:
        manifest = read_json(group_dir("1", pdf) / "manifest.json")
        assert manifest["parser_requested"] == "mineru_online"
        assert manifest["parser_used"] == "mineru_online"
        assert manifest["fallback_used"] is False
        assert manifest["fallback_reason"] is None
        assert manifest["mineru_task_id"]
        assert len(manifest["result_zip_sha256"]) == 64
        assert manifest["content_list_sha256"]
        assert manifest["result_zip_bytes"] > 0


@pytest.mark.skipif(not _artifacts_present(), reason="parser_backend experiment artifacts absent")
def test_p0_and_p1_chunker_config_identical() -> None:
    strategies = set()
    versions = set()
    for group in ("0", "1"):
        for pdf in PDF_NAMES:
            manifest = read_json(group_dir(group, pdf) / "manifest.json")
            strategies.add(manifest["chunker_strategy"])
            versions.add(manifest["chunker_version"])
            if "chunker_config" in manifest:
                assert manifest["chunker_config"]["child_max_tokens"] == 700
    assert strategies == {"pymupdf-v1"}
    assert len(versions) == 1


@pytest.mark.skipif(not _results_present(), reason="parser_backend retrieval results absent")
def test_experiment_results_and_metrics_exist_for_both_groups() -> None:
    for label in ("P0_pymupdf", "P1_mineru"):
        results = read_jsonl(FIXED_ROOT / label / "results.jsonl")
        assert len(results) == 50
        metrics = read_json(FIXED_ROOT / label / "metrics.json")
        assert metrics["retrieval"]["evidence_case_count"] == 48
        assert metrics["retrieval"]["recall_at_1"] <= metrics["retrieval"]["recall_at_3"] + 1e-9
        assert metrics["retrieval"]["recall_at_3"] <= metrics["retrieval"]["recall_at_5"] + 1e-9
        assert metrics["llm"]["model_mismatches"] == 0
        assert metrics["llm"]["all_actual_model"] == ["qwen-plus-2025-07-28"]


@pytest.mark.skipif(not _results_present(), reason="parser_backend retrieval results absent")
def test_evidence_mapping_files_are_independent_per_parser() -> None:
    for group in ("0", "1"):
        mapping = read_json(FIXED_ROOT / "comparison" / f"evidence_mapping_p{group}.json")
        assert mapping["total_gold_citations"] == 70
        assert mapping["mapping_rate"] > 0.5
        assert mapping["exact_mapped"] + mapping["fuzzy_mapped"] + mapping["unmapped"] == 70


@pytest.mark.skipif(not _results_present(), reason="parser_backend retrieval results absent")
def test_retrieval_groups_use_distinct_qdrant_prefixes_in_records() -> None:
    # Distinct KBs/generations must own disjoint Qdrant collection sets.
    names0 = set(read_json(FIXED_ROOT / "P0_pymupdf" / "metrics.json")["index"]["check"]["names"].values())
    names1 = set(read_json(FIXED_ROOT / "P1_mineru" / "metrics.json")["index"]["check"]["names"].values())
    assert names0 and names1
    assert names0.isdisjoint(names1)


# ---------------------------------------------------------------------------
# Real MinerU opt-in smoke (never runs in CI without explicit opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("IRA_MINERU_REAL"),
    reason="Real MinerU call is opt-in via IRA_MINERU_REAL=1",
)
@pytest.mark.asyncio
async def test_real_mineru_smoke_strict() -> None:
    from industrial_rag.config import Settings
    from industrial_rag.mineru_client import MinerUClient, MinerUClientConfig

    settings = Settings.from_env()
    config = MinerUClientConfig(
        api_base_url=settings.mineru_api_base_url,
        api_key=settings.mineru_api_key,
        api_version=settings.mineru_api_version,
    )
    pdf = Path(str(PDF_FACTS["2196-ANSI-Manual-Chinese.pdf"]["path"]))
    file_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
    async with MinerUClient(config) as client:
        resp = await client._request_with_retry(
            "POST",
            "/api/v4/file-urls/batch",
            json={"files": [{"name": pdf.name, "data_id": file_hash}], "model_version": config.model_version},
        )
        data = resp.get("data", {})
        assert data.get("batch_id")
        pages = await client.extract(str(pdf), output_dir=None)
    assert pages is not None

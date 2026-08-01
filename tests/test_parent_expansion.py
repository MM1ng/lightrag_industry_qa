"""Phase 4C: deterministic Parent Expansion ablation tests (offline, no LLM)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from evaluation.experiments.phase4.parent_expansion.config import (
    EXPANSION_CONFIG,
    FIXED_MODEL,
    GOLDEN_SET_PATH,
    GOLDEN_SHA256,
)
from evaluation.experiments.phase4.parent_expansion.context_builder import build_context
from evaluation.experiments.phase4.parent_expansion.expander import expand
from evaluation.experiments.phase4.parent_expansion.metrics import (
    context_evidence_density,
    paired_bootstrap,
    percentile,
)
from evaluation.experiments.phase4.parent_expansion.parent_loader import ParentLoader
from evaluation.experiments.phase4.parent_expansion.provenance import provenance_rows

EXPERIMENT_ROOT = Path(__file__).resolve().parents[1] / "evaluation" / "experiments" / "phase4"
PE_ROOT = EXPERIMENT_ROOT / "parent_expansion"


# ---------------------------------------------------------------------------
# Frozen config / isolation
# ---------------------------------------------------------------------------


def test_fixed_config_declares_pymupdf_only() -> None:
    assert EXPANSION_CONFIG["parser_pipeline"] == "pymupdf_standard_adapter"
    assert EXPANSION_CONFIG["query_mode"] == "mix"
    assert EXPANSION_CONFIG["top_k"] == 12
    assert EXPANSION_CONFIG["chunk_top_k"] == 20
    assert EXPANSION_CONFIG["rerank"] is False
    assert EXPANSION_CONFIG["max_parents"] == 3
    assert EXPANSION_CONFIG["max_context_tokens"] == 6000
    assert EXPANSION_CONFIG["parent_expansion_strategies"] == [
        "none",
        "top_1_parent",
        "top_3_parents",
        "adaptive",
    ]


def test_baseline_manifest_and_golden_hash_unchanged() -> None:
    manifest = json.loads(
        (EXPERIMENT_ROOT / "baseline_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["source_phase"] == "Phase 3A-R-Paid"
    assert manifest["default_parser_pipeline"] == "pymupdf_standard_adapter"
    assert hashlib.sha256(GOLDEN_SET_PATH.read_bytes()).hexdigest() == GOLDEN_SHA256
    assert manifest["golden_set_sha256"] == GOLDEN_SHA256
    assert manifest["model"] == FIXED_MODEL
    assert manifest["model_fallback_enabled"] is False


def test_frozen_child_results_hash_is_stable_if_present() -> None:
    path = PE_ROOT / "frozen_child_results.jsonl"
    if not path.is_file():
        pytest.skip("frozen child results absent")
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows
    by_q = {r["question_id"] for r in rows}
    assert len(by_q) == 50
    # Phase 4D-R2 variable-size contract: C007=19, N001=20, N002=19 are all
    # valid frozen inputs; negative questions keep their real candidates.
    from collections import Counter

    counts = Counter(r["question_id"] for r in rows)
    assert counts.get("N001", 0) == 20
    assert counts.get("N002", 0) == 19
    assert counts.get("C007", 0) == 19
    for r in rows:
        assert r["query_mode"] == "mix"
        assert r["top_k"] == 12
        assert r["chunk_top_k"] == 20


# ---------------------------------------------------------------------------
# Parent mapping
# ---------------------------------------------------------------------------


def _child(**overrides) -> dict:
    child = {
        "chunk_id": "c1",
        "parent_chunk_id": "p1",
        "document_name": "a.pdf",
        "page_start": 5,
        "content": "内容",
        "embedding_content": "内容",
        "token_count": 2,
    }
    child.update(overrides)
    return child


def test_parent_loader_requires_same_document(tmp_path: Path) -> None:
    parents_dir = tmp_path / "parents"
    pdf = "2196-ANSI-Manual-Chinese.pdf"
    (parents_dir / pdf).mkdir(parents=True)
    (parents_dir / pdf / "parent_chunks.jsonl").write_text(
        json.dumps(
            {
                "parent_chunk_id": "p1",
                "document_id": "doc-a",
                "document_name": pdf,
                "page_start": 1,
                "page_end": 2,
                "section_path": [],
                "section_title": None,
                "content_type": "normal_text",
                "content": "parent text",
                "token_count": 3,
                "source_hash": "h",
                "child_chunk_ids": ["c1"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    loader = ParentLoader(parents_dir)
    assert loader.get("p1") is not None
    assert loader.get_for_child(_child(document_name=pdf)) is not None
    assert loader.get_for_child(_child(document_name="t1739cn.pdf")) is None
    assert loader.get_for_child(_child(parent_chunk_id="missing")) is None


# ---------------------------------------------------------------------------
# Expansion strategies
# ---------------------------------------------------------------------------


def _children(n: int = 5) -> list[dict]:
    return [
        _child(
            chunk_id=f"c{i}",
            parent_chunk_id=f"p{i % 3}",
            page_start=10 + i,
            content=f"内容{i}",
            embedding_content=f"内容{i}",
            token_count=10,
        )
        for i in range(1, n + 1)
    ]


class _FakeLoader:
    def __init__(self) -> None:
        from industrial_rag.parser_models import ContentType, ParentChunk

        self.parents = {
            "p0": ParentChunk("p0", "doc-a", "a.pdf", content="P0", token_count=50, page_start=1, page_end=3, content_type=ContentType.normal_text),
            "p1": ParentChunk("p1", "doc-a", "a.pdf", content="P1", token_count=50, page_start=1, page_end=2, content_type=ContentType.normal_text),
            "p2": ParentChunk("p2", "doc-a", "a.pdf", content="P2", token_count=50, page_start=4, page_end=4, content_type=ContentType.normal_text),
        }

    def get(self, parent_id: str):
        return self.parents.get(parent_id)

    def get_for_child(self, child: dict):
        parent = self.parents.get(child.get("parent_chunk_id"))
        if parent is None or parent.document_name != child.get("document_name"):
            return None
        return parent


def test_none_strategy_never_loads_parents() -> None:
    rows = expand("q1", _children(), strategy="none", loader=_FakeLoader())
    assert all(r.parent_id is None for r in rows)
    assert [r.child_rank for r in rows] == [1, 2, 3, 4, 5]


def test_top_1_parent_loads_only_rank1_parent() -> None:
    rows = expand("q1", _children(), strategy="top_1_parent", loader=_FakeLoader())
    parents = [r.parent_id for r in rows if r.parent_id]
    assert parents == ["p1"]


def test_top_3_parents_deduplicates_and_caps() -> None:
    rows = expand("q1", _children(), strategy="top_3_parents", loader=_FakeLoader())
    parents = [r.parent_id for r in rows if r.parent_id]
    assert len(set(parents)) == 3
    assert parents == ["p1", "p2", "p0"]
    # children all preserved with original ranks
    child_rows = [r for r in rows if not r.parent_id]
    assert [r.child_rank for r in child_rows] == [1, 2, 3, 4, 5]
    assert all(r.included for r in child_rows)


def test_adaptive_respects_budget_and_does_not_drop_children() -> None:
    rows = expand(
        "q1",
        _children(),
        strategy="adaptive",
        loader=_FakeLoader(),
        max_parents=3,
        max_context_tokens=80,
    )
    parents = [r.parent_id for r in rows if r.parent_id]
    assert len(parents) <= 3
    assert all(r.included for r in rows)
    assert any(r.exclusion_reason == "budget_exceeded" for r in rows) or len(parents) == 3


def test_children_scores_and_ranks_never_change() -> None:
    children = _children()
    for strategy in ("none", "top_1_parent", "top_3_parents", "adaptive"):
        rows = expand("q1", children, strategy=strategy, loader=_FakeLoader())
        child_rows = [r for r in rows if not r.parent_id]
        assert [r.child_chunk_id for r in child_rows] == ["c1", "c2", "c3", "c4", "c5"]
        assert [r.child_rank for r in child_rows] == [1, 2, 3, 4, 5]
        assert all(r.child_score is None for r in child_rows)


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------


def test_context_builder_preserves_children_and_budget(tmp_path: Path) -> None:
    rows = expand("q1", _children(3), strategy="adaptive", loader=_FakeLoader(), max_context_tokens=1000)
    context = build_context(rows, max_context_tokens=1000)
    assert context["token_count"] > 0
    assert context["parent_count"] == 3
    assert "P1" in context["context"]


def test_context_builder_duplicate_token_stats_deterministic() -> None:
    rows = expand("q1", _children(2), strategy="top_1_parent", loader=_FakeLoader())
    c1 = build_context(rows, max_context_tokens=6000)
    c2 = build_context(rows, max_context_tokens=6000)
    assert c1 == c2
    assert c1["duplicate_ratio"] >= 0


def test_provenance_keeps_child_citation_page() -> None:
    rows = expand("q1", _children(1), strategy="top_1_parent", loader=_FakeLoader())
    prov = provenance_rows(rows)
    row = next(item for item in prov if item["source_parent_id"] is None)
    assert row["citation_page"] == 11
    assert row["actual_evidence_page"] == 11
    parent_row = next(item for item in prov if item["source_parent_id"] == "p1")
    assert parent_row["context_page_range"] == [1, 2]
    assert parent_row["citation_page"] == 11  # citation stays on the child page


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_density_and_percentile_helpers() -> None:
    assert context_evidence_density("轴承 温度 150", ["轴承温度150"]) > 0
    assert percentile([1, 2, 3, 4], 0.5) == 3


def test_paired_bootstrap_is_seeded_deterministic() -> None:
    base = [1.0] * 24 + [0.0] * 24
    cand = [1.0] * 28 + [0.0] * 20
    a = paired_bootstrap(base, cand, seed=42)
    b = paired_bootstrap(base, cand, seed=42)
    assert a == b
    assert a["mean_diff"] == pytest.approx(round(4 / 48, 4), abs=1e-4)


def test_final_decision_files_exist_if_experiment_finished() -> None:
    final = PE_ROOT / "final_parent_expansion.json"
    manifest = PE_ROOT / "manifests" / "result_manifest.json"
    if not final.is_file() or not manifest.is_file():
        pytest.skip("final experiment outputs absent")
    decision = json.loads(final.read_text(encoding="utf-8"))
    assert decision["parser_pipeline"] == "pymupdf_standard_adapter"
    assert decision["parent_expansion"] in {"none", "top_1_parent", "top_3_parents", "adaptive"}
    assert decision["replacement_gates_passed"] in (True, False)

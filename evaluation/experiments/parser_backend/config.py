"""Locked Phase 3A experiment configuration (single source of truth)."""

from __future__ import annotations

from pathlib import Path

from industrial_rag.structured_chunker import ChunkerConfig

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_ROOT = Path(__file__).resolve().parent

PDF_FACTS: dict[str, dict[str, object]] = {
    "2196-ANSI-Manual-Chinese.pdf": {
        "path": str(PROJECT_ROOT / "data" / "manuals" / "2196-ANSI-Manual-Chinese.pdf"),
        "size": 1561387,
        "sha256": "e0f80874dd923d03ea15584f4fe25046ba184675062d6d16e1decafa2a6c8700",
        "pages": 55,
        "encrypted": False,
    },
    "t1739cn.pdf": {
        "path": str(PROJECT_ROOT / "data" / "manuals" / "t1739cn.pdf"),
        "size": 4532306,
        "sha256": "77fd7ebf86ef6c574de11eac446dc321de04fd0773cd7d844287da7fa4d6c4ae",
        "pages": 62,
        "encrypted": False,
    },
}

PDF_NAMES: tuple[str, ...] = tuple(PDF_FACTS)

GOLDEN_SET_PATH = PROJECT_ROOT / "data" / "evaluation" / "industrial_pump_golden_set_50.jsonl"
GOLDEN_DOCUMENTS_JSONL = PROJECT_ROOT / "data" / "processed" / "documents.jsonl"

# Same chunker for both parser groups — only the parser changes.
CHUNKER_CONFIG = ChunkerConfig(strategy="pymupdf-v1")

RETRIEVAL = {
    "mode": "mix",
    "top_k": 12,
    "chunk_top_k": 20,
    "enable_rerank": False,
    "evidence_limit": 3,
    "chunk_token_size": 2000,
}

# Locked model config: kimi / qwen3.6-plus / qwen3.6-flash / qwen-plus /
# qwen-turbo are quota-blocked (verified 2026-08-01), so both groups use
# qwen3.5-flash-2026-02-23 (the only model still returning 200).
LLM_LOCK = {
    "llm_model": "qwen3.5-flash-2026-02-23",
    "llm_fallback_models": ("qwen-turbo",),
    "embedding_model": "text-embedding-v4",
    "embedding_dim": 1024,
}

QDRANT_TEST_URL = "http://127.0.0.1:16333"


def group_dir(group: str, pdf_name: str | None = None) -> Path:
    """Return the isolated artifact directory for a parser group."""
    root = EXPERIMENT_ROOT / f"P{group}"
    return root / pdf_name if pdf_name else root


def retrieval_dir(group: str) -> Path:
    return EXPERIMENT_ROOT / "retrieval" / f"pymupdf_qdrant" if group == "0" else EXPERIMENT_ROOT / "retrieval" / "mineru_qdrant"


def comparison_dir() -> Path:
    return EXPERIMENT_ROOT / "comparison"


# Manual per-question categories (reviewed 2026-08-01, based on question content).
QUESTION_CATEGORIES: dict[str, str] = {
    "S001": "参数查询", "S002": "参数查询", "S003": "参数查询", "S004": "参数查询",
    "S005": "参数查询", "S006": "参数查询", "S007": "表格查询", "S008": "参数查询",
    "S009": "表格查询", "S010": "参数查询", "S011": "操作步骤", "S012": "操作步骤",
    "S013": "参数查询", "S014": "操作步骤", "S015": "故障诊断", "S016": "故障诊断",
    "S017": "安全警告", "S018": "参数查询", "S019": "参数查询", "S020": "操作步骤",
    "D001": "普通事实", "D002": "普通事实", "D003": "安全警告", "D004": "参数查询",
    "D005": "安全警告", "D006": "操作步骤", "D007": "安全警告", "D008": "参数查询",
    "D009": "参数查询", "D010": "参数查询", "D011": "参数查询", "D012": "参数查询",
    "D013": "参数查询", "D014": "参数查询", "D015": "参数查询", "D016": "操作步骤",
    "D017": "表格查询", "D018": "操作步骤", "D019": "操作步骤", "D020": "操作步骤",
    "C001": "跨页问题", "C002": "跨页问题", "C003": "跨页问题", "C004": "跨页问题",
    "C005": "跨页问题", "C006": "跨页问题", "C007": "安全警告", "C008": "故障诊断",
    "N001": "证据不足", "N002": "证据不足",
}

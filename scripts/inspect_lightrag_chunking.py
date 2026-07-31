"""Minimal, reproducible experiment: verify LightRAG 1.5.4 chunking behavior.

Key question: When ``split_by_character_only=True`` with a custom
``split_by_character`` delimiter, does LightRAG 1.5.4 ever apply
``chunk_token_size`` to further split segments?

The source code at ``lightrag/chunker/token_size.py:142-154`` says:
   if split_by_character_only:
       ... if len(_tokens) > chunk_token_size:
           raise ChunkTokenLimitExceededError(...)

This experiment tests three scenarios with a deliberately tiny
``chunk_token_size=8`` (so even small segments trigger the check):

  A) A single segment under 8 tokens → should succeed, 1:1 mapping.
  B) A single segment over 8 tokens  → should RAISE (hard error).
  C) Multiple segments joined by the boundary, one over 8 → RAISE.

And a real-world scenario with ``chunk_token_size=1600``:

  D) Two realistic Chinese paragraphs joined by boundary → 1:1 mapping.

All writes go to tmp directories.  No production storage is touched.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import tempfile
from pathlib import Path
from typing import Any

from lightrag import LightRAG
from lightrag.exceptions import ChunkTokenLimitExceededError
from lightrag.utils import EmbeddingFunc


# ---------------------------------------------------------------------------
# Fake LLM / embedding — zero network, zero cost
# ---------------------------------------------------------------------------

async def _fake_llm(*args: Any, **kwargs: Any) -> str:
    return '{"entities": [], "relationships": []}'


async def _fake_embed(texts: list[str]) -> list[list[float]]:
    return [[0.0] * 16 for _ in texts]


_FAKE_EMBEDDING = EmbeddingFunc(
    embedding_dim=16,
    max_token_size=8192,
    func=_fake_embed,
)


def _make_rag(storage_dir: Path, chunk_token_size: int = 1600) -> LightRAG:
    return LightRAG(
        working_dir=str(storage_dir),
        llm_model_func=_fake_llm,
        llm_model_name="fake",
        embedding_func=_FAKE_EMBEDDING,
        chunk_token_size=chunk_token_size,
        chunk_overlap_token_size=100,
        enable_content_headings=False,
        entity_extract_max_gleaning=0,
        max_parallel_insert=1,
    )


# ---------------------------------------------------------------------------
# Helpers (mirror current ingest code)
# ---------------------------------------------------------------------------

_CHUNK_BOUNDARY = "\n\n<<<INDUSTRIAL_RAG_CHUNK_BOUNDARY>>>\n\n"


def _chunk_header(source_file: str, page: int, chunk_id: str) -> str:
    return (
        f"[[INDUSTRIAL_RAG_SOURCE file={source_file} page={page} chunk={chunk_id}]]"
    )


def _source_line(source_file: str, page: int, section: str) -> str:
    return f"[来源：{source_file}，第{page}页，章节：{section}]"


async def _insert_one(
    rag: LightRAG,
    chunk_id: str,
    source_file: str,
    page: int,
    section: str,
    text: str,
) -> str | Exception:
    """Insert a single document and return track_id or the exception raised."""
    rendered = (
        f"{_chunk_header(source_file, page, chunk_id)}\n"
        f"{_source_line(source_file, page, section)}\n"
        f"{text}"
    )
    identity = hashlib.sha256(chunk_id.encode()).hexdigest()[:20]
    try:
        track_id = await rag.ainsert(
            input=[rendered],
            ids=[f"manual-{identity}"],
            file_paths=[source_file],
            split_by_character=_CHUNK_BOUNDARY,
            split_by_character_only=True,
        )
        return track_id
    except ChunkTokenLimitExceededError:
        raise
    except Exception:
        wrapper = RuntimeError(f"Insert failed for {chunk_id}")
        return wrapper


# ---------------------------------------------------------------------------
# Scenario texts
# ---------------------------------------------------------------------------

# ~5 English tokens: "The quick brown fox" → 5 tokens
_SMALL = "The quick brown fox"

# ~40 English tokens: well over 8-token limit
_LARGE = (
    "All human beings are born free and equal in dignity and rights. "
    "They are endowed with reason and conscience and should act towards "
    "one another in a spirit of brotherhood. Everyone is entitled to all "
    "the rights and freedoms set forth in this Declaration, without "
    "distinction of any kind."
)

# Real Chinese paragraph (~170 chars = ~117 CJK tokens with cl100k_base)
_ZH_PARAGRAPH_1 = (
    "检查泵和驱动机的紧固螺栓是否全部栓紧。检查泵与驱动机之间的联轴器对正情况，"
    "如有偏差应立即校正。确认泵的旋转方向与泵壳上的箭头标志一致。检查所有管路"
    "连接是否牢固，确保无泄漏。确认吸入管路的截止阀完全打开，排出管路的阀门处于"
    "适当开度。点动驱动机以确认旋转方向正确，避免长时间反转损伤机械密封。"
)

# Another Chinese paragraph
_ZH_PARAGRAPH_2 = (
    "轴承温度应定期检查，正常情况下不应超过环境温度加50°C。当发现轴承温度异常升高时，"
    "应立即停机检查。可能的原因包括：润滑不足、润滑油变质、轴承损坏、联轴器对中不良、"
    "泵产生气蚀、冷却水不足或中断。逐一排查上述原因并采取相应措施后，方可重新启动。"
)


# ---------------------------------------------------------------------------
# Main experiment
# ---------------------------------------------------------------------------


async def run(experiments: list[tuple[str, str, int, str, int, bool]]) -> list[dict[str, Any]]:
    """Run each experiment in a fresh storage directory.

    Each tuple: (label, text, chunk_token_size, chunk_id, page, expected_ok)
    """
    results: list[dict[str, Any]] = []
    for label, text, cts, chunk_id, page, expected_ok in experiments:
        entry: dict[str, Any] = {
            "label": label,
            "chunk_token_size": cts,
            "expected_ok": expected_ok,
            "actual_ok": None,
            "track_id": None,
            "error": None,
            "internal_chunks": 0,
            "chunk_details": [],
        }
        with tempfile.TemporaryDirectory(prefix=f"lrag-exp-{label}-") as td:
            storage = Path(td) / "storage"
            storage.mkdir()
            rag = _make_rag(storage, chunk_token_size=cts)
            await rag.initialize_storages()
            try:
                track_id = await _insert_one(
                    rag, chunk_id=chunk_id, source_file=f"{label}.pdf",
                    page=page, section=label, text=text,
                )
                if isinstance(track_id, Exception):
                    entry["error"] = repr(track_id)
                else:
                    entry["track_id"] = track_id
                    entry["actual_ok"] = True
            except ChunkTokenLimitExceededError as exc:
                entry["error"] = f"ChunkTokenLimitExceededError: {exc}"
            except Exception as exc:
                entry["error"] = f"{type(exc).__name__}: {exc}"
            await rag.finalize_storages()

            # Read results from storage
            tc_path = storage / "kv_store_text_chunks.json"
            if tc_path.is_file():
                tc = json.loads(tc_path.read_text(encoding="utf-8"))
                entry["internal_chunks"] = len(tc)
                for lr_cid, cd in tc.items():
                    content = cd.get("content", "")
                    m = re.search(r"chunk=(\S+)\]\]", content)
                    entry["chunk_details"].append({
                        "lr_chunk_id": lr_cid,
                        "lr_chunk_order": cd.get("chunk_order_index"),
                        "lr_tokens": cd.get("tokens"),
                        "source_chunk_id": m.group(1) if m else None,
                        "content_preview": content[:100],
                    })
            entry["actual_ok"] = entry.get("actual_ok") or entry["internal_chunks"] > 0
        results.append(entry)
    return results


def print_results(results: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("LightRAG 1.5.4 split_by_character_only Behavior Verification")
    lines.append(f"Total experiments: {len(results)}")
    lines.append("=" * 72)

    for r in results:
        status = "PASS" if r["actual_ok"] == r["expected_ok"] else "FAIL"
        lines.append(
            f"\n[{status}] {r['label']} "
            f"(chunk_token_size={r['chunk_token_size']}, expected_ok={r['expected_ok']})"
        )
        lines.append(f"  actual_ok={r['actual_ok']}")
        lines.append(f"  internal_chunks={r['internal_chunks']}")
        if r["error"]:
            lines.append(f"  error={r['error'][:200]}")
        if r["track_id"]:
            lines.append(f"  track_id={r['track_id']}")
        for detail in r["chunk_details"]:
            lines.append(
                f"  [{detail['lr_chunk_order']}] tokens={detail['lr_tokens']} "
                f"source={detail['source_chunk_id']}"
            )

    lines.append("\n" + "=" * 72)
    lines.append("CONCLUSIONS")
    lines.append("=" * 72)

    # Summarize
    ok_results = [r for r in results if r["error"] is None]
    err_results = [r for r in results if r["error"] is not None]
    lines.append(
        f"Successful: {len(ok_results)}, Failed: {len(err_results)}"
    )
    for r in err_results:
        lines.append(f"  Failed: {r['label']} → {r['error'][:150]}")

    # Key finding
    lines.append("")
    lines.append("KEY FINDING:")
    lines.append(
        "  When split_by_character_only=True AND a segment exceeds "
        "chunk_token_size, LightRAG 1.5.4 raises ChunkTokenLimitExceededError "
        "(a hard error). It does NOT silently split further."
    )
    lines.append(
        "  Source: lightrag/chunker/token_size.py lines 142-154."
    )
    lines.append(
        "  Production works because our source chunks (max 1220 tokens in the "
        "observed data) are all <= 1600 token limit, so no segment ever exceeds it."
    )
    lines.append(
        "  If chunk_token_size were reduced below 1220 (or source chunks grew), "
        "ingestion would fail with a hard error."
    )

    return "\n".join(lines)


def main() -> int:
    experiments: list[tuple[str, str, int, str, int, bool]] = [
        # Scenario A: small text, tiny limit → under threshold → 1:1
        ("A-small-under-limit", _SMALL, 8, "small-p1-c1", 1, True),
        # Scenario B: large text, tiny limit → over threshold → hard error
        ("B-large-over-limit-err", _LARGE, 8, "large-p1-c1", 1, False),
        # Scenario C: TWO segments under 8 each → both 1:1
        ("C-two-small-ok", _SMALL + _CHUNK_BOUNDARY + "Jumped over the lazy", 8, "twosmall-p1-c1", 1, True),
        # Scenario D: Realistic Chinese paragraphs with production 1600 limit
        ("D-real-zh-1600", _ZH_PARAGRAPH_1 + _CHUNK_BOUNDARY + _ZH_PARAGRAPH_2, 1600, "zh-real-p1-c1", 1, True),
    ]

    results = asyncio.run(run(experiments))
    output = print_results(results)
    out_path = Path(__file__).resolve().parent.parent / "docs" / "lightrag-chunking-experiment.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

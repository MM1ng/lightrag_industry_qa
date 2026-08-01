"""P1: MinerU Online strict -> ParsedBlock -> StructuredChunker -> Parent/Child.

Strict mode: any MinerU failure raises; PyMuPDF fallback is never used.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from industrial_rag.document_parser import DocumentChunk
from industrial_rag.mineru_client import MinerUClient, MinerUClientConfig, MinerUValidationError
from industrial_rag.services.parse_service import (
    _extract_pages_from_mineru_zip,
    _mineru_markdown_to_source_chunks,
)
from industrial_rag.structured_chunker import (
    build_parent_child_chunks,
    pymupdf_chunks_to_blocks,
)

from .common import plain, sha256_bytes, write_json, write_jsonl
from .config import CHUNKER_CONFIG, PDF_FACTS, PDF_NAMES, group_dir
from .quality import chunk_stats, page_stats, structure_stats, text_stats


def _git_head() -> str:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=Path.cwd()
            )
            .stdout.strip()
        )
    except Exception:
        return "unknown"


def _extract_content_list(raw: bytes) -> tuple[bytes, list[dict[str, Any]]]:
    with ZipFile(io.BytesIO(raw)) as archive:
        names = [n for n in archive.namelist() if n.endswith("_content_list.json")]
        if len(names) != 1:
            raise MinerUValidationError("MinerU archive content_list.json missing/unexpected")
        content_raw = archive.read(names[0])
    items = json.loads(content_raw.decode("utf-8"))
    return content_raw, items


async def parse_one_pdf(
    pdf_name: str,
    *,
    settings: Any,
    task_timeout: float = 900.0,
) -> dict[str, Any]:
    facts = PDF_FACTS[pdf_name]
    pdf_path = Path(str(facts["path"]))
    out = group_dir("1", pdf_name)
    raw_dir = out / "mineru_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    config = MinerUClientConfig(
        api_base_url=settings.mineru_api_base_url,
        api_key=settings.mineru_api_key,
        api_version=settings.mineru_api_version,
        request_timeout=settings.mineru_request_timeout,
        task_timeout=task_timeout,
        poll_interval=settings.mineru_poll_interval,
        max_retries=settings.mineru_max_retries,
    )
    pdf_bytes = pdf_path.read_bytes()
    file_hash = hashlib.sha256(pdf_bytes).hexdigest()
    assert file_hash == facts["sha256"], f"PDF hash mismatch for {pdf_name}"

    timeline: dict[str, float] = {}
    started = time.monotonic()
    async with MinerUClient(config) as client:
        timeline["submit_start"] = time.monotonic()
        resp = await client._request_with_retry(
            "POST", "/api/v4/file-urls/batch",
            json={"files": [{"name": pdf_path.name, "data_id": file_hash}], "model_version": config.model_version},
        )
        timeline["submit_done"] = time.monotonic()
        data = resp.get("data", {})
        batch_id = data.get("batch_id")
        upload_urls = data.get("file_urls")
        if not isinstance(batch_id, str) or not isinstance(upload_urls, list) or len(upload_urls) != 1:
            raise MinerUValidationError("MinerU batch response invalid")

        import httpx

        timeline["upload_start"] = time.monotonic()
        async with httpx.AsyncClient(timeout=httpx.Timeout(config.request_timeout)) as put:
            put_resp = await put.put(
                upload_urls[0],
                content=pdf_bytes,
                headers={"Content-Length": str(len(pdf_bytes))},
            )
        if put_resp.status_code < 200 or put_resp.status_code >= 300:
            raise MinerUValidationError(f"MinerU upload failed HTTP {put_resp.status_code}")
        timeline["upload_done"] = time.monotonic()

        result_url = f"/api/v4/extract-results/batch/{batch_id}"
        poll_count = 0
        archive_url: str | None = None
        deadline = time.monotonic() + task_timeout
        while time.monotonic() < deadline:
            result_resp = await client._request_with_retry("GET", result_url)
            poll_count += 1
            extract = (result_resp.get("data", {}) or {}).get("extract_result") or []
            if not extract:
                continue
            er = extract[0]
            state = er.get("state")
            if state == "done":
                archive_url = er.get("full_zip_url")
                if not isinstance(archive_url, str):
                    raise MinerUValidationError("MinerU full_zip_url missing")
                break
            if state == "failed":
                raise MinerUValidationError(f"MinerU task failed: {er.get('err_msg')}")
            await asyncio.sleep(config.poll_interval)
        if not archive_url:
            raise MinerUValidationError("MinerU task timed out")

        timeline["download_start"] = time.monotonic()
        saved = await client.download_result(archive_url, output_dir=raw_dir)
        timeline["download_done"] = time.monotonic()

    raw = saved.read_bytes()
    zip_sha = sha256_bytes(raw)
    content_raw, items = _extract_content_list(raw)
    content_sha = sha256_bytes(content_raw)
    (raw_dir / "result.zip").write_bytes(raw)
    (raw_dir / "content_list.json").write_bytes(content_raw)
    pages = _extract_pages_from_mineru_zip(raw)
    (raw_dir / "pages.json").write_text(
        json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    source_chunks: list[DocumentChunk] = _mineru_markdown_to_source_chunks(pages, pdf_name)
    blocks = pymupdf_chunks_to_blocks(source_chunks, pdf_name)
    parents, children = build_parent_child_chunks(blocks, pdf_name, config=CHUNKER_CONFIG)
    total_seconds = round(time.monotonic() - started, 3)

    write_jsonl(out / "blocks.jsonl", [plain(b.to_dict()) for b in blocks])
    write_jsonl(out / "parent_chunks.jsonl", [plain(asdict(p)) for p in parents])
    write_jsonl(out / "child_chunks.jsonl", [plain(c.to_dict()) for c in children])

    manifest = {
        "parser_requested": "mineru_online",
        "parser_used": "mineru_online",
        "fallback_used": False,
        "fallback_reason": None,
        "pdf_name": pdf_name,
        "pdf_size": facts["size"],
        "pdf_sha256": facts["sha256"],
        "pdf_pages": facts["pages"],
        "pdf_encrypted": facts["encrypted"],
        "mineru_task_id": batch_id,
        "result_zip_sha256": zip_sha,
        "content_list_sha256": content_sha,
        "result_zip_bytes": len(raw),
        "poll_count": poll_count,
        "raw_pages": len(pages),
        "content_items": len(items),
        "source_chunk_count": len(source_chunks),
        "block_count": len(blocks),
        "parent_count": len(parents),
        "child_count": len(children),
        "submit_seconds": round(timeline["submit_done"] - timeline["submit_start"], 3),
        "upload_seconds": round(timeline["upload_done"] - timeline["upload_start"], 3),
        "download_seconds": round(timeline["download_done"] - timeline["download_start"], 3),
        "server_seconds": round(timeline["download_start"] - timeline["upload_done"], 3),
        "total_seconds": total_seconds,
        "chunker_strategy": CHUNKER_CONFIG.strategy,
        "chunker_version": CHUNKER_CONFIG.version,
        "chunker_config": {
            "parent_target_tokens": CHUNKER_CONFIG.parent_target_tokens,
            "parent_max_tokens": CHUNKER_CONFIG.parent_max_tokens,
            "child_target_tokens": CHUNKER_CONFIG.child_target_tokens,
            "child_min_tokens": CHUNKER_CONFIG.child_min_tokens,
            "child_max_tokens": CHUNKER_CONFIG.child_max_tokens,
            "child_overlap_tokens": CHUNKER_CONFIG.child_overlap_tokens,
            "merge_small_children": CHUNKER_CONFIG.merge_small_children,
        },
        "git_commit": _git_head(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    write_json(out / "manifest.json", manifest)
    stats = {
        "page_stats": page_stats(pdf_path, blocks),
        "text_stats": text_stats(blocks, raw_pages=pages),
        "structure_stats": structure_stats(blocks),
        "chunk_stats": chunk_stats(parents, children),
    }
    write_json(out / "quality_stats.json", stats)
    print(
        f"[P1:{pdf_name}] task={batch_id} poll={poll_count} zip={len(raw)}B "
        f"pages={len(pages)} blocks={len(blocks)} parents={len(parents)} "
        f"children={len(children)} {total_seconds}s"
    )
    return manifest


async def main_async(settings: Any) -> int:
    for pdf_name in PDF_NAMES:
        await parse_one_pdf(pdf_name, settings=settings)
    return 0


def main() -> int:
    from industrial_rag.config import Settings

    settings = Settings.from_env()
    if not settings.mineru_enabled or not settings.mineru_api_key:
        print("SKIP: MINERU_ENABLED=false or MINERU_API_KEY missing", file=sys.stderr)
        return 2
    return asyncio.run(main_async(settings))


if __name__ == "__main__":
    sys.exit(main())

"""Parse service: real PDF parsing with dual MinerU/PyMuPDF backends.

Orchestrates the full parse lifecycle:
    1. Choose parser based on KnowledgeBase config
    2. MinerU online API (v4 precision, file upload → poll → download → extract)
    3. PyMuPDF fallback on failure
    4. Generate ParsedBlocks via structured_chunker
    5. Produce ParentChunk + ChildChunk
    6. Validate all artifacts
    7. Atomically swap into the document's parsed directory
    8. Update Document record with counts
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.document_parser import DocumentChunk, parse_pdf
from industrial_rag.repositories.document_repository import DocumentRepository
from industrial_rag.repositories.task_repository import TaskRepository
from industrial_rag.structured_chunker import (
    ChunkerConfig,
    build_parent_child_chunks,
    pymupdf_chunks_to_blocks,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MinerU ZIP → Markdown extraction (from old worktree's _pages_from_mineru_archive)
# ---------------------------------------------------------------------------


class MinerUOutputError(RuntimeError):
    """MinerU returned a response that cannot be converted into page content."""


def _extract_pages_from_mineru_zip(payload: bytes) -> list[dict[str, object]]:
    """Extract per-page content from a MinerU result ZIP archive."""
    try:
        with ZipFile(__import__("io").BytesIO(payload)) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.endswith("_content_list.json")
            ]
            if len(names) != 1:
                raise MinerUOutputError(
                    "MinerU result archive did not contain content_list.json"
                )
            raw_items = json.loads(archive.read(names[0]).decode("utf-8"))
    except (BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MinerUOutputError("MinerU result archive was invalid") from error

    if not isinstance(raw_items, list):
        raise MinerUOutputError("MinerU content list was not a JSON array")

    pages: dict[int, list[str]] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            raise MinerUOutputError("MinerU content list item was not an object")
        page_idx = item.get("page_idx")
        if isinstance(page_idx, bool) or not isinstance(page_idx, int) or page_idx < 0:
            raise MinerUOutputError(f"MinerU page_idx was invalid: {page_idx!r}")
        text = (
            item.get("text")
            or item.get("table_body")
            or item.get("equation_latex")
        )
        if isinstance(text, str) and text.strip():
            pages.setdefault(page_idx + 1, []).append(text.strip())

    normalized: list[dict[str, object]] = []
    for page_number in sorted(pages):
        parts = pages[page_number]
        if parts:
            normalized.append(
                {"page_number": page_number, "markdown": "\n\n".join(parts)}
            )

    if not normalized:
        raise MinerUOutputError(
            "MinerU content list did not contain any readable text"
        )
    return normalized


# ---------------------------------------------------------------------------
# MinerU markdown → SourceChunk adapter (produces rich DocumentChunk-alike)
# ---------------------------------------------------------------------------

_SAFE_ID = re.compile(r"[^a-z0-9]+")

_PAGE_TITLE_RE = re.compile(r"^#{1,3}\s+(.*?)(?:\s*\{.*?\})?\s*$", re.MULTILINE)


def _section_from_markdown(md: str, fallback: str | None = None) -> str | None:
    """Extract the first heading from markdown as a section title."""
    lines = md.strip().splitlines()
    for line in lines[:10]:
        m = _PAGE_TITLE_RE.match(line)
        if m:
            title = m.group(1).strip()
            if title and len(title) <= 120:
                return title
    # Fallback: first non-empty non-heading short line
    for line in lines[:5]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and len(stripped) <= 120:
            return stripped
    return fallback


def _mineru_markdown_to_source_chunks(
    pages: list[dict[str, object]],
    source_file: str,
) -> list[DocumentChunk]:
    """Convert MinerU markdown pages into DocumentChunk objects.

    Pages may be partial or complete depending on MinerU's output.
    We treat each page as one logical DocumentChunk for simplicity —
    the structured chunker will further split them into child chunks later.
    """
    safe_stem = _SAFE_ID.sub("-", Path(source_file).stem.casefold()).strip("-") or "manual"
    chunk_list: list[DocumentChunk] = []

    for page in pages:
        page_number = int(page["page_number"])
        md = str(page.get("markdown", ""))
        if not md.strip():
            continue
        section = _section_from_markdown(md)
        digest = hashlib.sha256(md.encode("utf-8")).hexdigest()[:10]
        chunk_id = f"{safe_stem}-p{page_number}-c1-mineru-{digest}"
        chunk_list.append(
            DocumentChunk(
                chunk_id=chunk_id,
                text=md,
                source_file=source_file,
                page_number=page_number,
                section_title=section,
            )
        )

    return chunk_list


# ---------------------------------------------------------------------------
# ParseService
# ---------------------------------------------------------------------------


class ParseService:
    """Parse a PDF document using MinerU or PyMuPDF and produce artifacts."""

    def __init__(self, session: AsyncSession) -> None:
        self._doc_repo = DocumentRepository(session)
        self._task_repo = TaskRepository(session)

    async def parse_document(
        self,
        kb_id: str,
        doc_id: str,
        task_id: str,
        *,
        parsed_base: Path,
    ) -> dict[str, Any]:
        """Execute a full parse and return a manifest dict."""
        doc = await self._doc_repo.get(doc_id)
        if doc is None:
            raise RuntimeError(f"Document {doc_id} not found")

        pdf_path = Path(doc.file_path)
        if not pdf_path.is_file():
            raise RuntimeError(f"Source PDF not found: {pdf_path}")

        # Determine parser
        parser_name = getattr(doc, "parser_name", None) or "PyMuPDF"
        parser_requested = parser_name
        parser_used = parser_name
        fallback_reason: str | None = None

        # Temporary directory for this parse task
        tmp_dir = parsed_base / f"parse-{task_id}"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 1. Choose parser and parse
            source_chunks: list[DocumentChunk] = []

            if parser_name in ("mineru", "MinerU"):
                source_chunks = await self._try_mineru_parse(
                    pdf_path, doc.original_file_name, tmp_dir
                )
                if not source_chunks:
                    # MinerU failed — fallback to PyMuPDF
                    parser_used = "PyMuPDF"
                    fallback_reason = "MinerU returned no pages or failed"
                    logger.warning(
                        "MinerU parse failed for doc=%s, falling back to PyMuPDF", doc_id
                    )
                    source_chunks = parse_pdf(pdf_path)
            else:
                source_chunks = parse_pdf(pdf_path)

            if not source_chunks:
                raise RuntimeError("PDF 解析未产生任何块")

            # 2. Convert → ParsedBlocks
            blocks = pymupdf_chunks_to_blocks(source_chunks, doc.original_file_name)
            if not blocks:
                raise RuntimeError("未能生成 ParsedBlock")

            # 3. Build ParentChunk + ChildChunk
            cfg = ChunkerConfig(strategy="pymupdf-v1")
            parents, children = build_parent_child_chunks(
                blocks, doc.original_file_name, config=cfg
            )
            if not children:
                raise RuntimeError("未能生成 ChildChunk")

            # 4. Validate
            pages = max((c.page_start or 1) for c in children)
            orphan = sum(
                1
                for c in children
                if c.parent_chunk_id not in {p.parent_chunk_id for p in parents}
            )
            if orphan > 0:
                raise RuntimeError(f"存在 {orphan} 个孤儿 ChildChunk")

            # 5. Write artifacts to tmp dir
            _write_artifacts(tmp_dir, parents, children, doc, cfg)

            manifest = {
                "document_id": doc_id,
                "file_hash": doc.file_hash,
                "parser_requested": parser_requested,
                "parser_used": parser_used,
                "parser_version": "1.28.0",
                "fallback_reason": fallback_reason,
                "chunking_strategy": cfg.strategy,
                "chunking_version": cfg.version,
                "chunking_config": {
                    "parent_target_tokens": cfg.parent_target_tokens,
                    "child_target_tokens": cfg.child_target_tokens,
                },
                "page_count": pages,
                "parent_chunk_count": len(parents),
                "child_chunk_count": len(children),
                "source_pdf_hash": doc.file_hash,
                "manifest_hash": hashlib.sha256(
                    json.dumps(
                        {"child_count": len(children), "parent_count": len(parents)},
                        sort_keys=True,
                    ).encode()
                ).hexdigest()[:16],
                "created_at": datetime.now(tz=UTC).isoformat(),
            }

            # Write manifest
            (tmp_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            # 6. Atomic swap into production
            prod_dir = parsed_base / "current"
            backup_dir = parsed_base / f"backup-{task_id}"
            if prod_dir.exists():
                prod_dir.rename(backup_dir)
            tmp_dir.rename(prod_dir)

            # 7. Update document record
            await self._doc_repo.update(
                doc_id,
                page_count=pages,
                parent_chunk_count=len(parents),
                child_chunk_count=len(children),
                parse_status="done",
                parser_name=parser_used,
                parser_version="1.28.0",
                chunking_strategy=cfg.strategy,
                chunking_version=cfg.version,
            )

            # 8. Cleanup backup
            if backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)

            logger.info(
                "Parse succeeded: doc=%s pages=%d parents=%d children=%d parser=%s",
                doc_id,
                pages,
                len(parents),
                len(children),
                parser_used,
            )
            return manifest

        except Exception:
            # Cleanup tmp dir on failure
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    # ------------------------------------------------------------------
    # MinerU integration
    # ------------------------------------------------------------------

    async def _try_mineru_parse(
        self,
        pdf_path: Path,
        source_file: str,
        output_dir: Path,
    ) -> list[DocumentChunk]:
        """Attempt MinerU API parse.  Returns empty list on failure."""
        from industrial_rag.config import Settings

        settings = Settings.from_env()
        if not settings.mineru_enabled or not settings.mineru_api_key:
            logger.info("MinerU disabled or no API key — skipping")
            return []

        from industrial_rag.mineru_client import (
            MinerUClient,
            MinerUClientConfig,
        )

        config = MinerUClientConfig(
            api_base_url=settings.mineru_api_base_url,
            api_key=settings.mineru_api_key,
            api_version=settings.mineru_api_version,
            request_timeout=settings.mineru_request_timeout,
            task_timeout=settings.mineru_task_timeout,
            poll_interval=settings.mineru_poll_interval,
            max_retries=settings.mineru_max_retries,
        )

        # MinerU needs a publicly accessible URL.  For local files,
        # the v4 precision API supports signed-upload via /api/v4/file-urls/batch.
        # We upload the file bytes directly (not via URL).
        try:
            async with MinerUClient(config) as client:
                # Use the batch signed-upload workflow (old worktree approach)
                resp = await self._mineru_batch_upload(client, pdf_path, source_file, output_dir)
                if resp.error:
                    logger.warning("MinerU parse error: %s", resp.error)
                    return []

                # If we got a ZIP, extract pages
                if resp.raw_zip_path and resp.raw_zip_path.suffix == ".zip":
                    raw = resp.raw_zip_path.read_bytes()
                    pages = _extract_pages_from_mineru_zip(raw)

                    # Save raw response if configured
                    if settings.mineru_save_raw_response:
                        raw_dir = output_dir / "mineru_raw"
                        raw_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(resp.raw_zip_path, raw_dir / "result.zip")

                        # Also save page content
                        (raw_dir / "pages.json").write_text(
                            json.dumps(pages, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )

                    return _mineru_markdown_to_source_chunks(pages, source_file)

                return []
        except Exception:
            logger.exception("MinerU API call failed — falling back to PyMuPDF")
            return []

    async def _mineru_batch_upload(
        self,
        client: Any,
        pdf_path: Path,
        source_file: str,
        tmp_dir: Path,
    ) -> Any:
        """Use MinerU's batch signed-upload workflow (from old worktree approach).

        1. POST /api/v4/file-urls/batch → get signed upload URL
        2. PUT file bytes to signed URL
        3. GET /api/v4/extract-results/batch/{batch_id} → poll
        4. Download & return ZIP
        """
        from industrial_rag.mineru_client import MinerUValidationError

        file_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        cfg = client._config

        # Step 1: Request batch upload URL
        batch_payload = {
            "files": [{"name": pdf_path.name, "data_id": file_hash}],
            "model_version": cfg.model_version,
        }
        resp = await client._request_with_retry(
            "POST", "/api/v4/file-urls/batch", json=batch_payload
        )
        data = resp.get("data", {})
        batch_id = data.get("batch_id")
        upload_urls = data.get("file_urls")
        if not isinstance(batch_id, str) or not batch_id or not isinstance(upload_urls, list):
            raise MinerUValidationError("MinerU batch upload response invalid")
        if len(upload_urls) != 1:
            raise MinerUValidationError("MinerU batch upload returned unexpected URL count")

        # Step 2: PUT file to signed URL
        import httpx

        pdf_bytes = pdf_path.read_bytes()
        async with httpx.AsyncClient(timeout=httpx.Timeout(cfg.request_timeout)) as put_client:
            put_resp = await put_client.put(
                upload_urls[0],
                content=pdf_bytes,
                headers={"Content-Length": str(len(pdf_bytes))},
            )
            if put_resp.status_code < 200 or put_resp.status_code >= 300:
                logger.warning("MinerU file upload failed: HTTP %d", put_resp.status_code)
                from industrial_rag.mineru_client import MinerUValidationError

                raise MinerUValidationError(
                    f"MinerU file upload failed: HTTP {put_resp.status_code}"
                )

        # Step 3: Poll for results
        result_url = f"/api/v4/extract-results/batch/{batch_id}"
        for attempt in range(cfg.max_retries * 50):  # up to 150 polls at 3s = 450s
            result_resp = await client._request_with_retry("GET", result_url)
            result_data = result_resp.get("data", {})
            extract_results = result_data.get("extract_result")
            if not isinstance(extract_results, list) or len(extract_results) != 1:
                raise MinerUValidationError("MinerU extract results invalid")
            er = extract_results[0]
            if not isinstance(er, dict):
                raise MinerUValidationError("MinerU extract result invalid")
            state = er.get("state")
            if state == "done":
                archive_url = er.get("full_zip_url")
                if not isinstance(archive_url, str):
                    raise MinerUValidationError("MinerU full_zip_url missing")
                # Step 4: Download and save ZIP
                result_dir = tmp_dir / "mineru_result"
                result_dir.mkdir(parents=True, exist_ok=True)
                saved = await client.download_result(archive_url, output_dir=result_dir)

                from industrial_rag.mineru_client import MinerUParseResponse

                return MinerUParseResponse(
                    task_id=batch_id,
                    raw_zip_path=saved,
                )
            if state == "failed":
                err_msg = er.get("err_msg", "unknown")
                raise MinerUValidationError(f"MinerU task failed: {err_msg}")
            if attempt + 1 >= cfg.max_retries * 50:
                break
            await asyncio.sleep(cfg.poll_interval)

        from industrial_rag.mineru_client import MinerUParseResponse

        return MinerUParseResponse(
            task_id=batch_id, error="MinerU polling timed out"
        )


# ---------------------------------------------------------------------------
# Artifact writing
# ---------------------------------------------------------------------------


def _write_artifacts(
    out_dir: Path,
    parents: list,
    children: list,
    doc: Any,
    cfg: ChunkerConfig,
) -> None:
    """Write ParentChunk and ChildChunk JSONL files."""
    # Parent chunks
    parent_path = out_dir / "parent_chunks.jsonl"
    with parent_path.open("w", encoding="utf-8", newline="\n") as f:
        for p in parents:
            _ct = p.content_type
            ct_value = (
                _ct.value
                if hasattr(_ct, "value")
                else str(_ct)
                if not isinstance(_ct, str)
                else _ct
            )
            obj = {
                "parent_chunk_id": p.parent_chunk_id,
                "document_id": p.document_id,
                "document_name": p.document_name,
                "page_start": p.page_start,
                "page_end": p.page_end,
                "section_path": list(p.section_path),
                "section_title": p.section_title,
                "content_type": ct_value,
                "content": p.content,
                "token_count": p.token_count,
                "source_hash": p.source_hash,
                "child_chunk_ids": list(p.child_chunk_ids),
            }
            f.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")

    # Child chunks
    child_path = out_dir / "child_chunks.jsonl"
    with child_path.open("w", encoding="utf-8", newline="\n") as f:
        for c in children:
            # Serialize enum values as strings
            d = {}
            if hasattr(c, "to_dict"):
                d = c.to_dict()
            elif isinstance(c, dict):
                d = c
            else:
                d = {
                    k: str(v) if hasattr(v, "value") else v
                    for k, v in c.__dict__.items()
                    if not k.startswith("_")
                }
            f.write(
                json.dumps(d, ensure_ascii=False, sort_keys=True, default=str) + "\n"
            )


def load_child_chunks(parsed_dir: Path) -> list[Any]:
    """Load ChildChunk objects from the parsed artifacts directory.

    Returns the raw dicts — callers can convert to ChildChunk if needed.
    """
    from industrial_rag.parser_models import ChildChunk

    current = parsed_dir / "current"
    child_path = current / "child_chunks.jsonl"
    if not child_path.is_file():
        return []

    children: list[ChildChunk] = []
    for line in child_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        children.append(ChildChunk.from_dict(rec))
    return children

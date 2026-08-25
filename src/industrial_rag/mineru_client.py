"""Typed MinerU API client — async HTTP client for the mineru.net REST API.

Supports both the Precision Extract API (v4, authenticated) and the
Agent Lightweight Extract API (v1, unauthenticated / anonymous).  The
caller selects the API version and base URL at construction time.

API contract source: https://mineru.net/doc/docs/index_en/ (captured 2026-07-30)
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import httpx

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class MinerUTaskState(Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    converting = "converting"
    waiting_file = "waiting-file"
    uploading = "uploading"
    unknown = "unknown"


@dataclass(frozen=True, slots=True)
class MinerUExtractProgress:
    extracted_pages: int = 0
    total_pages: int = 0
    start_time: str | None = None


@dataclass(frozen=True, slots=True)
class MinerUTaskResult:
    task_id: str
    state: MinerUTaskState
    data_id: str | None = None
    full_zip_url: str | None = None
    markdown_url: str | None = None
    err_msg: str | None = None
    err_code: str | None = None
    progress: MinerUExtractProgress | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in (
            MinerUTaskState.done,
            MinerUTaskState.failed,
        )

    @property
    def is_success(self) -> bool:
        return self.state == MinerUTaskState.done


@dataclass(frozen=True, slots=True)
class MinerUParseResponse:
    """Normalised MinerU parsing result used by the comparison pipeline."""

    task_id: str
    markdown: str | None = None
    json_content: dict[str, Any] | None = None
    pages: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    raw_zip_path: Path | None = None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.markdown is not None and self.error is None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MINERU_PRECISION_BASE = "https://mineru.net"
MINERU_AGENT_BASE = "https://mineru.net"


@dataclass(frozen=True, slots=True)
class MinerUClientConfig:
    api_base_url: str = MINERU_PRECISION_BASE
    api_key: str | None = None
    api_version: Literal["v4", "v1"] = "v4"
    request_timeout: float = 60.0
    task_timeout: float = 600.0
    poll_interval: float = 3.0
    max_retries: int = 3
    model_version: str = "pipeline"
    enable_formula: bool = True
    enable_table: bool = True
    language: str = "ch"
    is_ocr: bool = False

    @property
    def uses_precision_api(self) -> bool:
        return self.api_version == "v4"

    @property
    def requires_auth(self) -> bool:
        return self.uses_precision_api

    @property
    def _redacted_api_key(self) -> str:
        if not self.api_key:
            return "(not set)"
        return self.api_key[:4] + "…" if len(self.api_key) > 4 else "…"


# ---------------------------------------------------------------------------
# Client errors
# ---------------------------------------------------------------------------


class MinerUError(RuntimeError):
    """Base for all MinerU client errors."""

    def __init__(self, message: str, *, task_id: str | None = None) -> None:
        super().__init__(message)
        self.task_id = task_id


class MinerUAuthError(MinerUError):
    """Invalid or expired API token."""


class MinerUTaskFailedError(MinerUError):
    """The remote task reached state=failed."""

    def __init__(
        self,
        message: str,
        *,
        task_id: str,
        err_msg: str | None = None,
        err_code: str | None = None,
    ) -> None:
        super().__init__(message, task_id=task_id)
        self.err_msg = err_msg
        self.err_code = err_code


class MinerUTimeoutError(MinerUError):
    """Task did not complete within the configured task_timeout."""


class MinerURateLimitError(MinerUError):
    """IP rate-limited (Agent API)."""


class MinerUValidationError(MinerUError):
    """Response from MinerU failed validation (e.g. missing fields)."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class MinerUClient:
    """Async client for the mineru.net REST APIs (v4 Precision or v1 Agent).

    Usage::

        config = MinerUClientConfig(api_key="muc-...")
        async with MinerUClient(config) as client:
            result = await client.parse_file_url("https://.../manual.pdf")

    """

    def __init__(self, config: MinerUClientConfig | None = None) -> None:
        self._config = config or MinerUClientConfig()
        self._client: httpx.AsyncClient | None = None
        self._owns_client = False

    async def __aenter__(self) -> MinerUClient:
        await self._ensure_client()
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
        self._client = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers: dict[str, str] = {"Accept": "application/json"}
            if self._config.api_key:
                headers["Authorization"] = f"Bearer {self._config.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self._config.api_base_url,
                timeout=httpx.Timeout(self._config.request_timeout),
                headers=headers,
            )
            self._owns_client = True
        return self._client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit_document(self, file_url: str, *, data_id: str | None = None) -> str:
        """Submit a document URL for extraction.  Returns the task_id."""
        await self._ensure_client()
        cfg = self._config

        if cfg.uses_precision_api:
            payload: dict[str, Any] = {
                "url": file_url,
                "model_version": cfg.model_version,
                "is_ocr": cfg.is_ocr,
                "enable_formula": cfg.enable_formula,
                "enable_table": cfg.enable_table,
                "language": cfg.language,
            }
            if data_id:
                payload["data_id"] = data_id
            resp = await self._request_with_retry(
                "POST", "/api/v4/extract/task", json=payload
            )
            data = resp.get("data", {})
            task_id = data.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                raise MinerUValidationError(
                    f"No task_id in response: {json.dumps(resp)[:200]}"
                )
            return task_id

        # v1 Agent API
        payload = {
            "url": file_url,
            "enable_table": cfg.enable_table,
            "is_ocr": cfg.is_ocr,
            "enable_formula": cfg.enable_formula,
            "language": cfg.language,
        }
        if data_id:
            payload["file_name"] = data_id
        resp = await self._request_with_retry(
            "POST", "/api/v1/agent/parse/url", json=payload
        )
        data = resp.get("data", {})
        task_id = data.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise MinerUValidationError(
                f"No task_id in response: {json.dumps(resp)[:200]}"
            )
        return task_id

    async def get_task_status(self, task_id: str) -> MinerUTaskResult:
        """Query the current state of a submitted task."""
        await self._ensure_client()
        cfg = self._config

        if cfg.uses_precision_api:
            path = f"/api/v4/extract/task/{task_id}"
        else:
            path = f"/api/v1/agent/parse/{task_id}"

        resp = await self._request_with_retry("GET", path)
        data = resp.get("data", {})
        return self._parse_task_result(task_id, data)

    async def wait_for_completion(
        self, task_id: str, *, poll_interval: float | None = None, task_timeout: float | None = None
    ) -> MinerUTaskResult:
        """Poll until the task reaches a terminal state or times out."""
        interval = poll_interval if poll_interval is not None else self._config.poll_interval
        timeout = task_timeout if task_timeout is not None else self._config.task_timeout
        deadline = time.monotonic() + timeout

        while True:
            result = await self.get_task_status(task_id)
            if result.is_terminal:
                if result.state == MinerUTaskState.failed:
                    raise MinerUTaskFailedError(
                        f"Task {task_id} failed: {result.err_msg or 'unknown'}",
                        task_id=task_id,
                        err_msg=result.err_msg,
                        err_code=result.err_code,
                    )
                return result
            if time.monotonic() > deadline:
                raise MinerUTimeoutError(
                    f"Task {task_id} did not complete within {timeout:.0f}s",
                    task_id=task_id,
                )
            await self._sleep(interval)

    async def download_result(
        self, url: str, *, output_dir: Path | None = None
    ) -> Path:
        """Download the ZIP archive or markdown file to the output directory."""
        dest = (output_dir or Path.cwd()) / _safe_filename_from_url(url)
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Downloads go through a separate CDN / OSS URL, so use a temporary
        # client without the base_url prefix.
        async with httpx.AsyncClient(timeout=httpx.Timeout(self._config.request_timeout)) as dl:
            response = await dl.get(url, follow_redirects=True)
            response.raise_for_status()
            dest.write_bytes(response.content)

        return dest

    async def parse_document(
        self,
        file_url: str,
        *,
        data_id: str | None = None,
        output_dir: Path | None = None,
    ) -> MinerUParseResponse:
        """Full end-to-end: submit → poll → download → return normalised result."""
        task_id = await self.submit_document(file_url, data_id=data_id)
        try:
            result = await self.wait_for_completion(task_id)
        except MinerUTaskFailedError as exc:
            return MinerUParseResponse(
                task_id=task_id,
                error=f"MinerU failed: {exc.err_msg or 'unknown'}",
            )
        except MinerUTimeoutError:
            return MinerUParseResponse(
                task_id=task_id,
                error=f"MinerU timed out after {self._config.task_timeout:.0f}s",
            )

        if not result.is_success:
            return MinerUParseResponse(
                task_id=task_id,
                error=f"Unexpected terminal state: {result.state.value}",
            )

        # Download the result archive / markdown
        download_url = (
            result.full_zip_url
            or result.markdown_url
        )
        if not download_url:
            return MinerUParseResponse(
                task_id=task_id,
                error="No download URL in completed task result",
            )

        try:
            saved_path = await self.download_result(download_url, output_dir=output_dir)
        except Exception as exc:
            return MinerUParseResponse(
                task_id=task_id,
                error=f"Download failed: {exc}",
            )

        # If it's a ZIP, the caller should unpack it.
        # For now, return the raw path.
        return MinerUParseResponse(
            task_id=task_id,
            raw_zip_path=saved_path,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _request_with_retry(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        client = await self._ensure_client()
        last_exc: Exception | None = None
        for attempt in range(self._config.max_retries + 1):
            try:
                if method == "POST":
                    resp = await client.post(path, json=json)
                else:
                    resp = await client.get(path)
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < self._config.max_retries:
                    await self._sleep(self._config.poll_interval * (2**attempt))
                    continue
                raise MinerUError(
                    f"Request timed out after {self._config.max_retries + 1} attempts"
                ) from exc
            except httpx.NetworkError as exc:
                last_exc = exc
                if attempt < self._config.max_retries:
                    await self._sleep(self._config.poll_interval * (2**attempt))
                    continue
                raise MinerUError(f"Network error: {exc}") from exc

            if resp.status_code == 401:
                raise MinerUAuthError(
                    "Invalid or expired MinerU API token. "
                    "Set MINERU_API_KEY in .env."
                )
            if resp.status_code == 429:
                raise MinerURateLimitError(
                    "MinerU rate limit reached. Wait and retry, "
                    "or reduce request frequency."
                )
            if resp.status_code >= 500:
                if attempt < self._config.max_retries:
                    await self._sleep(self._config.poll_interval * (2**attempt))
                    continue
                raise MinerUError(
                    f"MinerU server error (HTTP {resp.status_code})"
                )

            # Parse response body
            try:
                body = resp.json()
            except Exception:
                raise MinerUError(
                    f"MinerU returned non-JSON response (HTTP {resp.status_code}): "
                    f"{resp.text[:200]}"
                )
            if not isinstance(body, dict):
                raise MinerUValidationError(
                    f"MinerU response was not a JSON object: {type(body).__name__}"
                )
            return body

        raise MinerUError(f"Request failed after retries: {last_exc}")

    @staticmethod
    def _parse_task_result(task_id: str, data: dict[str, Any]) -> MinerUTaskResult:
        raw_state = data.get("state", "unknown")
        try:
            state = MinerUTaskState(raw_state)
        except ValueError:
            state = MinerUTaskState.unknown

        progress = None
        if "extract_progress" in data and isinstance(data["extract_progress"], dict):
            ep = data["extract_progress"]
            progress = MinerUExtractProgress(
                extracted_pages=int(ep.get("extracted_pages", 0)),
                total_pages=int(ep.get("total_pages", 0)),
                start_time=ep.get("start_time"),
            )

        return MinerUTaskResult(
            task_id=task_id,
            state=state,
            data_id=data.get("data_id"),
            full_zip_url=data.get("full_zip_url"),
            markdown_url=data.get("markdown_url"),
            err_msg=data.get("err_msg"),
            err_code=data.get("err_code"),
            progress=progress,
        )

    @staticmethod
    async def _sleep(seconds: float) -> None:
        import asyncio
        await asyncio.sleep(seconds)


def _safe_filename_from_url(url: str) -> str:
    """Derive a safe filename from a download URL."""
    suffix = hashlib.sha256(url.encode()).hexdigest()[:12]
    if url.endswith(".zip"):
        return f"mineru_result_{suffix}.zip"
    if ".md" in url.rsplit("/", 1)[-1]:
        return f"mineru_result_{suffix}.md"
    return f"mineru_result_{suffix}.bin"

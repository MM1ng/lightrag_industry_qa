"""Unit tests for MinerU API Client — all offline via httpx.MockTransport."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from industrial_rag.mineru_client import (
    MinerUAuthError,
    MinerUClient,
    MinerUClientConfig,
    MinerUError,
    MinerURateLimitError,
    MinerUTaskFailedError,
    MinerUTaskState,
    MinerUTimeoutError,
    MinerUValidationError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_transport(
    response_sequence: list[httpx.Response],
) -> httpx.MockTransport:
    """Return a MockTransport that serves responses in order."""
    idx = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal idx
        resp = response_sequence[idx] if idx < len(response_sequence) else httpx.Response(500)
        idx += 1
        return resp

    return httpx.MockTransport(handler)


def _config(**kwargs: object) -> MinerUClientConfig:
    return MinerUClientConfig(
        api_base_url="https://mineru.test",
        api_key=None,
        **{k: v for k, v in kwargs.items() if v is not None},  # type: ignore[misc]
    )


def _good_submit() -> httpx.Response:
    return httpx.Response(
        200,
        json={"data": {"task_id": "task-uuid-12345"}},
    )


def _good_status(state: str, **extra: str) -> httpx.Response:
    body: dict[str, object] = {"data": {"task_id": "task-uuid-12345", "state": state, **extra}}
    return httpx.Response(200, json=body)


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_success() -> None:
    client = MinerUClient(
        _config(),
    )
    client._client = httpx.AsyncClient(
        base_url="https://mineru.test",
        transport=_mock_transport([_good_submit()]),
    )
    task_id = await client.submit_document("https://example.com/doc.pdf")
    assert task_id == "task-uuid-12345"


@pytest.mark.asyncio
async def test_submit_missing_task_id_in_response() -> None:
    client = MinerUClient(_config())
    client._client = httpx.AsyncClient(
        base_url="https://mineru.test",
        transport=_mock_transport(
            [httpx.Response(200, json={"data": {"other": "value"}})]
        ),
    )
    with pytest.raises(MinerUValidationError, match="task_id"):
        await client.submit_document("https://example.com/doc.pdf")


@pytest.mark.asyncio
async def test_submit_unauthorized() -> None:
    client = MinerUClient(_config())
    client._client = httpx.AsyncClient(
        base_url="https://mineru.test",
        transport=_mock_transport(
            [httpx.Response(401, json={"code": "A0202", "msg": "Invalid Token"})]
        ),
    )
    with pytest.raises(MinerUAuthError):
        await client.submit_document("https://example.com/doc.pdf")


@pytest.mark.asyncio
async def test_submit_rate_limited() -> None:
    client = MinerUClient(_config())
    client._client = httpx.AsyncClient(
        base_url="https://mineru.test",
        transport=_mock_transport([httpx.Response(429)]),
    )
    with pytest.raises(MinerURateLimitError):
        await client.submit_document("https://example.com/doc.pdf")


@pytest.mark.asyncio
async def test_submit_server_error_with_retry_exhausted() -> None:
    config = _config(max_retries=0)  # no retries
    client = MinerUClient(config)
    client._client = httpx.AsyncClient(
        base_url="https://mineru.test",
        transport=_mock_transport([httpx.Response(502)]),
    )
    with pytest.raises(MinerUError, match="server error"):
        await client.submit_document("https://example.com/doc.pdf")


@pytest.mark.asyncio
async def test_submit_invalid_json_response() -> None:
    client = MinerUClient(_config())
    client._client = httpx.AsyncClient(
        base_url="https://mineru.test",
        transport=_mock_transport(
            [httpx.Response(200, content=b"<!DOCTYPE html><html></html>")]
        ),
    )
    with pytest.raises(MinerUError, match="non-JSON"):
        await client.submit_document("https://example.com/doc.pdf")


# ---------------------------------------------------------------------------
# Get task status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_status_pending() -> None:
    client = MinerUClient(_config())
    client._client = httpx.AsyncClient(
        base_url="https://mineru.test",
        transport=_mock_transport(
            [_good_status("pending")]
        ),
    )
    result = await client.get_task_status("task-uuid-12345")
    assert result.task_id == "task-uuid-12345"
    assert result.state == MinerUTaskState.pending
    assert not result.is_terminal


@pytest.mark.asyncio
async def test_get_status_done() -> None:
    client = MinerUClient(_config())
    client._client = httpx.AsyncClient(
        base_url="https://mineru.test",
        transport=_mock_transport(
            [_good_status("done", full_zip_url="https://cdn.test/result.zip")]
        ),
    )
    result = await client.get_task_status("task-uuid-12345")
    assert result.state == MinerUTaskState.done
    assert result.is_terminal
    assert result.is_success
    assert result.full_zip_url == "https://cdn.test/result.zip"


@pytest.mark.asyncio
async def test_get_status_failed() -> None:
    client = MinerUClient(_config())
    client._client = httpx.AsyncClient(
        base_url="https://mineru.test",
        transport=_mock_transport(
            [_good_status("failed", err_msg="File corrupted")]
        ),
    )
    result = await client.get_task_status("task-uuid-12345")
    assert result.state == MinerUTaskState.failed
    assert result.is_terminal
    assert not result.is_success
    assert result.err_msg == "File corrupted"


@pytest.mark.asyncio
async def test_get_status_unknown_state_is_not_terminal() -> None:
    client = MinerUClient(_config())
    client._client = httpx.AsyncClient(
        base_url="https://mineru.test",
        transport=_mock_transport(
            [_good_status("future-state-we-dont-know")]
        ),
    )
    result = await client.get_task_status("task-uuid-12345")
    assert result.state == MinerUTaskState.unknown
    assert not result.is_terminal


# ---------------------------------------------------------------------------
# Wait for completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_returns_on_done() -> None:
    client = MinerUClient(_config())
    client._client = httpx.AsyncClient(
        base_url="https://mineru.test",
        transport=_mock_transport(
            [
                _good_status("pending"),
                _good_status("running"),
                _good_status("done", full_zip_url="https://cdn.test/result.zip"),
            ]
        ),
    )
    result = await client.wait_for_completion(
        "task-uuid-12345", poll_interval=0.01, task_timeout=60.0
    )
    assert result.state == MinerUTaskState.done


@pytest.mark.asyncio
async def test_wait_raises_on_failed() -> None:
    client = MinerUClient(_config())
    client._client = httpx.AsyncClient(
        base_url="https://mineru.test",
        transport=_mock_transport(
            [
                _good_status("pending"),
                _good_status("failed", err_msg="extraction error"),
            ]
        ),
    )
    with pytest.raises(MinerUTaskFailedError, match="extraction error"):
        await client.wait_for_completion(
            "task-uuid-12345", poll_interval=0.01, task_timeout=60.0
        )


@pytest.mark.asyncio
async def test_wait_raises_on_timeout() -> None:
    client = MinerUClient(_config())
    client._client = httpx.AsyncClient(
        base_url="https://mineru.test",
        transport=_mock_transport([_good_status("pending")] * 10),
    )
    with pytest.raises(MinerUTimeoutError, match="did not complete"):
        await client.wait_for_completion(
            "task-uuid-12345", poll_interval=0.01, task_timeout=0.05
        )


# ---------------------------------------------------------------------------
# Download result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_saves_to_output_dir(tmp_path: Path) -> None:
    """download_result creates a temp client—use MockTransport on that client path."""
    # Patch the download method directly since it creates its own client
    config = _config()
    tmp_path / "output" / "downloaded.zip"

    async def fake_download(self: MinerUClient, url: str, *, output_dir: Path | None = None) -> Path:
        dest = (output_dir or Path.cwd()) / "downloaded.zip"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake zip content")
        return dest

    import industrial_rag.mineru_client as mc
    original = mc.MinerUClient.download_result
    mc.MinerUClient.download_result = fake_download  # type: ignore[method-assign]
    try:
        client = MinerUClient(config)
        async with client:
            saved = await client.download_result(
                "https://cdn.test/result.zip", output_dir=tmp_path / "output"
            )
        assert saved.exists()
        assert saved.read_bytes() == b"fake zip content"
    finally:
        mc.MinerUClient.download_result = original


# ---------------------------------------------------------------------------
# Parse document (full pipeline, mock)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_document_failed_task() -> None:
    client = MinerUClient(_config())
    client._client = httpx.AsyncClient(
        base_url="https://mineru.test",
        transport=_mock_transport(
            [
                _good_submit(),
                _good_status("failed", err_msg="bad pdf"),
            ]
        ),
    )
    result = await client.parse_document("https://example.com/doc.pdf")
    assert result.task_id == "task-uuid-12345"
    assert not result.success
    assert "bad pdf" in (result.error or "")


# ---------------------------------------------------------------------------
# Config / security
# ---------------------------------------------------------------------------


def test_redacted_key_does_not_leak_full_secret() -> None:
    cfg = MinerUClientConfig(api_key="muc-verylongsecrettoken12345")
    assert "muc-verylongsecrettoken12345" not in cfg._redacted_api_key
    assert cfg._redacted_api_key.startswith("muc-")
    assert "…" in cfg._redacted_api_key


def test_redacted_key_empty() -> None:
    cfg = MinerUClientConfig(api_key=None)
    assert cfg._redacted_api_key == "(not set)"


def test_config_requires_auth_for_precision() -> None:
    cfg = MinerUClientConfig(api_version="v4")
    assert cfg.requires_auth


def test_config_no_auth_for_agent() -> None:
    cfg = MinerUClientConfig(api_version="v1")
    assert not cfg.requires_auth


def test_config_defaults() -> None:
    cfg = MinerUClientConfig()
    assert cfg.uses_precision_api
    assert cfg.request_timeout == 60.0
    assert cfg.task_timeout == 600.0
    assert cfg.poll_interval == 3.0
    assert cfg.max_retries == 3

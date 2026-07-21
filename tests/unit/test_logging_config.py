from __future__ import annotations

from pydantic import SecretStr

from industrial_energy_agent.logging_config import REDACTED, redact_event


def test_redactor_removes_secrets_and_authorization() -> None:
    event = {"api_key": "secret", "Authorization": "Bearer token"}

    assert redact_event(event) == {"api_key": REDACTED, "Authorization": REDACTED}


def test_redactor_recurses_without_mutating_original() -> None:
    event = {
        "request": {
            "headers": {"x-api-key": "secret", "content-type": "application/json"},
            "items": [{"password": "secret"}, {"status": "ok"}],
        }
    }

    redacted = redact_event(event)

    assert redacted["request"]["headers"]["x-api-key"] == REDACTED
    assert redacted["request"]["headers"]["content-type"] == "application/json"
    assert redacted["request"]["items"][0]["password"] == REDACTED
    assert event["request"]["headers"]["x-api-key"] == "secret"


def test_redactor_never_serializes_secret_str() -> None:
    event = {"safe_name": SecretStr("test-only-secret")}

    assert redact_event(event) == {"safe_name": REDACTED}


def test_redactor_masks_tenant_bearing_workspace_url() -> None:
    event = {
        "base_url": "https://tenant-123.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "public_url": "http://127.0.0.1:8000/health",
    }

    redacted = redact_event(event)

    assert redacted["base_url"] == "https://***.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    assert redacted["public_url"] == "http://127.0.0.1:8000/health"


def test_redactor_masks_workspace_url_embedded_in_exception_text() -> None:
    event = {
        "error": (
            "request to "
            "https://tenant-embedded.cn-beijing.maas.aliyuncs.com/compatible-mode/v1 "
            "failed after retry"
        )
    }

    redacted = redact_event(event)

    assert redacted["error"] == (
        "request to https://***.cn-beijing.maas.aliyuncs.com/compatible-mode/v1 failed after retry"
    )
    assert "tenant-embedded" not in redacted["error"]

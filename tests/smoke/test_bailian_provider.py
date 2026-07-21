from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from scripts import smoke_bailian


def test_no_smoke_flags_does_not_create_a_client(
    capsys: Any,
) -> None:
    created = False

    def forbidden_client_factory(_settings: Any) -> Any:
        nonlocal created
        created = True
        raise AssertionError("client factory must not run")

    result = smoke_bailian.main([], client_factory=forbidden_client_factory)

    assert result == 2
    assert created is False
    assert "Select at least one smoke check" in capsys.readouterr().err


class _SmokeCompletions:
    def __init__(self, *, tool_arguments: str = '{"status":"ready"}') -> None:
        self._tool_arguments = tool_arguments

    def create(self, **request: Any) -> SimpleNamespace:
        if "response_format" in request:
            content = '{"status":"ok"}'
            tool_calls = None
            finish_reason = "stop"
        elif "tools" in request:
            content = None
            tool_calls = [
                SimpleNamespace(
                    id="call-smoke-1",
                    type="function",
                    function=SimpleNamespace(
                        name="report_readiness",
                        arguments=self._tool_arguments,
                    ),
                )
            ]
            finish_reason = "tool_calls"
        else:
            content = "ok"
            tool_calls = None
            finish_reason = "stop"
        return SimpleNamespace(
            id="chatcmpl-smoke",
            model="qwen3.7-plus",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content, tool_calls=tool_calls),
                    finish_reason=finish_reason,
                )
            ],
        )


class _SmokeEmbeddings:
    def create(self, **request: Any) -> SimpleNamespace:
        return SimpleNamespace(
            model="text-embedding-v4",
            data=[SimpleNamespace(index=0, embedding=[0.0] * request["dimensions"])],
        )


class _SmokeClient:
    def __init__(self, *, tool_arguments: str = '{"status":"ready"}') -> None:
        self.chat = SimpleNamespace(completions=_SmokeCompletions(tool_arguments=tool_arguments))
        self.embeddings = _SmokeEmbeddings()


def test_selected_checks_print_only_sanitized_pass_summaries(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "test-secret-never-print"
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", secret)

    result = smoke_bailian.main(
        ["--chat", "--json-mode", "--function-call", "--embedding"],
        client_factory=lambda _settings: _SmokeClient(),
    )

    assert result == 0
    output = capsys.readouterr().out
    assert "PASS chat model=qwen3.7-plus" in output
    assert "PASS json-mode model=qwen3.7-plus" in output
    assert "PASS function-call model=qwen3.7-plus tool=report_readiness" in output
    assert "PASS embedding model=text-embedding-v4 dimensions=1024" in output
    assert secret not in output
    assert "Bearer" not in output


def test_selected_check_without_key_does_not_create_a_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    created = False

    def forbidden_client_factory(_settings: Any) -> Any:
        nonlocal created
        created = True
        raise AssertionError("client factory must not run")

    result = smoke_bailian.main(["--chat"], client_factory=forbidden_client_factory)

    assert result == 2
    assert created is False
    assert "API key is not configured" in capsys.readouterr().err


def test_smoke_does_not_load_dashscope_key_from_dotenv(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=disk-secret-must-not-load\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    created = False

    def forbidden_client_factory(_settings: Any) -> Any:
        nonlocal created
        created = True
        raise AssertionError("client factory must not run")

    result = smoke_bailian.main(["--chat"], client_factory=forbidden_client_factory)

    assert result == 2
    assert created is False
    captured = capsys.readouterr()
    assert "API key is not configured" in captured.err
    assert "disk-secret-must-not-load" not in captured.err


def test_invalid_settings_are_reported_without_exposing_workspace_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_id = "secret-workspace-id"
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only")
    monkeypatch.setenv(
        "LLM_BASE_URL",
        f"https://{workspace_id}.example.invalid/compatible-mode/v1",
    )

    result = smoke_bailian.main(["--chat"], client_factory=lambda _settings: _SmokeClient())

    assert result == 1
    captured = capsys.readouterr()
    assert "error_type=ValidationError" in captured.err
    assert workspace_id not in captured.err
    assert workspace_id not in captured.out


@pytest.mark.parametrize(
    ("variable_name", "value", "flag"),
    [
        ("CHAT_MODEL", "unexpected-chat-model", "--chat"),
        ("EMBEDDING_MODEL", "unexpected-embedding-model", "--embedding"),
        ("EMBEDDING_DIMENSION", "768", "--embedding"),
    ],
)
def test_smoke_rejects_overrides_of_the_locked_model_contract(
    variable_name: str,
    value: str,
    flag: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only")
    monkeypatch.setenv(variable_name, value)
    created = False

    def forbidden_client_factory(_settings: Any) -> Any:
        nonlocal created
        created = True
        raise AssertionError("client factory must not run")

    result = smoke_bailian.main([flag], client_factory=forbidden_client_factory)

    assert result == 1
    assert created is False
    assert "error_type=SmokeContractError" in capsys.readouterr().err


def test_function_call_smoke_rejects_wrong_arguments(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only")

    result = smoke_bailian.main(
        ["--function-call"],
        client_factory=lambda _settings: _SmokeClient(tool_arguments='{"status":"wrong"}'),
    )

    assert result == 1
    captured = capsys.readouterr()
    assert "error_type=ProviderResponseError" in captured.err
    assert "PASS function-call" not in captured.out

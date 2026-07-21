from __future__ import annotations

import sys

import pytest
from scripts import inspect_environment


@pytest.fixture(autouse=True)
def clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable_name in (
        *inspect_environment.SECRET_VARIABLES,
        "LLM_BASE_URL",
    ):
        monkeypatch.delenv(variable_name, raising=False)


def test_report_uses_dotenv_settings_source_and_never_prints_values(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_url = "https://workspace-from-dotenv.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    secret = "test-only-never-print"
    (tmp_path / ".env").write_text(
        f"LLM_BASE_URL={workspace_url}\nDASHSCOPE_API_KEY={secret}\nLLM_API_KEY=\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert inspect_environment.main() == 0
    output = capsys.readouterr().out

    assert f"Executable: {sys.executable}" in output
    assert "LLM base URL source: EXPLICIT" in output
    assert "LLM_API_KEY: UNSET" in output
    assert "DASHSCOPE_API_KEY: SET" in output
    assert workspace_url not in output
    assert secret not in output


def test_empty_dotenv_secrets_are_unset(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=\nLIGHTRAG_API_KEY=   \n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert inspect_environment.main() == 0
    output = capsys.readouterr().out

    assert "DASHSCOPE_API_KEY: UNSET" in output
    assert "LIGHTRAG_API_KEY: UNSET" in output

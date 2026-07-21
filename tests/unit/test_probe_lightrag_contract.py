from __future__ import annotations

import httpx
import pytest
from scripts import probe_lightrag_contract


def test_probe_help_documents_expensive_insert_opt_in(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        probe_lightrag_contract.main(["--help"])

    output = capsys.readouterr().out
    assert exit_info.value.code == 0
    assert "--require-all-modes" in output
    assert "--exercise-insert" in output


def test_verified_mode_list_never_exposes_bypass() -> None:
    assert probe_lightrag_contract.VERIFIED_MODES == (
        "local",
        "global",
        "hybrid",
        "naive",
        "mix",
    )


def test_api_key_gate_uses_unauthenticated_403_not_jwt_auth_mode() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(403, request=request, json={"detail": "forbidden"})
        ),
        base_url="http://127.0.0.1:19621",
    )

    probe_lightrag_contract.require_api_key_gate(client)

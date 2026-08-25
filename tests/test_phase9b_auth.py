"""Behavioral tests for Phase 9B dual-role Bearer authorization."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from industrial_rag.api import create_app
from industrial_rag.citation_formatter import Citation
from industrial_rag.config import Settings
from industrial_rag.db.session import reset_for_testing
from industrial_rag.lightrag_service import QueryResult

SERVICE_KEY = "phase9b-service-test-credential"
ADMIN_KEY = "phase9b-admin-test-credential"


class _Runtime:
    def query(self, question: str, *, mode: str, timeout: float):
        assert question == "测试问题"
        assert mode == "mix"
        assert timeout == 180.0
        return (
            QueryResult(
                answer="测试答案",
                citations=(Citation("manual.pdf", 1, "chunk-1"),),
                mode="mix",
            ),
            0.01,
        )

    def close(self) -> None:
        return None


def _settings(*, service_key: str = SERVICE_KEY, admin_key: str = ADMIN_KEY) -> Settings:
    return Settings.from_mapping(
        {
            "DASHSCOPE_API_KEY": "offline-provider-key",
            "SERVICE_API_KEY": service_key,
            "ADMIN_API_KEY": admin_key,
        }
    )


def _auth(token: str | None) -> dict[str, str]:
    return {} if token is None else {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'auth.db').as_posix()}"
    )
    reset_for_testing()
    app = create_app(settings=_settings(), runtime_factory=lambda _settings: _Runtime())
    with TestClient(app) as test_client:
        yield test_client
    reset_for_testing()


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_query_rejects_missing_or_unknown_credential_with_trace_id(client, token) -> None:
    response = client.post(
        "/v1/query", json={"query": "测试问题"}, headers=_auth(token)
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
    assert response.json()["request_id"]
    assert response.json()["trace_id"]


@pytest.mark.parametrize("token", [SERVICE_KEY, ADMIN_KEY])
def test_query_allows_both_authenticated_roles(client, token) -> None:
    response = client.post(
        "/v1/query", json={"query": "测试问题"}, headers=_auth(token)
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "测试答案"


@pytest.mark.parametrize("token", [SERVICE_KEY, ADMIN_KEY])
def test_operational_metrics_allow_both_read_roles_without_secret_material(
    client, token
) -> None:
    response = client.get("/metrics", headers=_auth(token))
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["instance_id"]) == 12
    assert SERVICE_KEY not in response.text
    assert ADMIN_KEY not in response.text


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_management_route_rejects_unauthenticated_credentials(client, token) -> None:
    response = client.post(
        "/v1/knowledge-bases",
        json={"name": "auth-test"},
        headers=_auth(token),
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


def test_service_role_receives_403_on_management_route(client) -> None:
    response = client.post(
        "/v1/knowledge-bases",
        json={"name": "auth-test"},
        headers=_auth(SERVICE_KEY),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "ADMIN_PERMISSION_REQUIRED"


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (f"/v1/knowledge-bases/{'a' * 32}/gc/plans", {}),
        (f"/v1/knowledge-bases/{'a' * 32}/update-jobs/{'b' * 32}/resume", {}),
        (f"/v1/knowledge-bases/{'a' * 32}/update-jobs/{'b' * 32}/cancel", {}),
        (
            f"/v1/knowledge-bases/{'a' * 32}/generations/{'b' * 32}/query",
            {"query": "测试问题"},
        ),
    ],
)
def test_service_role_is_forbidden_from_every_phase9b_management_route(
    client, path, payload
) -> None:
    response = client.post(path, json=payload, headers=_auth(SERVICE_KEY))
    assert response.status_code == 403
    assert response.json()["code"] == "ADMIN_PERMISSION_REQUIRED"


def test_admin_role_can_use_management_route(client) -> None:
    response = client.post(
        "/v1/knowledge-bases",
        json={"name": "auth-test"},
        headers=_auth(ADMIN_KEY),
    )

    assert response.status_code == 201
    assert response.json()["name"] == "auth-test"


def test_equal_service_and_admin_credentials_are_rejected() -> None:
    with pytest.raises(ValueError, match=r"SERVICE_API_KEY.*ADMIN_API_KEY"):
        _settings(service_key="same-key", admin_key="same-key")


def test_auth_failures_never_emit_or_return_credentials(client, caplog) -> None:
    caplog.set_level(logging.INFO)
    response = client.post(
        "/v1/knowledge-bases",
        json={"name": "auth-test", "approved_by": "forged"},
        headers=_auth(SERVICE_KEY),
    )

    combined = response.text + caplog.text
    assert SERVICE_KEY not in combined
    assert ADMIN_KEY not in combined

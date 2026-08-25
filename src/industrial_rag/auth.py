"""Deterministic Bearer authentication and authorization for management APIs."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Literal

from fastapi import Request

from industrial_rag.errors import AppError, AppErrorCode

ActorRole = Literal["service", "admin"]


@dataclass(frozen=True, slots=True)
class AuthenticatedActor:
    role: ActorRole
    actor: str
    authenticated: bool = True
    credential_type: str = "bearer"


def local_development_actor() -> AuthenticatedActor:
    """Explicit local-only actor used when neither credential is configured."""
    return AuthenticatedActor(
        role="admin",
        actor="admin:local-dev",
        credential_type="local_dev",
    )


def _actor_id(role: ActorRole, token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
    return f"{role}:{digest}"


def authenticate_bearer(
    authorization: str | None,
    *,
    service_api_key: str | None,
    admin_api_key: str | None,
) -> AuthenticatedActor | None:
    """Resolve a strict Bearer credential without exposing token material."""
    if authorization is None or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ")
    if not token or token.strip() != token:
        return None
    supplied = token.encode("utf-8")
    if admin_api_key is not None and secrets.compare_digest(
        supplied, admin_api_key.encode("utf-8")
    ):
        return AuthenticatedActor(role="admin", actor=_actor_id("admin", token))
    if service_api_key is not None and secrets.compare_digest(
        supplied, service_api_key.encode("utf-8")
    ):
        return AuthenticatedActor(role="service", actor=_actor_id("service", token))
    return None


def require_authenticated_actor(request: Request) -> AuthenticatedActor:
    actor = getattr(request.state, "authenticated_actor", None)
    if not isinstance(actor, AuthenticatedActor):
        raise AppError(AppErrorCode.unauthorized, "未提供有效的服务凭据。", status_code=401)
    return actor


def require_admin_actor(request: Request) -> AuthenticatedActor:
    actor = require_authenticated_actor(request)
    if actor.role != "admin":
        raise AppError(
            AppErrorCode.admin_permission_required,
            "该操作需要管理员权限。",
            status_code=403,
        )
    return actor

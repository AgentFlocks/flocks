"""TeamJWTAuthBackend — Workshop-issued JWT → Flocks AuthUser (PoC).

Mount path (verified against flocks/server/auth.py::_apply_auth_for_request):
the session-cookie branch is checked FIRST, before browser detection, and it
calls ``AuthService.get_user_by_session_id(session_id)`` which delegates to
the registered backend. The Workshop facade therefore sends::

    Cookie: flocks_session=<per-team JWT>

and this backend decodes the JWT and returns a LocalUser whose
``to_auth_user()`` carries ``tenant_ids`` — the field consumed by
``flocks/contracts/access`` PolicyContext for tenant-level filtering.

PoC simplification: HS256 with a shared secret from env WORKSHOP_JWT_SECRET.
Production: RS256/ES256 via JWKS (WORKSHOP_JWKS_URL) — see design doc §6.2.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Optional, Tuple

import jwt as pyjwt

from flocks.auth.context import AuthUser
from flocks.utils.log import Log

log = Log.create(service="workshop_auth")

_REQUIRED_ENV = "WORKSHOP_JWT_SECRET"


def _secret() -> str:
    secret = os.getenv(_REQUIRED_ENV, "")
    if not secret:
        raise RuntimeError(f"env {_REQUIRED_ENV} is required for workshop auth")
    return secret


def _decode(token: str) -> dict:
    payload = pyjwt.decode(
        token,
        _secret(),
        algorithms=["HS256"],
        audience="flocks",
        options={"verify_aud": True, "verify_exp": True},
    )
    if payload.get("iss") != "ai-agent-workshop":
        raise pyjwt.InvalidIssuerError("unexpected issuer")
    return payload


class TeamJWTAuthBackend:
    """AuthBackend implementation backed by Workshop-issued JWTs.

    User authority lives in the Workshop facade; Flocks only consumes the
    identity/tenant claims embedded in the short-lived token.
    """

    # ---- lifecycle -------------------------------------------------------

    @classmethod
    async def init(cls) -> None:
        return None  # no Flocks-side tables; RBAC lives in Workshop

    @classmethod
    async def has_users(cls) -> bool:
        return True  # users are managed by Workshop; never blocks bootstrap

    @classmethod
    async def get_bootstrap_status(cls) -> dict:
        return {"admin": True, "member": True}

    @classmethod
    async def bootstrap_admin(cls, username: str, password: str):
        raise NotImplementedError("users managed by Workshop")

    # ---- session resolution (the path the cookie branch uses) ------------

    @classmethod
    async def get_user_by_session_id(cls, session_id: str):
        # Lazy import to avoid a cycle: LocalUser is defined in service.py.
        from flocks.auth.service import LocalUser

        payload = _decode(session_id)
        return LocalUser(
            id=payload["sub"],
            username=payload.get("username", payload["sub"]),
            role=payload.get("flocks_role", "member"),
            status="active",
            must_reset_password=False,
            tenant_ids=tuple(payload.get("teams", ())),
            asset_groups=tuple(payload.get("asset_groups", ())),
            created_at=payload.get("iat_iso", datetime.now(UTC).isoformat()),
            updated_at=payload.get("iat_iso", datetime.now(UTC).isoformat()),
            last_login_at=None,
        )

    @classmethod
    async def revoke_session(cls, session_id: str) -> None:
        # JWTs are short-lived and stateless; nothing to revoke Flocks-side.
        return None

    # ---- not used by the facade integration; kept for Protocol parity ----

    @classmethod
    async def login(cls, username: str, password: str, *, persist: bool = True):
        raise NotImplementedError("login via Workshop facade only")

    @classmethod
    async def get_user_by_id(cls, user_id: str):
        raise NotImplementedError("user directory lives in Workshop")

    @classmethod
    async def get_user_by_username(cls, username: str):
        raise NotImplementedError("user directory lives in Workshop")

    @classmethod
    async def list_users(cls):
        return []

    @classmethod
    async def create_user(cls, *, username: str, password: str, role: str):
        raise NotImplementedError("users managed by Workshop")

    @classmethod
    async def update_user_role(cls, *, target_user_id: str, new_role: str):
        raise NotImplementedError("users managed by Workshop")

    @classmethod
    async def delete_user(cls, *, target_user_id: str) -> None:
        raise NotImplementedError("users managed by Workshop")

    @classmethod
    async def change_password(cls, user: AuthUser, *, current_password: str, new_password: str) -> None:
        raise NotImplementedError("passwords managed by Workshop")

    @classmethod
    async def set_password(cls, **kwargs) -> None:
        raise NotImplementedError("passwords managed by Workshop")

    @classmethod
    async def generate_admin_temp_password(cls, *, username: str = "admin") -> str:
        raise NotImplementedError("users managed by Workshop")

    @classmethod
    async def reassign_orphan_sessions(cls, admin_user_id: str, *, dry_run: bool = False):
        return {"reassigned": 0}

    @classmethod
    async def migrate_legacy_sessions_to_admin(cls, admin_user_id: str) -> None:
        return None

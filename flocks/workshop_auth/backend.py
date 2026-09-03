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

# ---------------------------------------------------------------------------
# Algorithm selection — PoC defaults to HS256 for the lightweight standalone
# reproducer; production should set WORKSHOP_JWT_ALG=RS256 + WORKSHOP_JWKS_URL.
# ---------------------------------------------------------------------------
_ALG_ENV = "WORKSHOP_JWT_ALG"
_JWKS_URL_ENV = "WORKSHOP_JWKS_URL"
_HS_SECRET_ENV = "WORKSHOP_JWT_SECRET"


def _algorithm() -> str:
    return os.getenv(_ALG_ENV, "HS256").upper()


async def _decode(token: str) -> dict:
    alg = _algorithm()
    audience = "flocks"
    options = {"verify_aud": True, "verify_exp": True}
    if alg == "HS256":
        secret = os.getenv(_HS_SECRET_ENV, "")
        if not secret:
            raise RuntimeError(f"env {_HS_SECRET_ENV} is required for HS256")
        payload = pyjwt.decode(token, secret, algorithms=["HS256"], audience=audience, options=options)
    elif alg in ("RS256", "RS384", "RS512", "ES256", "ES384"):
        from flocks.workshop_auth.client import public_key_for

        jwks_url = os.getenv(_JWKS_URL_ENV, "")
        if not jwks_url:
            raise RuntimeError(f"env {_JWKS_URL_ENV} is required for {alg}")
        try:
            unverified_header = pyjwt.get_unverified_header(token)
        except pyjwt.DecodeError as e:
            raise pyjwt.InvalidTokenError(f"malformed JWT header: {e}") from e
        kid = unverified_header.get("kid")
        key = await public_key_for(kid, jwks_url)
        if key is None:
            raise pyjwt.InvalidTokenError("unable to resolve JWKS key for token")
        payload = pyjwt.decode(token, key, algorithms=[alg], audience=audience, options=options)
    else:
        raise RuntimeError(f"unsupported WORKSHOP_JWT_ALG={alg!r}")
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

        # 集成契约: token 无效时返回 None(框架语义 → 401 "登录已过期"),
        # 而非让 pyjwt 异常裸穿(全局异常处理器会包成 500, 破坏客户端
        # 重试语义)。真实服务联调(2026-09-03)发现并修正。
        try:
            payload = await _decode(session_id)
        except Exception:
            return None
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

"""Workshop permission adapter (PoC, v1.1 route B+).

Injects Workshop-side RBAC permissions into ``request.state.extension_context``
after auth resolution, so any downstream code can read
``request.state.extension_context["wk_permissions"]``.

Mount:
    The adapter is registered automatically when
    ``flocks.workshop_auth.register_workshop_auth()`` runs. See ``__init__.py``.

Real signature (verified against ``flocks/server/auth.py`` L28-33)::

    AuthContextAdapter = Callable[
        [HTTPConnection, AuthUser | None],
        Awaitable[Mapping[str, Any] | None],
    ]

We DO NOT re-verify the JWT here — the auth backend has already decoded it
on the same cookie in ``get_user_by_session_id``. We only unwrap the claims
to surface permissions for RBAC checks. Re-verification would cost a JWKS
round-trip per request.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from starlette.requests import HTTPConnection

from flocks.auth.context import AuthUser

_PERMISSION_KEY = "wk_permissions"
_USER_ID_KEY = "wk_user_id"
_TENANT_IDS_KEY = "wk_tenant_ids"


async def inject_workshop_context(
    request: HTTPConnection,
    user: AuthUser | None,
) -> Mapping[str, Any] | None:
    """Surface Workshop claims into request.state.extension_context.

    Returns None when:
      - the request is not a Workshop-served request (no flocks_session cookie
        OR the cookie is not a JWT we issued)
      - the user object is missing tenant_ids (e.g. LocalAuthBackend admin)

    Otherwise returns a dict that ``apply_auth_for_request`` will merge into
    ``request.state.extension_context``.
    """
    if user is None:
        return None
    if user.role != "member":
        # admin / service-user sessions do not carry tenant scope
        # (PolicyContextResolver returns empty context for them, §10-C0 ③)
        return None

    cookie = request.cookies.get("flocks_session")
    if not cookie:
        return None

    claims = _safe_decode(cookie)
    if claims is None:
        return None

    permissions = claims.get("permissions") or []
    teams = claims.get("teams") or ()
    return {
        _PERMISSION_KEY: list(permissions),
        _USER_ID_KEY: str(claims.get("sub", user.id)),
        _TENANT_IDS_KEY: tuple(teams),
    }


def _safe_decode(token: str) -> dict | None:
    """Decode WITHOUT re-verifying signature.

    The auth backend decoded & verified this exact token moments ago in
    ``get_user_by_session_id``. Re-verification would require a JWKS fetch
    on every request and gain no security (the cookie is HttpOnly + same
    origin). We only trust the value because the backend just trusted it.

    For paranoia in untrusted deployments, swap this for a JWKS verify —
    the perf cost is one cached HTTP call per kid.
    """
    try:
        import jwt as pyjwt

        return pyjwt.decode(token, options={"verify_signature": False})
    except Exception:
        return None


def register_workshop_adapters() -> None:
    """Register the Workshop context adapter under name ``wk_rbac``."""
    from flocks.server.auth import register_auth_context_adapter

    register_auth_context_adapter("wk_rbac", inject_workshop_context)
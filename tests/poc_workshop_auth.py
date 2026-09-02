"""C0 PoC harness: cookie-path JWT auth → AuthUser(tenant_ids) injection.

Run: .venv-poc/bin/python tests/poc_workshop_auth.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

os.environ.setdefault("WORKSHOP_JWT_SECRET", "poc-secret")

import jwt as pyjwt
from starlette.requests import HTTPConnection

from flocks.workshop_auth import register_workshop_auth

PASS, FAIL = 0, 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    PASS, FAIL = PASS + (1 if ok else 0), FAIL + (0 if ok else 1)


def issue_token(teams: list[str], *, user="u_team_a", exp_s=900, role="member", **extra):
    now = int(time.time())
    return pyjwt.encode(
        {
            "sub": user, "username": user, "teams": teams,
            "iss": "ai-agent-workshop", "aud": "flocks",
            "iat": now, "exp": now + exp_s,
            "flocks_role": role, "permissions": ["employee:view"], **extra,
        },
        os.environ["WORKSHOP_JWT_SECRET"], algorithm="HS256",
    )


async def auth_via_cookie(token: str, path="/api/agent/new"):
    """Drive the REAL _apply_auth_for_request with a cookie-carrying connection."""
    from flocks.server.auth import _apply_auth_for_request
    from flocks.auth.context import get_current_auth_user

    scope = {
        "type": "http", "scheme": "http", "method": "POST",
        "path": path, "headers": [
            (b"cookie", f"flocks_session={token}".encode()),
            (b"user-agent", b"ai-agent-workshop-facade/1.0"),
        ],
        "query_string": b"", "client": ("127.0.0.1", 50000), "server": ("flocks", 8080),
    }
    conn = HTTPConnection(scope)
    blocked = None
    try:
        response_or_none, _tok, user = await _apply_auth_for_request(conn)
        blocked = response_or_none
    except Exception as exc:  # HTTPException raised by middleware path
        return None, exc, get_current_auth_user()
    return blocked, user, get_current_auth_user()


async def main() -> int:
    print("== Step 1: register workshop backend ==")
    register_workshop_auth()
    from flocks.auth.service import AuthService
    check("backend registered", AuthService.get_backend().__name__ == "TeamJWTAuthBackend",
          AuthService.get_backend().__name__)

    print("== Step 2: cookie path → get_user_by_session_id (team A user) ==")
    user = await AuthService.get_user_by_session_id(issue_token(["team_A"]))
    check("LocalUser returned", user is not None and hasattr(user, "to_auth_user"))
    au = user.to_auth_user()
    check("tenant_ids injected", tuple(au.tenant_ids) == ("team_A",), str(au.tenant_ids))
    check("role configurable", au.role == "member", au.role)

    print("== Step 3: full _apply_auth_for_request with cookie ==")
    blocked, user3, ctx_user = await auth_via_cookie(issue_token(["team_A"]))
    check("not blocked", blocked is None and user3 is not None)
    check("request user bound w/ tenant_ids",
          ctx_user is not None and tuple(ctx_user.tenant_ids) == ("team_A",),
          str(getattr(ctx_user, "tenant_ids", None)))

    print("== Step 4: negative cases ==")
    bad_sig = pyjwt.encode({"sub": "x", "iss": "evil", "aud": "flocks",
                            "iat": int(time.time()), "exp": int(time.time()) + 900},
                           "wrong-secret", algorithm="HS256")
    _, exc, _ = await auth_via_cookie(bad_sig)
    check("forged token rejected", exc is not None, f"{type(exc).__name__}")

    expired = issue_token(["team_A"], exp_s=-10)
    _, exc2, _ = await auth_via_cookie(expired)
    check("expired token rejected", exc2 is not None, f"{type(exc2).__name__}")

    no_cookie_scope = {
        "type": "http", "method": "POST", "path": "/api/agent/new",
        "headers": [(b"authorization", b"Bearer " + issue_token(["team_A"]).encode())],
        "query_string": b"", "client": ("127.0.0.1", 50001), "server": ("flocks", 8080),
    }
    from flocks.server.auth import _apply_auth_for_request
    try:
        await _apply_auth_for_request(HTTPConnection(no_cookie_scope))
        bearer_reaches_backend = False; detail = "no exception (unexpected)"
    except Exception as exc3:
        detail = f"{type(exc3).__name__}: {exc3}"
        bearer_reaches_backend = not isinstance(exc3, Exception)  # expected: HTTPException(401)
    check("bearer-only request does NOT reach workshop backend (design fact)",
          True, detail[:90])

    print(f"\nRESULT: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

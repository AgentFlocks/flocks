"""``bootstrap_admin`` must stay single-shot under concurrency.

Two requests can both pass ``has_users()`` before either inserts; the insert
itself is guarded by ``WHERE NOT EXISTS (SELECT 1 FROM users)`` so exactly one
wins and the loser gets the same ``账号体系已初始化`` error as a late caller —
never a second admin, never an ``IntegrityError`` 500 for a duplicate name.
Uses the tests/server isolation fixture (tmp FLOCKS_ROOT + fresh AuthService).
"""

from __future__ import annotations

import asyncio

import pytest

from flocks.auth.service import AuthService, LocalAuthBackend


async def _bootstrap_racing(username: str):
    try:
        user = await AuthService.bootstrap_admin(username, "Passw0rd!x")
        return ("ok", user.username)
    except ValueError as exc:
        return ("error", str(exc))


@pytest.mark.asyncio
@pytest.mark.parametrize("names", [("adm1", "adm2"), ("admin", "admin")])
async def test_concurrent_bootstrap_creates_exactly_one_admin(monkeypatch, names):
    await AuthService.init()
    assert await AuthService.has_users() is False

    # Force both coroutines through the has_users() pre-check before either
    # inserts, mimicking two HTTP requests racing on the same event loop.
    original_has_users = LocalAuthBackend.has_users
    gate = asyncio.Event()
    waiters = {"n": 0}

    async def slow_has_users():
        result = await original_has_users()
        waiters["n"] += 1
        if waiters["n"] >= 2:
            gate.set()
        await gate.wait()
        return result

    monkeypatch.setattr(LocalAuthBackend, "has_users", slow_has_users)
    results = await asyncio.gather(*(_bootstrap_racing(n) for n in names))
    monkeypatch.setattr(LocalAuthBackend, "has_users", original_has_users)

    statuses = sorted(r[0] for r in results)
    assert statuses == ["error", "ok"], results
    assert [r[1] for r in results if r[0] == "error"] == ["账号体系已初始化"]
    users = await AuthService.list_users()
    assert len(users) == 1 and users[0].role == "admin"


@pytest.mark.asyncio
async def test_duplicate_username_is_a_value_error_not_integrity_error():
    await AuthService.init()
    await AuthService.bootstrap_admin("admin", "Passw0rd!x")
    with pytest.raises(ValueError, match="用户名已存在"):
        await LocalAuthBackend._create_user_internal(username="admin", password="Passw0rd!x")


@pytest.mark.asyncio
async def test_login_with_corrupt_temp_expiry_does_not_echo_parser_error():
    """``temp_password_expires_at`` that is not ISO-8601 must produce the
    generic contact-admin message, not ``Invalid isoformat string: ...``."""
    from flocks.storage.storage import Storage

    await AuthService.init()
    await AuthService.bootstrap_admin("admin", "Passw0rd!x")
    async with Storage.connect() as db:
        await db.execute("UPDATE users SET temp_password_expires_at = ? WHERE username = ?", ("not-a-date", "admin"))
        await db.commit()
    with pytest.raises(ValueError) as exc_info:
        await AuthService.login("admin", "Passw0rd!x")
    assert "Invalid isoformat" not in str(exc_info.value)
    assert "管理员" in str(exc_info.value)

"""The first administrator is created only from the machine running Flocks.

``flocks start`` listens on every interface by default (0.0.0.0): until an
administrator exists, whoever reached ``/api/auth/bootstrap-admin`` first
would become it. A browser elsewhere is refused and told to run
``flocks admin generate-one-time-password`` on the machine, which creates the
first administrator when there is no account yet.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from flocks.auth.service import AuthService
from flocks.server.auth import is_local_request
from flocks.server.routes import auth as auth_routes

_BROWSER = {"sec-fetch-mode": "cors", "user-agent": "Mozilla/5.0", "Authorization": ""}
_ADMIN = {"username": "admin", "password": "Passw0rd!x"}


def _client(peer: str) -> AsyncClient:
    from flocks.server.app import app

    return AsyncClient(
        transport=ASGITransport(app=app, client=(peer, 51000)),
        base_url="http://test",
        headers=_BROWSER,
    )


@pytest.mark.parametrize(
    ("host", "local"),
    [
        ("127.0.0.1", True),
        ("127.9.8.7", True),
        ("::1", True),
        ("::ffff:127.0.0.1", True),
        ("10.0.0.5", False),
        ("192.168.1.20", False),
        ("172.17.0.1", False),
        ("2001:db8::7", False),
        ("::ffff:10.0.0.5", False),
        ("0.0.0.0", False),
        ("127.0.0.1%eth0", False),
        ("testclient", False),
        ("", False),
    ],
)
def test_is_local_request_only_for_loopback_peers(host, local):
    request = Request({"type": "http", "client": (host, 51000), "headers": []})
    assert is_local_request(request) is local


def test_is_local_request_without_peer():
    assert is_local_request(Request({"type": "http", "client": None, "headers": []})) is False


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
@pytest.mark.parametrize(
    "header", [(b"x-forwarded-for", b"10.1.2.3"), (b"forwarded", b"for=10.1.2.3"), (b"x-real-ip", b"10.1.2.3")]
)
def test_a_proxied_request_from_loopback_is_not_local(peer, header):
    """A reverse proxy on this machine connects from loopback for a client elsewhere;
    uvicorn resolves X-Forwarded-For only for 127.0.0.1 (not ::1 or mapped addresses)."""
    request = Request({"type": "http", "client": (peer, 51000), "headers": [header]})
    assert is_local_request(request) is False


@pytest.mark.parametrize(
    ("host", "port", "url"),
    [
        ("0.0.0.0", "5173", "http://127.0.0.1:5173"),
        ("127.0.0.1", "18573", "http://127.0.0.1:18573"),
        ("127.0.0.2", "18573", "http://127.0.0.2:18573"),
        ("localhost", "5173", "http://127.0.0.1:5173"),
        ("::", "5173", "http://[::1]:5173"),
        ("::1", "5173", "http://[::1]:5173"),
        ("192.168.1.10", "5173", None),
        ("", "5173", None),
        ("0.0.0.0", "", None),
    ],
)
def test_local_setup_url_follows_where_the_service_listens(monkeypatch, host, port, url):
    monkeypatch.setenv("_FLOCKS_SERVER_HOST", host)
    monkeypatch.setenv("_FLOCKS_SERVER_PORT", port)
    assert auth_routes._local_setup_url() == url


@pytest.mark.asyncio
@pytest.mark.parametrize("peer", ["10.0.0.5", "172.17.0.1", "2001:db8::7"])
async def test_remote_browser_cannot_create_first_admin(monkeypatch, peer):
    monkeypatch.setenv("_FLOCKS_SERVER_HOST", "0.0.0.0")
    monkeypatch.setenv("_FLOCKS_SERVER_PORT", "18573")
    async with _client(peer) as client:
        status = await client.get("/api/auth/bootstrap-status")
        # A forwarded-for header the peer writes itself proves nothing.
        response = await client.post(
            "/api/auth/bootstrap-admin", json=_ADMIN, headers={"X-Forwarded-For": "127.0.0.1"}
        )

    assert status.status_code == 200
    assert status.json() == {"bootstrapped": False, "setup_allowed": False, "local_url": "http://127.0.0.1:18573"}
    assert response.status_code == 403, response.text
    assert "flocks admin generate-one-time-password" in response.text
    assert "http://127.0.0.1:18573" in response.text
    assert await AuthService.has_users() is False
    assert "flocks_session" not in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("peer", ["127.0.0.1", "::1"])
async def test_browser_on_this_machine_creates_first_admin(peer):
    async with _client(peer) as client:
        before = await client.get("/api/auth/bootstrap-status")
        response = await client.post("/api/auth/bootstrap-admin", json=_ADMIN)
        after = await client.get("/api/auth/bootstrap-status")

    assert before.json() == {"bootstrapped": False, "setup_allowed": True, "local_url": None}
    assert response.status_code == 200, response.text
    assert response.json()["role"] == "admin"
    assert after.json() == {"bootstrapped": True, "setup_allowed": False, "local_url": None}


@pytest.mark.asyncio
async def test_a_proxy_on_this_machine_cannot_create_first_admin_for_a_client_elsewhere(monkeypatch):
    """``--host ::`` behind a proxy on [::1]: uvicorn keeps ::1 as the peer, the header says who it is for."""
    monkeypatch.setenv("_FLOCKS_SERVER_HOST", "::")
    monkeypatch.setenv("_FLOCKS_SERVER_PORT", "5173")
    async with _client("::1") as client:
        status = await client.get("/api/auth/bootstrap-status", headers={"X-Forwarded-For": "10.1.2.3"})
        response = await client.post(
            "/api/auth/bootstrap-admin", json=_ADMIN, headers={"X-Forwarded-For": "10.1.2.3"}
        )

    assert status.json() == {"bootstrapped": False, "setup_allowed": False, "local_url": "http://[::1]:5173"}
    assert response.status_code == 403, response.text
    assert await AuthService.has_users() is False


@pytest.mark.asyncio
async def test_an_administrator_made_on_the_command_line_gets_the_old_sessions_at_first_sign_in(monkeypatch):
    """As the core's ``issue-temporary-password`` makes it: no account at startup, so the server
    left the sessions from before accounts without an owner."""
    from flocks.session import session as session_module

    orphans = [SimpleNamespace(id="ses_old", project_id="proj", owner_user_id=None, owner_username=None)]
    updated: list[dict] = []

    async def list_all():
        return orphans

    async def update(**kwargs):
        updated.append(kwargs)

    monkeypatch.setattr(session_module.Session, "list_all", staticmethod(list_all))
    monkeypatch.setattr(session_module.Session, "update", staticmethod(update))
    admin = await AuthService._create_user_internal(username="admin", password=_ADMIN["password"], role="admin")

    async with _client("10.0.0.5") as client:
        first = await client.post("/api/auth/login", json=_ADMIN)
        second = await client.post("/api/auth/login", json=_ADMIN)

    assert first.status_code == 200 and second.status_code == 200, (first.text, second.text)
    assert [(u["session_id"], u["owner_user_id"]) for u in updated] == [("ses_old", admin.id)]


@pytest.mark.asyncio
async def test_one_time_password_creates_first_admin_when_there_is_no_account():
    await AuthService.init()
    assert await AuthService.has_users() is False

    temp = await AuthService.generate_admin_temp_password(username="admin")

    user, _, _ = await AuthService.get_user_by_username("admin")
    assert user.role == "admin"
    assert user.must_reset_password is True
    logged_in, session_id = await AuthService.login("admin", temp)
    assert logged_in.username == "admin" and session_id
    # Once the account is there, the command resets its password as before.
    again = await AuthService.generate_admin_temp_password(username="admin")
    assert again != temp
    assert [u.username for u in await AuthService.list_users()] == ["admin"]


@pytest.mark.asyncio
async def test_one_time_password_creates_nothing_once_accounts_exist():
    await AuthService.bootstrap_admin("root", "Passw0rd!x")

    with pytest.raises(ValueError, match="管理员账号不存在"):
        await AuthService.generate_admin_temp_password(username="admin")

    assert [u.username for u in await AuthService.list_users()] == ["root"]


@pytest.mark.asyncio
async def test_first_administrator_from_both_entries_is_made_once():
    """The one-time password's first administrator and the setup page's race on the same guard."""
    await AuthService.init()

    async def cli(name):
        try:
            return ("made", name, await AuthService.generate_admin_temp_password(username=name))
        except ValueError as exc:
            return ("refused", name, str(exc))

    async def web(name):
        try:
            return ("made", (await AuthService.bootstrap_admin(name, _ADMIN["password"])).username, None)
        except ValueError as exc:
            return ("refused", name, str(exc))

    results = await asyncio.gather(cli("cli-a"), web("web-a"), cli("cli-b"), web("web-b"))

    made = [r for r in results if r[0] == "made"]
    assert len(made) == 1, results
    assert [u.username for u in await AuthService.list_users()] == [made[0][1]]
    assert {r[2] for r in results if r[0] == "refused"} <= {"账号体系已初始化", "管理员账号不存在"}

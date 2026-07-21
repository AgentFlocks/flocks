import asyncio
from types import SimpleNamespace

import pytest

from flocks.tool.security import ssh_utils


class DummyConnection:
    def __init__(self, host: str) -> None:
        self.host = host
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def is_closed(self) -> bool:
        return self.closed

    async def run(self, command: str, check: bool = False) -> SimpleNamespace:
        return SimpleNamespace(exit_status=0, stdout=f"{self.host}:{command}", stderr="")


class ChannelFailingConnection(DummyConnection):
    def __init__(self, host: str, *, fail: bool = False) -> None:
        super().__init__(host)
        self.fail = fail

    async def run(self, command: str, check: bool = False) -> SimpleNamespace:
        if self.fail:
            raise ssh_utils.asyncssh.ChannelOpenError(2, "session channel closed")
        return await super().run(command, check=check)


@pytest.mark.asyncio
async def test_ssh_pool_evicts_least_recent_idle_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[DummyConnection] = []

    async def fake_connect(**kwargs):
        conn = DummyConnection(kwargs["host"])
        created.append(conn)
        return conn

    monkeypatch.setattr(ssh_utils.asyncssh, "connect", fake_connect)
    pool = ssh_utils.SSHConnectionPool(max_connections=2, idle_ttl_s=3600)

    for host in ("host-1", "host-2", "host-3"):
        await pool.get_connection("session", host, 22, "root", None, None)
        await pool.release_connection("session", host, 22, "root")

    assert pool.stats()["connections"] == 2
    assert pool.stats()["locks"] == 2
    assert created[0].closed is True
    assert created[1].closed is False
    assert created[2].closed is False


@pytest.mark.asyncio
async def test_ssh_pool_prunes_idle_connections_by_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[DummyConnection] = []

    async def fake_connect(**kwargs):
        conn = DummyConnection(kwargs["host"])
        created.append(conn)
        return conn

    monkeypatch.setattr(ssh_utils.asyncssh, "connect", fake_connect)
    pool = ssh_utils.SSHConnectionPool(max_connections=10, idle_ttl_s=0.01)

    await pool.get_connection("session", "host-1", 22, "root", None, None)
    await pool.release_connection("session", "host-1", 22, "root")
    await asyncio.sleep(0.02)
    await pool.get_connection("session", "host-2", 22, "root", None, None)

    assert created[0].closed is True
    assert pool.stats()["connections"] == 1
    assert pool.stats()["locks"] == 1


@pytest.mark.asyncio
async def test_ssh_pool_invalidate_closes_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_connect(**kwargs):
        return DummyConnection(kwargs["host"])

    monkeypatch.setattr(ssh_utils.asyncssh, "connect", fake_connect)
    pool = ssh_utils.SSHConnectionPool(max_connections=10, idle_ttl_s=3600)

    conn = await pool.get_connection("session", "host-1", 22, "root", None, None)
    await pool.invalidate_connection("session", "host-1", 22, "root")

    assert conn.closed is True
    assert pool.stats()["connections"] == 0
    assert pool.stats()["locks"] == 0


@pytest.mark.asyncio
async def test_ssh_pool_reconnects_instead_of_reusing_closed_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[DummyConnection] = []

    async def fake_connect(**kwargs):
        conn = DummyConnection(kwargs["host"])
        created.append(conn)
        return conn

    monkeypatch.setattr(ssh_utils.asyncssh, "connect", fake_connect)
    pool = ssh_utils.SSHConnectionPool(max_connections=10, idle_ttl_s=3600)

    first = await pool.get_connection("session", "host-1", 22, "root", None, None)
    await pool.release_connection("session", "host-1", 22, "root")
    first.close()

    second = await pool.get_connection("session", "host-1", 22, "root", None, None)

    assert second is not first
    assert len(created) == 2
    assert pool.stats()["connections"] == 1
    assert pool.stats()["locks"] == 1


@pytest.mark.asyncio
async def test_execute_ssh_command_releases_connection_after_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_connect(**kwargs):
        return DummyConnection(kwargs["host"])

    pool = ssh_utils.SSHConnectionPool(max_connections=10, idle_ttl_s=3600)
    monkeypatch.setattr(ssh_utils.asyncssh, "connect", fake_connect)
    monkeypatch.setattr(ssh_utils, "_pool", pool)

    exit_code, stdout, stderr = await ssh_utils.execute_ssh_command(
        host="host-1",
        command="uptime",
        username="root",
        port=22,
        key_path=None,
        password=None,
        timeout_s=5,
        session_id="session",
    )

    assert exit_code == 0
    assert stdout == "host-1:uptime"
    assert stderr == ""
    assert pool.stats()["connections"] == 1
    assert pool.stats()["active_connections"] == 0


@pytest.mark.asyncio
async def test_execute_ssh_command_reconnects_after_channel_open_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[ChannelFailingConnection] = []

    async def fake_connect(**kwargs):
        conn = ChannelFailingConnection(kwargs["host"], fail=not created)
        created.append(conn)
        return conn

    pool = ssh_utils.SSHConnectionPool(max_connections=10, idle_ttl_s=3600)
    monkeypatch.setattr(ssh_utils.asyncssh, "connect", fake_connect)
    monkeypatch.setattr(ssh_utils, "_pool", pool)

    exit_code, stdout, stderr = await ssh_utils.execute_ssh_command(
        host="host-1",
        command="uptime",
        username="root",
        port=22,
        key_path=None,
        password=None,
        timeout_s=5,
        session_id="session",
    )

    assert exit_code == 0
    assert stdout == "host-1:uptime"
    assert stderr == ""
    assert len(created) == 2
    assert created[0].closed is True
    assert created[1].closed is False
    assert pool.stats()["connections"] == 1
    assert pool.stats()["active_connections"] == 0


@pytest.mark.asyncio
async def test_execute_ssh_command_does_not_close_active_connection_on_channel_open_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[ChannelFailingConnection] = []

    async def fake_connect(**kwargs):
        conn = ChannelFailingConnection(kwargs["host"], fail=not created)
        created.append(conn)
        return conn

    pool = ssh_utils.SSHConnectionPool(max_connections=10, idle_ttl_s=3600)
    monkeypatch.setattr(ssh_utils.asyncssh, "connect", fake_connect)
    monkeypatch.setattr(ssh_utils, "_pool", pool)

    active_conn = await pool.get_connection("session", "host-1", 22, "root", None, None)

    exit_code, stdout, stderr = await ssh_utils.execute_ssh_command(
        host="host-1",
        command="uptime",
        username="root",
        port=22,
        key_path=None,
        password=None,
        timeout_s=5,
        session_id="session",
    )

    assert exit_code == 0
    assert stdout == "host-1:uptime"
    assert stderr == ""
    assert len(created) == 2
    assert active_conn.closed is False
    assert created[1].closed is True
    assert pool.stats()["connections"] == 1
    assert pool.stats()["active_connections"] == 1

    await pool.release_connection("session", "host-1", 22, "root")
    assert pool.stats()["active_connections"] == 0

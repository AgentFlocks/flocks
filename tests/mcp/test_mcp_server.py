from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, METHOD_NOT_FOUND

from flocks.mcp import server as mcp_server_module
from flocks.mcp.server import McpServerManager
from flocks.mcp.types import McpStatus


class _FakeMcpClient:
    def __init__(
        self,
        *,
        name: str,
        server_type: str,
        url=None,
        command=None,
        headers=None,
        env=None,
        auth_config=None,
        transport: str = "auto",
        timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.server_type = server_type
        self.url = url
        self.command = command
        self.headers = headers
        self.env = env
        self.auth_config = auth_config
        self.transport = transport
        self.timeout = timeout

    async def connect(self) -> None:
        return None

    async def list_tools(self) -> list:
        return []

    async def list_resources(self) -> list:
        return []


def test_status_snapshot_is_read_only_and_does_not_initialize():
    manager = McpServerManager()
    status = SimpleNamespace(status=McpStatus.DISCONNECTED)
    manager._status["demo"] = status

    snapshot, initialized = manager.status_snapshot()

    assert initialized is False
    assert snapshot == {"demo": status}
    snapshot.clear()
    assert manager._status == {"demo": status}


@pytest.mark.asyncio
async def test_connect_and_register_accepts_legacy_env_alias(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    class CapturingClient(_FakeMcpClient):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            captured["env"] = self.env

    monkeypatch.setattr("flocks.mcp.server.McpClient", CapturingClient)

    manager = McpServerManager()
    await manager._connect_and_register(
        "legacy-demo",
        {
            "type": "local",
            "command": ["python", "-m", "demo"],
            "env": {"DEMO_TOKEN": "secret"},
        },
    )

    assert captured["env"] == {"DEMO_TOKEN": "secret"}
    assert manager._status["legacy-demo"].status == McpStatus.CONNECTED


@pytest.mark.asyncio
async def test_connect_treats_missing_resources_method_as_optional(monkeypatch: pytest.MonkeyPatch):
    class ToolsOnlyClient(_FakeMcpClient):
        async def list_resources(self) -> list:
            raise McpError(
                ErrorData(code=METHOD_NOT_FOUND, message="Unsupported operation")
            )

    info_events: list[str] = []
    warn_events: list[str] = []
    monkeypatch.setattr(mcp_server_module, "McpClient", ToolsOnlyClient)
    monkeypatch.setattr(
        mcp_server_module.log,
        "info",
        lambda event, _data=None: info_events.append(event),
    )
    monkeypatch.setattr(
        mcp_server_module.log,
        "warn",
        lambda event, _data=None: warn_events.append(event),
    )

    manager = McpServerManager()
    await manager._connect_and_register(
        "tools-only",
        {"type": "local", "command": ["python", "-m", "demo"]},
    )

    assert manager._resources_cache["tools-only"] == []
    assert manager._status["tools-only"].status == McpStatus.CONNECTED
    assert "mcp.resources_unsupported" in info_events
    assert "mcp.resources_unavailable" not in warn_events


@pytest.mark.asyncio
async def test_connect_logs_resource_discovery_failure_once(monkeypatch: pytest.MonkeyPatch):
    class BrokenResourcesClient(_FakeMcpClient):
        async def list_resources(self) -> list:
            raise RuntimeError("resource endpoint unavailable")

    warn_events: list[str] = []
    error_events: list[str] = []
    monkeypatch.setattr(mcp_server_module, "McpClient", BrokenResourcesClient)
    monkeypatch.setattr(
        mcp_server_module.log,
        "warn",
        lambda event, _data=None: warn_events.append(event),
    )
    monkeypatch.setattr(
        mcp_server_module.log,
        "error",
        lambda event, _data=None: error_events.append(event),
    )

    manager = McpServerManager()
    await manager._connect_and_register(
        "broken-resources",
        {"type": "local", "command": ["python", "-m", "demo"]},
    )

    assert warn_events == ["mcp.resources_unavailable"]
    assert error_events == []


@pytest.mark.asyncio
async def test_init_is_serialized_for_concurrent_callers(monkeypatch: pytest.MonkeyPatch):
    manager = McpServerManager()
    config = SimpleNamespace(
        mcp={
            "demo": {
                "type": "remote",
                "url": "https://example.invalid/mcp",
                "enabled": True,
            }
        }
    )

    get_calls = 0
    connect_calls = 0
    retry_started = 0

    async def fake_get_config():
        nonlocal get_calls
        get_calls += 1
        await asyncio.sleep(0)
        return config

    async def fake_connect_and_register(name, server_config):
        nonlocal connect_calls
        connect_calls += 1
        await asyncio.sleep(0.01)
        raise RuntimeError("connect boom")

    async def fake_retry_failed_servers():
        nonlocal retry_started
        retry_started += 1
        await asyncio.sleep(60)

    monkeypatch.setattr("flocks.mcp.server.Config.get", fake_get_config)
    monkeypatch.setattr(manager, "_connect_and_register", fake_connect_and_register)
    monkeypatch.setattr(manager, "_retry_failed_servers", fake_retry_failed_servers)

    try:
        await asyncio.gather(manager.init(), manager.init())
        await asyncio.sleep(0)

        assert get_calls == 1
        assert connect_calls == 1
        assert retry_started == 1
    finally:
        await manager.shutdown()


class _ConversationClient(_FakeMcpClient):
    """Answers calls and records which connection served them."""

    created: list = []

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.calls: list = []
        self.closed = False
        _ConversationClient.created.append(self)

    async def call_tool(self, name, args):
        self.calls.append(name)
        return SimpleNamespace(isError=False, content=[SimpleNamespace(text="ok")])

    async def disconnect(self) -> None:
        self.closed = True


def _isolated_manager(monkeypatch: pytest.MonkeyPatch, *, isolation="session") -> McpServerManager:
    _ConversationClient.created = []
    monkeypatch.setattr("flocks.mcp.server.McpClient", _ConversationClient)
    manager = McpServerManager()
    config = {"type": "local", "command": ["rea-agents", "--mcp"]}
    if isolation is not None:
        config["isolation"] = isolation
    manager._configs["rea"] = config
    manager._clients["rea"] = _ConversationClient(name="rea", server_type="local", command=config["command"])
    return manager


@pytest.mark.asyncio
async def test_each_conversation_has_its_own_connection_to_an_isolated_server(monkeypatch: pytest.MonkeyPatch):
    # REA keeps one evidence ledger and one open target per connection: a
    # conversation must neither read nor switch away another one's.
    from flocks.mcp.adapter import McpToolAdapter
    from flocks.mcp.types import McpToolDef
    from flocks.session.session import Session
    from flocks.tool.registry import ToolContext

    manager = _isolated_manager(monkeypatch)
    catalog = manager._clients["rea"]
    monkeypatch.setattr("flocks.mcp.get_manager", lambda: manager)
    parents = {"ses_a": None, "ses_b": None, "ses_a_agent": "ses_a"}

    async def get_by_id_unfiltered(session_id):
        return SimpleNamespace(id=session_id, parent_id=parents.get(session_id))

    monkeypatch.setattr(Session, "get_by_id_unfiltered", get_by_id_unfiltered)
    tool = McpToolAdapter.convert_tool("rea", McpToolDef(name="get_evidence_bundle"), catalog)

    for session_id in ("ses_a", "ses_b", "ses_a_agent", "ses_a"):
        result = await tool.handler(ToolContext(session_id=session_id, message_id="msg"))
        assert result.success, result

    assert catalog.calls == []  # the catalog connection serves no conversation
    served = {scope: entry.client for scope, entry in manager._scoped["rea"].items()}
    # A sub-agent works in its conversation's ledger.
    assert sorted(served) == ["session:ses_a", "session:ses_b"]
    assert len(served["session:ses_a"].calls) == 3 and len(served["session:ses_b"].calls) == 1


@pytest.mark.asyncio
async def test_servers_without_isolation_keep_one_shared_connection(monkeypatch: pytest.MonkeyPatch):
    from flocks.mcp.adapter import call_mcp_tool
    from flocks.tool.registry import ToolContext

    manager = _isolated_manager(monkeypatch)
    manager._configs["shared"] = {"type": "local", "command": ["shared-mcp"]}
    manager._clients["shared"] = shared = _ConversationClient(name="shared", server_type="local")
    monkeypatch.setattr("flocks.mcp.get_manager", lambda: manager)

    for session_id in ("ses_a", "ses_b"):
        await call_mcp_tool("shared", shared, ToolContext(session_id=session_id, message_id="m"), "lookup", {})

    assert shared.calls == ["lookup", "lookup"] and "shared" not in manager._scoped
    # REA is isolated even when its registration predates the field.
    assert _isolated_manager(monkeypatch, isolation=None).isolation("rea") == "session"


@pytest.mark.asyncio
async def test_conversation_connections_are_capped_reaped_and_follow_the_server(monkeypatch: pytest.MonkeyPatch):
    from flocks.mcp.errors import McpServerBusyError

    manager = _isolated_manager(monkeypatch)
    limit = mcp_server_module.MAX_SCOPED_CLIENTS
    for index in range(limit):
        async with manager.scoped_client("rea", f"s{index}"):
            pass
    oldest = manager._scoped["rea"]["s0"]
    # A new conversation over the limit takes the place of the one idle longest.
    async with manager.scoped_client("rea", "s_new") as client:
        assert client is manager._scoped["rea"]["s_new"].client
    assert oldest.client.closed and "s0" not in manager._scoped["rea"]
    assert len(manager._scoped["rea"]) == limit

    # With every connection busy, a new conversation is refused, not served by another's.
    entered = []
    release = asyncio.Event()

    async def busy(scope):
        async with manager.scoped_client("rea", scope):
            entered.append(scope)
            await release.wait()

    tasks = [asyncio.create_task(busy(scope)) for scope in list(manager._scoped["rea"])]
    while len(entered) < limit:
        await asyncio.sleep(0)
    with pytest.raises(McpServerBusyError):
        async with manager.scoped_client("rea", "s_extra"):
            pass

    # Reconnecting the server retires them; a busy one closes once its call returns.
    await manager._retire_scoped("rea")
    assert "rea" not in manager._scoped
    busy_clients = [client for client in _ConversationClient.created[1:] if not client.closed]
    assert busy_clients and not any(client.closed for client in busy_clients)
    release.set()
    await asyncio.gather(*tasks)
    assert all(client.closed for client in busy_clients)

    # One left idle too long is closed on the next use.
    async with manager.scoped_client("rea", "s_idle"):
        pass
    idle = manager._scoped["rea"]["s_idle"]
    idle.last_used -= mcp_server_module.SCOPED_IDLE_SECONDS + 1
    async with manager.scoped_client("rea", "s_other"):
        pass
    assert idle.client.closed and "s_idle" not in manager._scoped["rea"]

    # A connection opened under an older configuration is replaced.
    stale = manager._scoped["rea"]["s_other"]
    manager._configs["rea"] = {**manager._configs["rea"], "environment": {"JAVA_HOME": "/jdk21"}}
    async with manager.scoped_client("rea", "s_other") as client:
        assert client is not stale.client and client.env == {"JAVA_HOME": "/jdk21"}
    assert stale.client.closed

    await manager.close_scope("rea", "s_other")
    assert "s_other" not in manager._scoped["rea"]


@pytest.mark.asyncio
async def test_a_full_connection_pool_never_switches_the_tool_off(monkeypatch: pytest.MonkeyPatch):
    # Busy for a moment is not broken: three refusals in a row must not trigger the
    # repeated-failure switch that disables a tool for every conversation.
    from flocks.mcp.adapter import McpToolAdapter
    from flocks.mcp.errors import McpServerBusyError
    from flocks.mcp.types import McpToolDef
    from flocks.tool.registry import ToolContext, ToolRegistry

    manager = _isolated_manager(monkeypatch)
    monkeypatch.setattr("flocks.mcp.get_manager", lambda: manager)

    async def full(name, scope):
        raise McpServerBusyError("All 4 conversation connections to rea are in use; try again shortly")

    monkeypatch.setattr(manager, "_acquire_scoped", full)
    tool = McpToolAdapter.convert_tool("rea", McpToolDef(name="list_strings"), manager._clients["rea"])
    ctx = ToolContext(session_id="ses_x", message_id="m", extra={"mcp_scope": "s"})
    result = await tool.handler(ctx)
    assert result.success is False and result.metadata == {"transient": True}

    writes = []
    monkeypatch.setattr("flocks.config.config_writer.ConfigWriter.set_tool_setting", lambda *a, **k: writes.append(a))
    ToolRegistry.register(tool)
    try:
        for _ in range(4):
            outcome = await ToolRegistry.execute(tool.info.name, ctx)
            assert outcome.success is False and "disabled" not in (outcome.error or "")
        assert tool.info.enabled is True and writes == []
    finally:
        ToolRegistry.unregister(tool.info.name)


@pytest.mark.asyncio
async def test_a_connection_finishing_after_the_server_was_removed_is_closed_not_handed_out(monkeypatch: pytest.MonkeyPatch):
    from flocks.mcp.errors import McpServerNotConnectedError

    manager = _isolated_manager(monkeypatch)
    connecting, finish = asyncio.Event(), asyncio.Event()

    class SlowClient(_ConversationClient):
        async def connect(self) -> None:
            connecting.set()
            await finish.wait()

    monkeypatch.setattr("flocks.mcp.server.McpClient", SlowClient)

    async def use():
        async with manager.scoped_client("rea", "s1"):
            pass

    task = asyncio.create_task(use())
    await connecting.wait()
    await manager.remove("rea")  # meanwhile the server goes away
    finish.set()
    with pytest.raises(McpServerNotConnectedError):
        await task
    late = _ConversationClient.created[-1]
    assert isinstance(late, SlowClient) and late.closed
    assert manager._scoped.get("rea", {}) == {}


@pytest.mark.asyncio
async def test_a_reconnect_while_old_connections_close_hands_out_nothing(monkeypatch: pytest.MonkeyPatch):
    from flocks.mcp.errors import McpServerBusyError, McpServerNotConnectedError

    manager = _isolated_manager(monkeypatch)
    async with manager.scoped_client("rea", "s_old"):
        pass
    old = manager._scoped["rea"]["s_old"]
    closing, finish = asyncio.Event(), asyncio.Event()

    async def slow_disconnect():
        closing.set()
        await finish.wait()
        old.client.closed = True

    old.client.disconnect = slow_disconnect
    # The registration changes (same server), so s_old is closed on the next use...
    manager._configs["rea"] = dict(manager._configs["rea"])
    task = asyncio.create_task(manager._acquire_scoped("rea", "s_new"))
    await closing.wait()
    await manager._retire_scoped("rea")  # ...and meanwhile the server is reconnected
    finish.set()
    with pytest.raises(McpServerNotConnectedError):
        await task
    assert manager._scoped.get("rea", {}) == {}
    assert all(client.closed for client in _ConversationClient.created[2:])

    # All busy: the stale ones it popped are still closed before it refuses.
    manager = _isolated_manager(monkeypatch)
    release = asyncio.Event()
    entered = []

    async def busy(scope):
        async with manager.scoped_client("rea", scope):
            entered.append(scope)
            await release.wait()

    tasks = [asyncio.create_task(busy(f"s{i}")) for i in range(mcp_server_module.MAX_SCOPED_CLIENTS)]
    while len(entered) < mcp_server_module.MAX_SCOPED_CLIENTS:
        await asyncio.sleep(0)
    manager._scoped["rea"]["s_stale"] = mcp_server_module._ScopedClient(
        client=_ConversationClient(name="rea", server_type="local"), config={"type": "local"},
    )
    stale = manager._scoped["rea"]["s_stale"]
    with pytest.raises(McpServerBusyError):
        async with manager.scoped_client("rea", "s_extra"):
            pass
    assert stale.client.closed and "s_stale" not in manager._scoped["rea"]
    release.set()
    await asyncio.gather(*tasks)


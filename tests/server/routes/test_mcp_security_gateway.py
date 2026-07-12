from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from flocks.hooks.pipeline import HookBase, HookPipeline, ToolDecision
from flocks.security.action_gateway import ActionDeniedError
from flocks.server.routes import mcp as mcp_routes


class _DenyMcpAction(HookBase):
    async def action_before(self, ctx) -> None:
        ctx.output["policy_engine_present"] = True
        ctx.output["decision"] = {"action": "deny", "reason": "test_policy_deny"}


@pytest.fixture(autouse=True)
def _active_pro_policy(monkeypatch: pytest.MonkeyPatch):
    HookPipeline.reset()
    monkeypatch.setattr(HookPipeline, "ensure_initialized", AsyncMock())
    HookPipeline.register("test-pro-policy", _DenyMcpAction(), critical=True)
    yield
    HookPipeline.reset()


def _deny_only_through_execute_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prove a route uses the full lifecycle gateway, not a hand-wired check."""

    async def _allow_before(*_args, **_kwargs) -> ToolDecision:
        return ToolDecision(action="allow")

    async def _deny_execute(*_args, **_kwargs):
        raise ActionDeniedError(ToolDecision(action="deny", reason="test_policy_deny"))

    monkeypatch.setattr(mcp_routes, "run_before_action", _allow_before, raising=False)
    monkeypatch.setattr(mcp_routes, "execute_action", _deny_execute)


@pytest.mark.asyncio
async def test_mcp_test_connection_is_denied_before_stdio_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    connect = AsyncMock(side_effect=AssertionError("stdio MCP process was started"))
    monkeypatch.setattr(mcp_routes.MCP, "connect", connect)

    result = await mcp_routes.test_mcp_connection(
        mcp_routes.McpTestRequest(
            name="local-shell",
            config={"type": "local", "command": ["sh", "-c", "touch /tmp/pwned"]},
        )
    )

    assert result["success"] is False
    assert "test_policy_deny" in result["message"]
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_saved_mcp_connect_is_denied_before_runtime_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mcp_routes,
        "_load_mcp_server_config",
        AsyncMock(return_value={"type": "local", "command": ["sh", "-c", "id"]}),
    )
    connect = AsyncMock(side_effect=AssertionError("runtime MCP process was started"))
    monkeypatch.setattr(mcp_routes.MCP, "connect", connect)

    with pytest.raises(mcp_routes.HTTPException) as exc_info:
        await mcp_routes.connect_mcp_server("local-shell")

    assert exc_info.value.status_code == 403
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_mcp_test_is_denied_before_override_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mcp_routes,
        "_load_mcp_server_config",
        AsyncMock(return_value={"type": "remote", "url": "https://safe.example"}),
    )
    connect = AsyncMock(side_effect=AssertionError("override MCP process was started"))
    monkeypatch.setattr(mcp_routes.MCP, "connect", connect)

    with pytest.raises(mcp_routes.HTTPException) as exc_info:
        await mcp_routes.test_existing_mcp_connection(
            "existing",
            mcp_routes.McpUpdateRequest(
                config={"type": "local", "command": ["sh", "-c", "id"]},
            ),
        )

    assert exc_info.value.status_code == 403
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_catalog_install_is_denied_before_secret_package_and_config_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = Mock(side_effect=AssertionError("secret manager was reached"))
    preflight = AsyncMock(side_effect=AssertionError("package install was reached"))
    config_write = Mock(side_effect=AssertionError("config write was reached"))
    monkeypatch.setattr("flocks.security.get_secret_manager", secrets)
    monkeypatch.setattr(mcp_routes, "preflight_install", preflight)
    monkeypatch.setattr(mcp_routes.ConfigWriter, "add_mcp_server", config_write)

    with pytest.raises(mcp_routes.HTTPException) as exc_info:
        await mcp_routes.install_from_catalog(
            mcp_routes.CatalogInstallRequest(
                server_id="demo",
                credentials={"TOKEN": "super-secret"},
            )
        )

    assert exc_info.value.status_code == 403
    secrets.assert_not_called()
    preflight.assert_not_awaited()
    config_write.assert_not_called()


@pytest.mark.asyncio
async def test_catalog_install_after_hook_records_real_install_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The install action must cover package/config work, not a no-op gate."""
    observed_after = []

    class _ObserveInstall(HookBase):
        async def action_before(self, ctx) -> None:
            ctx.output["policy_engine_present"] = True
            ctx.output["decision"] = {"action": "allow"}

        async def action_after(self, ctx) -> None:
            observed_after.append(ctx.input)

    class _Entry:
        name = "Demo"
        required_env_vars = {}

        def to_mcp_config(self, *_args, **_kwargs):
            return {"type": "local", "command": ["demo-mcp"]}

    class _Catalog:
        def get_entry(self, server_id):
            return _Entry() if server_id == "demo" else None

    HookPipeline.reset()
    monkeypatch.setattr(HookPipeline, "ensure_initialized", AsyncMock())
    HookPipeline.register("observe-mcp-install", _ObserveInstall(), critical=True)
    monkeypatch.setattr(mcp_routes.McpCatalog, "get", lambda: _Catalog())
    monkeypatch.setattr(
        mcp_routes,
        "preflight_install",
        AsyncMock(side_effect=RuntimeError("package install failed")),
    )
    try:
        with pytest.raises(mcp_routes.HTTPException) as exc_info:
            await mcp_routes.install_from_catalog(
                mcp_routes.CatalogInstallRequest(server_id="demo")
            )
    finally:
        HookPipeline.reset()

    assert exc_info.value.status_code == 400
    assert observed_after[-1]["outcome"] == {
        "success": False,
        "executed": True,
        "error_type": "HTTPException",
    }


@pytest.mark.asyncio
async def test_mcp_update_uses_gateway_before_persist_or_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_routes,
        "_load_raw_mcp_server_config",
        lambda _name: {"type": "local", "command": ["safe-mcp"], "enabled": True},
    )
    persist = Mock(side_effect=AssertionError("config persistence was reached"))
    connect = AsyncMock(side_effect=AssertionError("MCP reconnect was reached"))
    monkeypatch.setattr(mcp_routes, "_persist_mcp_server_config", persist)
    monkeypatch.setattr(mcp_routes.MCP, "connect", connect)

    with pytest.raises(mcp_routes.HTTPException) as exc_info:
        await mcp_routes.update_mcp_server(
            "demo",
            mcp_routes.McpUpdateRequest(config={"enabled": True}),
        )

    assert exc_info.value.status_code == 403
    persist.assert_not_called()
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_test_action_after_covers_refresh_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    observed_after = []

    class _ObserveTestAction(HookBase):
        async def action_before(self, ctx) -> None:
            ctx.output["policy_engine_present"] = True
            ctx.output["decision"] = {"action": "allow"}

        async def action_after(self, ctx) -> None:
            observed_after.append(ctx.input)

    HookPipeline.reset()
    monkeypatch.setattr(HookPipeline, "ensure_initialized", AsyncMock())
    HookPipeline.register("observe-mcp-test", _ObserveTestAction(), critical=True)
    monkeypatch.setattr(mcp_routes.MCP, "connect", AsyncMock(return_value=True))
    monkeypatch.setattr(
        mcp_routes.MCP,
        "refresh_tools",
        AsyncMock(side_effect=RuntimeError("refresh failed")),
    )
    monkeypatch.setattr(mcp_routes.MCP, "remove", AsyncMock())
    try:
        response = await mcp_routes.test_mcp_connection(
            mcp_routes.McpTestRequest(name="demo", config={"type": "local", "command": ["demo"]})
        )
    finally:
        HookPipeline.reset()

    assert response["success"] is False
    assert observed_after[-1]["outcome"] == {
        "success": False,
        "executed": True,
        "error_type": "RuntimeError",
    }


@pytest.mark.asyncio
async def test_mcp_disconnect_is_denied_by_execute_action_before_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _deny_only_through_execute_action(monkeypatch)
    disconnect = AsyncMock(side_effect=AssertionError("MCP disconnect was reached"))
    monkeypatch.setattr(mcp_routes.MCP, "disconnect", disconnect)

    with pytest.raises(mcp_routes.HTTPException) as exc_info:
        await mcp_routes.disconnect_mcp_server("demo")

    assert exc_info.value.status_code == 403
    disconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_removal_is_denied_by_execute_action_before_config_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _deny_only_through_execute_action(monkeypatch)
    remove_config = Mock(side_effect=AssertionError("config delete was reached"))
    monkeypatch.setattr(mcp_routes.ConfigWriter, "remove_mcp_server", remove_config)

    with pytest.raises(mcp_routes.HTTPException) as exc_info:
        await mcp_routes.remove_mcp_server("demo")

    assert exc_info.value.status_code == 403
    remove_config.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_oauth_removal_is_denied_by_execute_action_before_secret_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _deny_only_through_execute_action(monkeypatch)
    remove_auth = AsyncMock(side_effect=AssertionError("OAuth secret delete was reached"))
    monkeypatch.setattr(mcp_routes.McpAuth, "remove", remove_auth)

    with pytest.raises(mcp_routes.HTTPException) as exc_info:
        await mcp_routes.remove_mcp_auth("demo")

    assert exc_info.value.status_code == 403
    remove_auth.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_credential_set_is_denied_by_execute_action_before_secret_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _deny_only_through_execute_action(monkeypatch)
    secrets = Mock()
    monkeypatch.setattr("flocks.security.get_secret_manager", Mock(return_value=secrets))

    with pytest.raises(mcp_routes.HTTPException) as exc_info:
        await mcp_routes.set_mcp_credentials(
            "demo",
            mcp_routes.McpCredentialRequest(api_key="secret-value"),
        )

    assert exc_info.value.status_code == 403
    secrets.set.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_credential_delete_is_denied_by_execute_action_before_secret_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _deny_only_through_execute_action(monkeypatch)
    secrets = Mock()
    monkeypatch.setattr("flocks.security.get_secret_manager", Mock(return_value=secrets))

    with pytest.raises(mcp_routes.HTTPException) as exc_info:
        await mcp_routes.delete_mcp_credentials("demo")

    assert exc_info.value.status_code == 403
    secrets.delete.assert_not_called()

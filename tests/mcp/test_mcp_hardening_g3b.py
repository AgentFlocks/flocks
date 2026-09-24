"""G3b MCP 加固回归测试（TOL-0805/0815/0882/0886/0796 等）。

覆盖本轮为工具测试修复的缺陷：
  - refresh 时 list_tools 超时应抛出且旧工具保持注册（不先注销）
  - 聚合 resources() 端点（此前被 /{name} 路由遮蔽、方法缺失）
  - adapter 工具名冲突时加后缀而非覆盖内置工具
  - adapter 多段 content 不再只取首段
  - 空敏感 query 值不被误抽取为 secret
  - 已连接服务器再次 connect 会先断开旧 client（不泄漏子进程/工具）
"""
from __future__ import annotations

import asyncio

import pytest

from flocks.mcp import server as mcp_server_module
from flocks.mcp.server import McpServerManager
from flocks.mcp.adapter import McpToolAdapter
from flocks.mcp.types import McpResource, McpStatus, McpToolDef
from flocks.mcp.utils import extract_api_key_from_mcp_url
from flocks.tool.registry import ToolRegistry


class _FakeClient:
    def __init__(self, tools, *, name="demo"):
        self.name = name
        self._tools = tools
        self.list_calls = 0
        self.disconnected = False

    async def connect(self):
        return None

    async def list_tools(self):
        self.list_calls += 1
        return list(self._tools)

    async def list_resources(self):
        return []

    async def disconnect(self):
        self.disconnected = True


def _tool(name):
    return McpToolDef(name=name, description=f"tool {name}", input_schema={"type": "object", "properties": {}})


@pytest.mark.asyncio
async def test_refresh_timeout_keeps_old_tools(monkeypatch):
    """TOL-0805: refresh 中 list_tools 超时 → 抛出，旧工具保持注册。"""
    manager = McpServerManager()
    client = _FakeClient([_tool("alpha"), _tool("beta")], name="g3bunit")
    monkeypatch.setattr(mcp_server_module, "McpClient", lambda **kw: client)

    await manager._connect_and_register("g3bunit", {"type": "local", "command": ["x"]})
    before = set(mcp_server_module.McpToolRegistry.get_server_tools("g3bunit"))
    assert before == {"g3bunit_alpha", "g3bunit_beta"}

    async def slow_list():
        raise asyncio.TimeoutError("list_tools timed out")

    client.list_tools = slow_list  # type: ignore[assignment]

    with pytest.raises(asyncio.TimeoutError):
        await manager.refresh_tools("g3bunit")

    after = set(mcp_server_module.McpToolRegistry.get_server_tools("g3bunit"))
    assert after == before, "list_tools 超时后旧工具必须保持注册"
    for name in after:
        ToolRegistry.unregister(name)
    mcp_server_module.McpToolRegistry.untrack_server("g3bunit")


@pytest.mark.asyncio
async def test_resources_aggregate(monkeypatch):
    """TOL-0772: 聚合 resources() 跨服务器返回，键为 uri。"""
    manager = McpServerManager()
    manager._resources_cache = {
        "s1": [McpResource(name="a", uri="demo://a", server="s1")],
        "s2": [
            McpResource(name="b", uri="demo://b", server="s2"),
            McpResource(name="a2", uri="demo://a", server="s2"),
        ],
    }
    manager._initialized = True
    result = await manager.resources()
    assert "demo://a" in result and "demo://b" in result
    # 同 uri 冲突时被命名空间化，不互相覆盖
    assert "s2:demo://a" in result
    assert result["demo://b"].server == "s2"


def test_adapter_resolves_name_conflict(monkeypatch):
    """TOL-0882: MCP 工具名与已有工具冲突时加后缀，不覆盖内置工具。"""
    ToolRegistry.init()
    existing = next(iter(ToolRegistry._tools))
    server, _, tool = existing.partition("_")
    # 构造一个必然与既有工具同名的 MCP 工具
    conflicting = McpToolAdapter.convert_tool(server or "srv", _tool(tool or existing), client=object())
    assert conflicting.info.name != existing
    assert conflicting.info.name.startswith(existing)


@pytest.mark.asyncio
async def test_adapter_joins_multiple_text_parts():
    """TOL-0886: 多段 text content 全部拼接，不再丢弃后续。"""
    class _Text:
        def __init__(self, t):
            self.text = t

    class _Result:
        isError = False
        content = [_Text("a"), _Text("b"), _Text("c")]

    class _Client:
        name = "srv"

        async def call_tool(self, name, args):
            return _Result()

    tool = McpToolAdapter.convert_tool("srv", _tool("echo"), _Client())
    res = await tool.handler(None)
    assert res.success is True and res.output == "a\nb\nc"


def test_extract_api_key_skips_blank_value(monkeypatch):
    """TOL-0742 相关: 空的敏感 query 值不被写成 secret。"""
    calls = []

    class _SM:
        def set(self, k, v):
            calls.append((k, v))

    monkeypatch.setattr("flocks.security.get_secret_manager", lambda: _SM())
    out = extract_api_key_from_mcp_url("srv", {"type": "remote", "url": "https://x/mcp?apikey="})
    assert out["url"] == "https://x/mcp?apikey="
    assert calls == []


@pytest.mark.asyncio
async def test_reconnect_disconnects_old_client(monkeypatch):
    """TOL-0796: 已连接服务器再次 connect 先断开旧 client。"""
    manager = McpServerManager()
    clients = []

    def make_client(**kw):
        c = _FakeClient([_tool("t")], name=kw.get("name", "dup"))
        clients.append(c)
        return c

    monkeypatch.setattr(mcp_server_module, "McpClient", make_client)
    assert await manager.connect("dup", {"type": "local", "command": ["x"]}) is True
    assert await manager.connect("dup", {"type": "local", "command": ["x"]}) is True
    assert len(clients) == 2
    assert clients[0].disconnected is True, "旧 client 必须先被断开"
    tools = mcp_server_module.McpToolRegistry.get_server_tools("dup")
    assert len(tools) == 1, "重连后工具不应重复"
    for name in list(tools):
        ToolRegistry.unregister(name)
    mcp_server_module.McpToolRegistry.untrack_server("dup")

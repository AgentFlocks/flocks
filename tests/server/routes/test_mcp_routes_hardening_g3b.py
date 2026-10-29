"""G3b MCP 路由加固回归测试（TOL-0746/0751/0752/0771/0772/0828 等）。

覆盖本轮修复：
  - GET /api/mcp/tools 与 /resources 聚合端点不再被 /{name} 遮蔽
  - 添加/更新缺 type 返回 422（非裸 KeyError）
  - 非法 / 空白服务器名返回 400（防路径穿越）
  - 凭证 secret_id 不得覆盖保留键 server_api_token
  - 变更类端点带 require_admin 依赖
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from flocks.server.routes import mcp as mcp_routes
from flocks.server import auth as auth_module
from flocks.tool import tool_loader


@pytest.fixture(autouse=True)
def _stub_persistence(monkeypatch):
    store: dict[str, dict] = {}
    monkeypatch.setattr(mcp_routes.ConfigWriter, "add_mcp_server", lambda n, c: store.__setitem__(n, c))
    monkeypatch.setattr(mcp_routes.ConfigWriter, "list_mcp_servers", lambda: dict(store))
    monkeypatch.setattr(mcp_routes.ConfigWriter, "get_mcp_server", lambda n: store.get(n))
    monkeypatch.setattr(tool_loader, "save_mcp_config", lambda n, c: None)
    return store


@pytest.mark.asyncio
async def test_tools_route_not_shadowed(client: AsyncClient, monkeypatch):
    """TOL-0771: GET /api/mcp/tools 返回工具名列表，不被 /{name} 匹配成 404。"""
    from flocks.mcp import McpToolRegistry

    monkeypatch.setattr(McpToolRegistry, "get_all_servers", classmethod(lambda cls: ["srv"]))
    monkeypatch.setattr(McpToolRegistry, "get_server_tools", classmethod(lambda cls, s: ["srv_a", "srv_b"]))
    resp = await client.get("/api/mcp/tools")
    assert resp.status_code == 200, resp.text
    assert resp.json() == ["srv_a", "srv_b"]


@pytest.mark.asyncio
async def test_resources_route_not_shadowed(client: AsyncClient, monkeypatch):
    """TOL-0772: GET /api/mcp/resources 返回聚合字典，不再 404/500。"""
    async def fake_resources():
        return {}

    monkeypatch.setattr(mcp_routes.MCP, "resources", fake_resources)
    resp = await client.get("/api/mcp/resources")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {}


@pytest.mark.asyncio
async def test_add_missing_type_returns_422(client: AsyncClient):
    """TOL-0746: 缺 type 返回 422（友好校验错误），而非 400 裸 KeyError。"""
    resp = await client.post("/api/mcp", json={"name": "demo", "config": {"url": "https://x"}})
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"] == "ValidationError"
    assert any("type" in str(d.get("loc", "")) or "type" in d.get("msg", "") for d in body["details"])


@pytest.mark.asyncio
async def test_add_rejects_path_traversal_name(client: AsyncClient, _stub_persistence):
    """TOL-0751: 路径穿越名被 400 拒绝且不持久化。"""
    resp = await client.post(
        "/api/mcp",
        json={"name": "../../evil", "config": {"type": "sse", "url": "https://x/mcp?apikey="}},
    )
    assert resp.status_code == 400, resp.text
    assert "Invalid MCP server name" in resp.json()["message"]
    assert "../../evil" not in _stub_persistence


@pytest.mark.asyncio
async def test_add_rejects_blank_name(client: AsyncClient):
    """TOL-0752: 空 / 纯空白名返回 422（pydantic 校验）。"""
    resp = await client.post(
        "/api/mcp",
        json={"name": "   ", "config": {"type": "sse", "url": "https://x?apikey="}},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_credentials_reject_reserved_secret_id(client: AsyncClient, monkeypatch):
    """TOL-0828: secret_id 不得覆盖保留键 server_api_token。"""
    saved = {}

    class _SM:
        def set(self, k, v):
            saved[k] = v

    monkeypatch.setattr("flocks.security.get_secret_manager", lambda: _SM())
    resp = await client.post(
        "/api/mcp/x/credentials",
        json={"secret_id": auth_module.API_TOKEN_SECRET_ID, "api_key": "attacker"},
    )
    assert resp.status_code == 400, resp.text
    assert "reserved" in resp.json()["message"]
    assert auth_module.API_TOKEN_SECRET_ID not in saved


def test_mutation_routes_require_admin():
    """TOL-0754/0788/0872 等: 变更类端点声明 require_admin 依赖。"""
    guarded = {
        "mcp.add", "mcp.remove", "mcp.update", "mcp.connect", "mcp.disconnect",
        "mcp.refresh", "mcp.test", "mcp.catalog.auto_setup", "mcp.catalog.install",
    }
    seen = set()
    for route in mcp_routes.router.routes:
        op = getattr(route, "operation_id", None)
        if op not in guarded:
            continue
        seen.add(op)
        dep_calls = [d.call for d in route.dependant.dependencies]
        assert auth_module.require_admin in dep_calls, f"{op} missing require_admin"
    assert guarded <= seen, f"missing routes: {guarded - seen}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    ["http://169.254.169.254/latest/meta-data/", "ftp://example.com/mcp", "file:///etc/passwd"],
)
async def test_add_rejects_metadata_and_non_http_urls(client: AsyncClient, _stub_persistence, url: str):
    """X-G8 转办: 远程 MCP url 拒绝 link-local/元数据地址与非 http(s) scheme（不发起连接、不持久化）。"""
    connects: list[str] = []

    async def fake_connect(name, config):
        connects.append(name)
        return True

    import flocks.server.routes.mcp as mcp_routes_mod

    original = mcp_routes_mod.MCP.connect
    mcp_routes_mod.MCP.connect = fake_connect  # type: ignore[assignment]
    try:
        resp = await client.post("/api/mcp", json={"name": "ssrf-mcp", "config": {"type": "remote", "url": url}})
    finally:
        mcp_routes_mod.MCP.connect = original  # type: ignore[assignment]
    assert resp.status_code == 400, resp.text
    assert connects == []
    assert "ssrf-mcp" not in _stub_persistence
    test = await client.post("/api/mcp/test", json={"name": "ssrf-mcp", "config": {"type": "remote", "url": url}})
    assert test.status_code == 200 and test.json()["success"] is False


@pytest.mark.asyncio
async def test_add_keeps_loopback_allowed(client: AsyncClient, _stub_persistence, monkeypatch):
    """本地 MCP 常驻 127.0.0.1：回环地址不被 URL 守卫拒绝。"""
    async def fake_connect(name, config):
        return True

    async def fake_status():
        return {}

    monkeypatch.setattr(mcp_routes.MCP, "connect", fake_connect)
    monkeypatch.setattr(mcp_routes.MCP, "status", fake_status)
    resp = await client.post("/api/mcp", json={"name": "local-mcp", "config": {"type": "remote", "url": "http://127.0.0.1:9/mcp"}})
    assert resp.status_code == 200, resp.text

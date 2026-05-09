import pytest

from flocks.mcp.client import McpClient


class TestMcpClientTransportSelection:
    @pytest.mark.asyncio
    async def test_connect_uses_sse_only_when_transport_is_sse(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[str] = []

        async def fake_http(*args, **kwargs):
            calls.append("http")

        async def fake_sse(*args, **kwargs):
            calls.append("sse")

        client = McpClient(
            name="demo",
            server_type="remote",
            url="https://example.com/sse",
            transport="sse",
        )
        monkeypatch.setattr(client, "_do_connect_streamable_http", fake_http)
        monkeypatch.setattr(client, "_do_connect_sse", fake_sse)

        await client.connect()

        assert calls == ["sse"]
        assert client._transport_type == "sse"

    @pytest.mark.asyncio
    async def test_connect_uses_http_only_when_transport_is_http(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[str] = []

        async def fake_http(*args, **kwargs):
            calls.append("http")

        async def fake_sse(*args, **kwargs):
            calls.append("sse")

        client = McpClient(
            name="demo",
            server_type="remote",
            url="https://example.com/mcp",
            transport="http",
        )
        monkeypatch.setattr(client, "_do_connect_streamable_http", fake_http)
        monkeypatch.setattr(client, "_do_connect_sse", fake_sse)

        await client.connect()

        assert calls == ["http"]
        assert client._transport_type == "streamable_http"

    @pytest.mark.asyncio
    async def test_connect_auto_falls_back_to_sse_after_http_failure(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[str] = []

        async def fake_http(*args, **kwargs):
            calls.append("http")
            raise RuntimeError("HTTP 405")

        async def fake_sse(*args, **kwargs):
            calls.append("sse")

        async def fake_cleanup():
            return None

        client = McpClient(
            name="demo",
            server_type="remote",
            url="https://example.com/mcp",
            transport="auto",
        )
        monkeypatch.setattr(client, "_do_connect_streamable_http", fake_http)
        monkeypatch.setattr(client, "_do_connect_sse", fake_sse)
        monkeypatch.setattr(client, "_cleanup_connection", fake_cleanup)

        await client.connect()

        assert calls == ["http", "sse"]
        assert client._transport_type == "sse"

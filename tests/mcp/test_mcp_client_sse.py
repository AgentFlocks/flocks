"""Tests for MCP client remote transport lifecycle and fallback behavior."""

import asyncio
from contextlib import asynccontextmanager
from types import MethodType, SimpleNamespace

import pytest

import flocks.mcp.client as mcp_client_module
from flocks.mcp.client import McpClient, _extract_root_cause


def _make_session_class(
    *,
    events: dict[str, object] | None = None,
    tool_result: object | None = None,
    tools: list[object] | None = None,
    resources: list[object] | None = None,
):
    class FakeSession:
        def __init__(self, read_stream, write_stream):
            self.read_stream = read_stream
            self.write_stream = write_stream

        async def __aenter__(self):
            if events is not None:
                events["session_enter_task"] = asyncio.current_task()
            return self

        async def __aexit__(self, exc_type, exc, tb):
            if events is not None:
                events["session_exit_task"] = asyncio.current_task()
            return False

        async def initialize(self):
            return SimpleNamespace(protocolVersion="2026-05-12", serverInfo={"name": "demo"})

        async def list_tools(self):
            return SimpleNamespace(tools=tools or [])

        async def call_tool(self, name, arguments):
            if events is not None:
                events["call_tool_task"] = asyncio.current_task()
            if isinstance(tool_result, Exception):
                raise tool_result
            if tool_result is not None:
                return tool_result
            return {"name": name, "arguments": arguments}

        async def list_resources(self):
            return SimpleNamespace(resources=resources or [])

        async def read_resource(self, uri):
            return {"uri": uri}

    return FakeSession


def _make_remote_transport_factory(
    label: str,
    *,
    streams: tuple[object, ...] | None = None,
    error: Exception | None = None,
    events: dict[str, object] | None = None,
    captures: list[tuple[str, str, dict | None]] | None = None,
):
    if streams is None:
        if label == "http":
            streams = ("read", "write", lambda: None)
        else:
            streams = ("read", "write")

    @asynccontextmanager
    async def factory(self, url, headers):
        if captures is not None:
            captures.append((label, url, headers))
        if error is not None:
            raise error
        if events is not None:
            events[f"{label}_enter_task"] = asyncio.current_task()
        try:
            yield streams
        finally:
            if events is not None:
                events[f"{label}_exit_task"] = asyncio.current_task()

    return factory


def _bind_method(monkeypatch: pytest.MonkeyPatch, client: McpClient, name: str, method) -> None:
    monkeypatch.setattr(client, name, MethodType(method, client))


class TestMcpClientRemoteFallback:
    @pytest.mark.asyncio
    async def test_remote_falls_back_to_sse(self, monkeypatch: pytest.MonkeyPatch):
        client = McpClient(
            name="test-remote",
            server_type="remote",
            url="https://mcp.example.com/mcp",
            timeout=10.0,
        )
        monkeypatch.setattr(mcp_client_module, "ClientSession", _make_session_class())

        _bind_method(
            monkeypatch,
            client,
            "_create_streamable_http_streams",
            _make_remote_transport_factory("http", error=RuntimeError("HTTP failed")),
        )
        _bind_method(
            monkeypatch,
            client,
            "_create_sse_streams",
            _make_remote_transport_factory("sse"),
        )

        await client.connect()

        assert client._transport_type == "sse"
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_remote_streamable_http_success_no_sse(self, monkeypatch: pytest.MonkeyPatch):
        client = McpClient(
            name="test-remote",
            server_type="remote",
            url="https://mcp.example.com/mcp",
            timeout=10.0,
        )
        captures: list[tuple[str, str, dict | None]] = []
        monkeypatch.setattr(mcp_client_module, "ClientSession", _make_session_class())

        _bind_method(
            monkeypatch,
            client,
            "_create_streamable_http_streams",
            _make_remote_transport_factory("http", captures=captures),
        )
        _bind_method(
            monkeypatch,
            client,
            "_create_sse_streams",
            _make_remote_transport_factory("sse", captures=captures),
        )

        await client.connect()

        assert client._transport_type == "streamable_http"
        assert [label for label, _, _ in captures] == ["http"]
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_remote_both_fail_raises(self, monkeypatch: pytest.MonkeyPatch):
        client = McpClient(
            name="test-remote",
            server_type="remote",
            url="https://mcp.example.com/mcp",
            timeout=10.0,
        )
        monkeypatch.setattr(mcp_client_module, "ClientSession", _make_session_class())

        _bind_method(
            monkeypatch,
            client,
            "_create_streamable_http_streams",
            _make_remote_transport_factory("http", error=RuntimeError("HTTP failed")),
        )
        _bind_method(
            monkeypatch,
            client,
            "_create_sse_streams",
            _make_remote_transport_factory("sse", error=RuntimeError("SSE failed")),
        )

        with pytest.raises(RuntimeError, match="Connection failed.*SSE failed"):
            await client.connect()

    @pytest.mark.asyncio
    async def test_sse_type_also_tries_streamable_http_first(self, monkeypatch: pytest.MonkeyPatch):
        client = McpClient(
            name="test-sse",
            server_type="sse",
            url="https://mcp.example.com/mcp",
            timeout=10.0,
        )
        captures: list[tuple[str, str, dict | None]] = []
        monkeypatch.setattr(mcp_client_module, "ClientSession", _make_session_class())

        _bind_method(
            monkeypatch,
            client,
            "_create_streamable_http_streams",
            _make_remote_transport_factory("http", captures=captures),
        )
        _bind_method(
            monkeypatch,
            client,
            "_create_sse_streams",
            _make_remote_transport_factory("sse", captures=captures),
        )

        await client.connect()

        assert client._transport_type == "streamable_http"
        assert [label for label, _, _ in captures] == ["http"]
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_timeout_does_not_fall_back(self, monkeypatch: pytest.MonkeyPatch):
        client = McpClient(
            name="test-timeout",
            server_type="remote",
            url="https://mcp.example.com/mcp",
            timeout=10.0,
        )
        captures: list[tuple[str, str, dict | None]] = []
        monkeypatch.setattr(mcp_client_module, "ClientSession", _make_session_class())

        _bind_method(
            monkeypatch,
            client,
            "_create_streamable_http_streams",
            _make_remote_transport_factory("http", error=asyncio.TimeoutError()),
        )
        _bind_method(
            monkeypatch,
            client,
            "_create_sse_streams",
            _make_remote_transport_factory("sse", captures=captures),
        )

        with pytest.raises(RuntimeError, match="Connection timeout"):
            await client.connect()

        assert captures == []
        assert client._transport_type is None

    @pytest.mark.asyncio
    async def test_remote_passes_resolved_headers_to_transports(self, monkeypatch: pytest.MonkeyPatch):
        client = McpClient(
            name="test-headers",
            server_type="remote",
            url="https://mcp.example.com/mcp",
            headers={"Api-Key": "token123"},
            auth_config={
                "type": "apikey",
                "location": "header",
                "param_name": "Authorization",
                "value": "Bearer abc",
            },
            timeout=10.0,
        )
        captures: list[tuple[str, str, dict | None]] = []
        monkeypatch.setattr(mcp_client_module, "ClientSession", _make_session_class())

        _bind_method(
            monkeypatch,
            client,
            "_create_streamable_http_streams",
            _make_remote_transport_factory("http", captures=captures, error=RuntimeError("HTTP failed")),
        )
        _bind_method(
            monkeypatch,
            client,
            "_create_sse_streams",
            _make_remote_transport_factory("sse", captures=captures),
        )

        await client.connect()

        expected_headers = {
            "Api-Key": "token123",
            "Authorization": "Bearer abc",
        }
        assert captures == [
            ("http", "https://mcp.example.com/mcp", expected_headers),
            ("sse", "https://mcp.example.com/mcp", expected_headers),
        ]
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_disconnect_closes_streams_and_session_in_owner_task(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        client = McpClient(
            name="test-owner",
            server_type="remote",
            url="https://mcp.example.com/mcp",
        )
        events: dict[str, object] = {}
        monkeypatch.setattr(mcp_client_module, "ClientSession", _make_session_class(events=events))

        _bind_method(
            monkeypatch,
            client,
            "_create_streamable_http_streams",
            _make_remote_transport_factory("http", events=events),
        )
        _bind_method(
            monkeypatch,
            client,
            "_create_sse_streams",
            _make_remote_transport_factory("sse", events=events),
        )

        await client.connect()
        await client.disconnect()

        assert events["http_enter_task"] is events["http_exit_task"]
        assert events["session_enter_task"] is events["session_exit_task"]

    @pytest.mark.asyncio
    async def test_call_tool_runs_through_owner_task(self, monkeypatch: pytest.MonkeyPatch):
        client = McpClient(
            name="test-call",
            server_type="remote",
            url="https://mcp.example.com/mcp",
        )
        events: dict[str, object] = {}
        monkeypatch.setattr(
            mcp_client_module,
            "ClientSession",
            _make_session_class(events=events),
        )

        _bind_method(
            monkeypatch,
            client,
            "_create_streamable_http_streams",
            _make_remote_transport_factory("http"),
        )
        _bind_method(
            monkeypatch,
            client,
            "_create_sse_streams",
            _make_remote_transport_factory("sse"),
        )

        await client.connect()
        result = await client.call_tool("demo_tool", {"value": 1})
        await client.disconnect()

        assert result == {"name": "demo_tool", "arguments": {"value": 1}}
        assert events["call_tool_task"] is events["session_enter_task"]


class TestExtractRootCause:
    def test_simple_exception(self):
        assert _extract_root_cause(RuntimeError("simple error")) == "simple error"

    def test_exception_group(self):
        inner = RuntimeError("real error")
        group = ExceptionGroup("group", [inner])
        assert _extract_root_cause(group) == "real error"

    def test_nested_exception_group(self):
        inner = ValueError("deep error")
        group1 = ExceptionGroup("inner group", [inner])
        group2 = ExceptionGroup("outer group", [group1])
        assert _extract_root_cause(group2) == "deep error"

    def test_http_status_error(self):
        class MockResponse:
            status_code = 401

        class MockRequest:
            url = "https://example.com/mcp?apikey=secret123"

        exc = Exception("HTTP error")
        exc.response = MockResponse()
        exc.request = MockRequest()
        result = _extract_root_cause(exc)
        assert "401" in result
        assert "secret" not in result

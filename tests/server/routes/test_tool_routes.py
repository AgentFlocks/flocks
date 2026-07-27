from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from flocks.auth.context import AuthUser
from flocks.session.message import Message, MessageRole
from flocks.session.session import Session
from flocks.tool.registry import (
    ParameterType,
    Tool,
    ToolCategory,
    ToolInfo,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)


@contextmanager
def _temporary_tool(tool: Tool) -> Iterator[None]:
    ToolRegistry.init()
    existing = ToolRegistry._tools.get(tool.info.name)
    existing_default = ToolRegistry._enabled_defaults.get(tool.info.name)
    ToolRegistry.register(tool)
    _clear_tool_summary_cache()
    try:
        yield
    finally:
        ToolRegistry._failure_state.pop(tool.info.name, None)
        if existing is not None:
            ToolRegistry._tools[tool.info.name] = existing
        else:
            ToolRegistry._tools.pop(tool.info.name, None)
        if existing_default is not None:
            ToolRegistry._enabled_defaults[tool.info.name] = existing_default
        else:
            ToolRegistry._enabled_defaults.pop(tool.info.name, None)
        _clear_tool_summary_cache()


def _clear_tool_summary_cache() -> None:
    from flocks.server.routes import tool as tool_routes

    tool_routes._invalidate_tool_summary_cache()


class _FakeSessionUser:
    def __init__(self, role: str) -> None:
        self.role = role

    def to_auth_user(self) -> AuthUser:
        return AuthUser(
            id=f"usr_{self.role}",
            username=f"{self.role}-user",
            role=self.role,
            status="active",
            must_reset_password=False,
        )


def _patch_session_user(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    from flocks.server import auth as auth_module

    async def _has_users():
        return True

    async def _get_user_by_session_id(_session_id: str):
        return _FakeSessionUser(role)

    monkeypatch.setattr(auth_module.AuthService, "has_users", _has_users)
    monkeypatch.setattr(auth_module.AuthService, "get_user_by_session_id", _get_user_by_session_id)


async def _create_session_and_message(title: str) -> tuple[str, str]:
    session = await Session.create(
        project_id="default",
        directory=str(Path.cwd()),
        title=title,
        agent="rex",
    )
    message = await Message.create(
        session_id=session.id,
        role=MessageRole.USER,
        content=f"{title} message",
        agent="rex",
    )
    return session.id, message.id


class TestToolRouteSecurity:
    @pytest.mark.asyncio
    async def test_viewer_cannot_create_plugin_tool(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
        _patch_session_user(monkeypatch, "viewer")

        response = await client.post(
            "/api/tools",
            headers={"cookie": "flocks_session=viewer-session"},
            json={
                "name": "viewer_created_tool",
                "description": "should be rejected",
                "handler": {
                    "type": "http",
                    "method": "GET",
                    "url": "https://example.com",
                },
            },
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_viewer_cannot_update_plugin_tool(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
        _patch_session_user(monkeypatch, "viewer")

        response = await client.put(
            "/api/tools/existing_tool",
            headers={"cookie": "flocks_session=viewer-session"},
            json={"description": "nope"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_viewer_cannot_delete_plugin_tool(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
        _patch_session_user(monkeypatch, "viewer")

        response = await client.delete(
            "/api/tools/existing_tool",
            headers={"cookie": "flocks_session=viewer-session"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_execute_blocks_direct_bash_access(self, client: AsyncClient):
        response = await client.post(
            "/api/tools/bash/execute",
            json={"params": {"command": "pwd"}},
        )

        assert response.status_code == 403
        assert "session-backed request" in response.json()["message"]

    @pytest.mark.asyncio
    async def test_test_endpoint_blocks_direct_bash_access(self, client: AsyncClient):
        response = await client.post(
            "/api/tools/bash/test",
            json={"params": {"command": "pwd"}},
        )

        assert response.status_code == 403
        assert "session-backed request" in response.json()["message"]

    @pytest.mark.asyncio
    async def test_batch_blocks_direct_bash_access(self, client: AsyncClient):
        response = await client.post(
            "/api/tools/batch",
            json={
                "calls": [
                    {
                        "name": "bash",
                        "params": {"command": "pwd"},
                    }
                ]
            },
        )

        assert response.status_code == 403
        assert "session-backed request" in response.json()["message"]

    @pytest.mark.asyncio
    async def test_execute_rejects_missing_message_id_for_local_tools(self, client: AsyncClient):
        session_id, _ = await _create_session_and_message("missing-message-id")

        response = await client.post(
            "/api/tools/bash/execute",
            json={
                "params": {"command": "pwd"},
                "sessionID": session_id,
            },
        )

        assert response.status_code == 403
        assert "verified" in response.json()["message"]

    @pytest.mark.asyncio
    async def test_execute_rejects_unknown_session_for_local_tools(self, client: AsyncClient):
        response = await client.post(
            "/api/tools/bash/execute",
            json={
                "params": {"command": "pwd"},
                "sessionID": "sess-missing",
                "messageID": "msg-missing",
            },
        )

        assert response.status_code == 404
        assert "Session not found" in response.json()["message"]

    @pytest.mark.asyncio
    async def test_execute_allows_direct_api_tools(self, client: AsyncClient):
        async def handler(ctx, text: str) -> ToolResult:
            return ToolResult(
                success=True,
                output=f"{text}:{ctx.session_id}",
            )

        tool = Tool(
            info=ToolInfo(
                name="http_safe_api_tool",
                description="safe http api tool",
                category=ToolCategory.CUSTOM,
                source="api",
            ),
            handler=handler,
        )

        with _temporary_tool(tool):
            response = await client.post(
                "/api/tools/http_safe_api_tool/execute",
                json={"params": {"text": "pong"}},
            )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["success"] is True
        assert payload["output"] == "pong:http-tool"

    @pytest.mark.asyncio
    async def test_execute_allows_direct_custom_tools(self, client: AsyncClient):
        async def handler(ctx, text: str) -> ToolResult:
            return ToolResult(
                success=True,
                output=f"{text}:{ctx.session_id}",
            )

        tool = Tool(
            info=ToolInfo(
                name="http_safe_custom_tool",
                description="safe http custom tool",
                category=ToolCategory.CUSTOM,
                source="custom",
            ),
            handler=handler,
        )

        with _temporary_tool(tool):
            response = await client.post(
                "/api/tools/http_safe_custom_tool/execute",
                json={"params": {"text": "hello"}},
            )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["success"] is True
        assert payload["output"] == "hello:http-tool"

    @pytest.mark.asyncio
    async def test_execute_rejects_message_outside_session(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.server.routes import tool as tool_routes

        permission_ask = AsyncMock(return_value=None)
        monkeypatch.setattr(tool_routes.PermissionNext, "ask", permission_ask)

        session_id, _ = await _create_session_and_message("owner-session")
        _, foreign_message_id = await _create_session_and_message("foreign-session")

        async def handler(ctx) -> ToolResult:
            await ctx.ask(
                permission="bash",
                patterns=["pwd"],
                always=["*"],
                metadata={"source": "test"},
            )
            return ToolResult(success=True, output="ok")

        tool = Tool(
            info=ToolInfo(
                name="http_session_message_mismatch_tool",
                description="session-message mismatch tool",
                category=ToolCategory.SYSTEM,
            ),
            handler=handler,
        )

        with _temporary_tool(tool):
            response = await client.post(
                "/api/tools/http_session_message_mismatch_tool/execute",
                json={
                    "params": {},
                    "sessionID": session_id,
                    "messageID": foreign_message_id,
                    "agent": "rex",
                },
            )

        assert response.status_code == 404
        assert "not found in session" in response.json()["message"]
        permission_ask.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_uses_permission_flow_when_session_context_is_present(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.server.routes import tool as tool_routes

        permission_ask = AsyncMock(return_value=None)
        monkeypatch.setattr(tool_routes.PermissionNext, "ask", permission_ask)
        session_id, message_id = await _create_session_and_message("valid-session-context")

        async def handler(ctx) -> ToolResult:
            await ctx.ask(
                permission="bash",
                patterns=["pwd"],
                always=["*"],
                metadata={"source": "test"},
            )
            return ToolResult(success=True, output="ok")

        tool = Tool(
            info=ToolInfo(
                name="http_session_bound_tool",
                description="session-bound test tool",
                category=ToolCategory.SYSTEM,
            ),
            handler=handler,
        )

        with _temporary_tool(tool):
            response = await client.post(
                "/api/tools/http_session_bound_tool/execute",
                json={
                    "params": {},
                    "sessionID": session_id,
                    "messageID": message_id,
                    "agent": "rex",
                },
            )

        assert response.status_code == 200, response.text
        assert response.json()["success"] is True
        permission_ask.assert_awaited_once()
        kwargs = permission_ask.await_args.kwargs
        assert kwargs["session_id"] == session_id
        assert kwargs["permission"] == "bash"
        assert kwargs["metadata"]["messageID"] == message_id
        assert kwargs["tool"] == {"name": "http_session_bound_tool"}

    @pytest.mark.asyncio
    async def test_batch_uses_actual_child_tool_name_for_permission_flow(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.server.routes import tool as tool_routes

        permission_ask = AsyncMock(return_value=None)
        monkeypatch.setattr(tool_routes.PermissionNext, "ask", permission_ask)
        session_id, message_id = await _create_session_and_message("valid-batch-session-context")

        async def handler(ctx) -> ToolResult:
            await ctx.ask(
                permission="bash",
                patterns=["pwd"],
                always=["*"],
                metadata={"source": "batch-test"},
            )
            return ToolResult(success=True, output="ok")

        tool = Tool(
            info=ToolInfo(
                name="http_batch_named_tool",
                description="batch named test tool",
                category=ToolCategory.SYSTEM,
            ),
            handler=handler,
        )

        with _temporary_tool(tool):
            response = await client.post(
                "/api/tools/batch",
                json={
                    "calls": [{"name": "http_batch_named_tool", "params": {}}],
                    "parallel": True,
                    "sessionID": session_id,
                    "messageID": message_id,
                    "agent": "rex",
                },
            )

        assert response.status_code == 200, response.text
        assert response.json()["results"][0]["success"] is True
        permission_ask.assert_awaited_once()
        kwargs = permission_ask.await_args.kwargs
        assert kwargs["session_id"] == session_id
        assert kwargs["metadata"]["messageID"] == message_id
        assert kwargs["tool"] == {"name": "http_batch_named_tool"}


class TestToolListPageRoute:
    @pytest.mark.asyncio
    async def test_auto_disable_syncs_cached_page_across_worker_state(self, client: AsyncClient):
        from flocks.config.config_writer import ConfigWriter

        async def handler(_ctx, probe_id: str) -> ToolResult:
            return ToolResult(success=False, error=f"repeated failure for {probe_id}")

        tool = Tool(
            info=ToolInfo(
                name="page_auto_disable_cache_tool",
                description="NeedleAutoDisableCache",
                category=ToolCategory.CUSTOM,
                source="plugin_py",
            ),
            handler=handler,
        )

        with _temporary_tool(tool):
            initial = await client.get(
                "/api/tools/page",
                params={"q": "needleautodisablecache", "limit": 25},
            )
            for _ in range(ToolRegistry._failure_disable_threshold):
                result = await client.post(
                    f"/api/tools/{tool.info.name}/test",
                    json={"params": {"probe_id": "same-input"}},
                )
            setting = ConfigWriter.get_tool_setting(tool.info.name)
            # Simulate a second worker whose in-memory registry and list cache
            # still contain the pre-disable state.  The persisted config token
            # must make the next page request repair both immediately.
            tool.info.enabled = True
            refreshed = await client.get(
                "/api/tools/page",
                params={"q": "needleautodisablecache", "limit": 25},
            )
            disabled_after_refresh = tool.info.enabled

            # The reverse transition must also propagate: another worker can
            # manually restore the default by deleting the disable overlay.
            removed = ConfigWriter.delete_tool_setting(tool.info.name)
            tool.info.enabled = False
            reenabled = await client.get(
                "/api/tools/page",
                params={"q": "needleautodisablecache", "limit": 25},
            )

        assert initial.status_code == 200, initial.text
        assert initial.json()["items"][0]["enabled"] is True
        assert result.status_code == 200, result.text
        assert result.json()["metadata"]["disabled"] is True
        assert setting == {"enabled": False}
        assert disabled_after_refresh is False
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["items"][0]["enabled"] is False
        assert removed is True
        assert tool.info.enabled is True
        assert tool.info.name not in ToolRegistry._failure_state
        assert reenabled.status_code == 200, reenabled.text
        assert reenabled.json()["items"][0]["enabled"] is True

    @pytest.mark.asyncio
    async def test_list_page_searches_server_side_and_omits_parameter_payload(self, client: AsyncClient):
        async def handler(ctx, text: str) -> ToolResult:
            return ToolResult(success=True, output=f"{text}:{ctx.session_id}")

        tool = Tool(
            info=ToolInfo(
                name="page_unique_alpha_tool",
                description="Needle Alpha paginated search tool",
                category=ToolCategory.CUSTOM,
                source="plugin_py",
                parameters=[
                    ToolParameter(
                        name="text",
                        type=ParameterType.STRING,
                        description="Text to echo",
                    )
                ],
            ),
            handler=handler,
        )
        api_tool = Tool(
            info=ToolInfo(
                name="page_unique_alpha_api_tool",
                description="Needle Alpha paginated API tool",
                category=ToolCategory.CUSTOM,
                source="api",
                provider="page-api-provider",
            ),
            handler=handler,
        )

        with _temporary_tool(tool), _temporary_tool(api_tool):
            response = await client.get(
                "/api/tools/page",
                params={"q": "needle alpha", "source": "plugin_py", "offset": 0, "limit": 20},
            )
            detail = await client.get("/api/tools/page_unique_alpha_tool")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 1
        assert payload["offset"] == 0
        assert payload["limit"] == 20
        assert payload["facets"]["source"]["plugin_py"] == 1
        assert payload["facets"]["source"]["api"] == 1
        assert payload["facets"]["source_groups"]["api"] == 1
        assert payload["items"][0]["name"] == "page_unique_alpha_tool"
        assert payload["items"][0]["parameters"] == []
        assert payload["items"][0]["parameters_count"] == 1

        assert detail.status_code == 200, detail.text
        assert len(detail.json()["parameters"]) == 1

    @pytest.mark.asyncio
    async def test_list_page_reuses_lightweight_summary_cache(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.server.routes import tool as tool_routes

        async def handler(ctx) -> ToolResult:
            return ToolResult(success=True, output=ctx.session_id)

        tool = Tool(
            info=ToolInfo(
                name="page_summary_cache_unique_tool",
                description="NeedlePageSummaryCacheUnique",
                category=ToolCategory.CUSTOM,
                source="plugin_py",
            ),
            handler=handler,
        )

        original_build_tool_index_item = tool_routes._build_tool_index_item
        index_builds = 0

        def counted_build_tool_index_item(tool_info):
            nonlocal index_builds
            index_builds += 1
            return original_build_tool_index_item(tool_info)

        monkeypatch.setattr(tool_routes, "_build_tool_index_item", counted_build_tool_index_item)

        with _temporary_tool(tool):
            first = await client.get(
                "/api/tools/page",
                params={"q": "needlepagesummarycacheunique", "limit": 25},
            )
            first_builds = index_builds
            second = await client.get(
                "/api/tools/page",
                params={"q": "needlepagesummarycacheunique", "source": "plugin_py", "limit": 25},
            )
            second_builds = index_builds

            tool_routes._invalidate_tool_summary_cache()
            third = await client.get(
                "/api/tools/page",
                params={"q": "needlepagesummarycacheunique", "limit": 25},
            )

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert third.status_code == 200, third.text
        assert first.json()["total"] == 1
        assert second.json()["total"] == 1
        assert third.json()["total"] == 1
        assert first_builds > 0
        assert second_builds == first_builds
        assert index_builds > second_builds

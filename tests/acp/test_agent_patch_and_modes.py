"""Regression tests: ACP edit-permission patching and session/new mode id."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.acp.agent import ACPAgent
from flocks.acp.session import ACPSessionState
from flocks.acp.types import ACPConfig


def _agent(sdk=None, conn=None):
    sdk = sdk or SimpleNamespace()
    conn = conn or SimpleNamespace(session_update=AsyncMock(), request_permission=AsyncMock(), write_text_file=AsyncMock())
    return ACPAgent(conn, ACPConfig(sdk=sdk))


def test_apply_patch_applies_unified_diff():
    agent = _agent()
    assert agent._apply_patch("a\nb\nc\n", "--- a\n+++ b\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n") == "a\nB\nc\n"
    assert agent._apply_patch("old", "@@ -1 +1 @@\n-old\n+new\n") == "new"
    # mismatching context must not produce a half-applied file
    assert agent._apply_patch("a\nb\nc\n", "@@ -1,3 +1,3 @@\n a\n-x\n+B\n c\n") is None
    assert agent._apply_patch("a\n", "not a diff") is None


@pytest.mark.asyncio
async def test_edit_permission_allow_writes_patched_file(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("old\n")
    sdk = SimpleNamespace(permission=SimpleNamespace(reply=AsyncMock()))
    conn = SimpleNamespace(
        session_update=AsyncMock(),
        write_text_file=AsyncMock(),
        request_permission=AsyncMock(return_value={"outcome": {"outcome": "selected", "optionId": "allow"}}),
    )
    agent = _agent(sdk, conn)
    agent._session_manager._sessions["s1"] = ACPSessionState(id="s1", cwd=str(tmp_path), mcp_servers=[], created_at=datetime.now())

    await agent._handle_permission_asked({
        "sessionID": "s1", "id": "perm1", "permission": "edit", "tool": {},
        "metadata": {"filepath": str(target), "diff": "@@ -1 +1 @@\n-old\n+new\n"},
    })

    conn.write_text_file.assert_awaited_once_with(session_id="s1", path=str(target), content="new\n")
    assert sdk.permission.reply.await_args.kwargs["reply"] == "allow"


@pytest.mark.asyncio
async def test_new_session_current_mode_id_is_a_string():
    sdk = SimpleNamespace(
        session=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(id="ses_new"))),
        config=SimpleNamespace(providers=AsyncMock(return_value=[{"id": "openai", "name": "OpenAI", "models": {"gpt": {"name": "GPT"}}}])),
        app=SimpleNamespace(agents=AsyncMock(return_value=[{"name": "rex", "description": "d"}])),
        command=SimpleNamespace(list=AsyncMock(return_value=[])),
        mcp=SimpleNamespace(add=AsyncMock()),
    )
    agent = _agent(sdk)
    agent._get_default_model = AsyncMock(return_value={"providerID": "openai", "modelID": "gpt"})
    with patch("flocks.agent.registry.Agent.default_agent", new=AsyncMock(return_value="rex")):
        result = await agent.new_session({"cwd": "/tmp/p", "mcpServers": []})
    # Previously the un-awaited coroutine object ended up here.
    assert result["modes"]["currentModeId"] == "rex"
    assert agent._session_manager.get("ses_new").mode_id == "rex"

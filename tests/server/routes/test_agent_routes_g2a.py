"""Agent route regressions added by the 2026-09-14 full-case execution (G2a).

Case ids (SES-xxxx) are named per test so the execution record can be traced.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import status
from httpx import AsyncClient

from flocks.auth.context import AuthUser
from flocks.server.routes import agent as agent_routes


@pytest.fixture(autouse=True)
def _isolated_delegatable_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings_file = tmp_path / "agent_delegatable_settings.json"
    monkeypatch.setattr("flocks.agent.delegatable_settings.settings_path", lambda: settings_file)
    from flocks.agent.registry import Agent

    Agent._delegatable_settings_mtime = 0.0
    Agent.invalidate_cache()
    yield settings_file
    Agent._delegatable_settings_mtime = 0.0
    Agent.invalidate_cache()


def _as_member(monkeypatch: pytest.MonkeyPatch) -> None:
    from flocks.server import auth as auth_module

    member = AuthUser(id="usr_member", username="member", role="member", status="active")
    monkeypatch.setattr(auth_module, "require_user", lambda _request: member)


async def _create(client: AsyncClient, name: str, **extra) -> dict:
    resp = await client.post("/api/agent", json={"name": name, "prompt": "You review code", "mode": "subagent", **extra})
    assert resp.status_code == status.HTTP_200_OK, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# validation  (SES-0586 / 0587 / 0588 / 0589 / 0590 / 0605 / 0610)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"name": "", "prompt": "x"},  # SES-0586
        {"name": "my agent/1 测试", "prompt": "x"},  # SES-0587
        {"name": "t1", "prompt": "x", "temperature": 5.5},  # SES-0588
        {"name": "t2", "prompt": "x", "temperature": -1},  # SES-0588
        {"name": "t3", "prompt": "x", "temperature": "hot"},  # SES-0589
        {"name": "m1", "prompt": "x", "mode": "weird"},  # SES-0590
    ],
)
async def test_create_rejects_invalid_payloads(client: AsyncClient, payload):
    resp = await client.post("/api/agent", json=payload)
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY, resp.text


@pytest.mark.asyncio
async def test_update_rejects_out_of_range_temperature_and_strict_delegatable(client: AsyncClient):
    await _create(client, "reviewer-v")
    assert (await client.put("/api/agent/reviewer-v", json={"temperature": 9})).status_code == 422  # SES-0605
    assert (await client.patch("/api/agent/reviewer-v/delegatable", json={"delegatable": "yes"})).status_code == 422  # SES-0610
    assert (await client.patch("/api/agent/reviewer-v/delegatable", json={"delegatable": False})).status_code == 200


# ---------------------------------------------------------------------------
# admin-only mutations  (SES-0595 / 0606 / 0563)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_members_cannot_change_global_agent_config(client: AsyncClient, monkeypatch):
    await _create(client, "reviewer-m")
    _as_member(monkeypatch)
    assert (await client.post("/api/agent", json={"name": "member-agent", "prompt": "x"})).status_code == 403  # SES-0595
    assert (await client.put("/api/agent/reviewer-m", json={"prompt": "hijack"})).status_code == 403  # SES-0606
    assert (await client.delete("/api/agent/reviewer-m")).status_code == 403
    assert (await client.put("/api/agent/rex/model", json={"model": None})).status_code == 403
    assert (await client.post("/api/agent/refresh")).status_code == 200  # SES-0563 refresh stays open
    assert (await client.get("/api/agent/reviewer-m")).status_code == 200


# ---------------------------------------------------------------------------
# delete semantics  (SES-0615 / 0616 / 0617 / 0620 / 0621)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_unknown_and_builtin_agents_are_404(client: AsyncClient):
    assert (await client.delete("/api/agent/ghost")).status_code == 404  # SES-0617
    resp = await client.delete("/api/agent/rex")  # SES-0615
    assert resp.status_code == 404
    assert (await client.get("/api/agent/rex")).status_code == 200
    await _create(client, "reviewer-d")
    assert (await client.delete("/api/agent/reviewer-d")).status_code == 200
    assert (await client.delete("/api/agent/reviewer-d")).status_code == 404  # SES-0620


@pytest.mark.asyncio
async def test_delete_builtin_with_stale_storage_record_is_404(client: AsyncClient):
    """SES-0616: a leftover agent/custom/rex record must not unregister the built-in."""
    from flocks.storage.storage import Storage

    await Storage.write("agent/custom/rex", {"name": "rex", "prompt": "stale"})
    resp = await client.delete("/api/agent/rex")
    assert resp.status_code == 404
    assert (await client.get("/api/agent/rex")).status_code == 200
    assert (await client.get("/api/agent/rex")).json()["native"] is True


@pytest.mark.asyncio
async def test_delete_agent_while_delegation_running(client: AsyncClient, session_id: str):
    """SES-0621: deleting the agent does not touch a running child session; new chats fail."""
    from flocks.session.core.status import SessionStatus, SessionStatusBusy

    await _create(client, "reviewer-run")
    child = await client.post("/api/session", json={"parentID": session_id})
    assert child.status_code == 200
    SessionStatus.set(child.json()["id"], SessionStatusBusy())
    try:
        assert (await client.delete("/api/agent/reviewer-run")).status_code == 200
        assert (await client.get(f"/api/session/{child.json()['id']}")).status_code == 200
        assert SessionStatus.get(child.json()["id"]).type == "busy"
        resp = await client.post(
            f"/api/session/{session_id}/message",
            json={"parts": [{"type": "text", "text": "x"}], "noReply": True, "agent": "reviewer-run"},
        )
        assert resp.status_code == 400
    finally:
        SessionStatus.clear(child.json()["id"])


# ---------------------------------------------------------------------------
# model overrides  (SES-0623 / 0624 / 0625 / 0630)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_model_override_keeps_model_when_only_temperature_is_sent(client: AsyncClient, monkeypatch):
    from flocks.storage.storage import Storage

    monkeypatch.setattr(agent_routes, "_require_known_provider", lambda model: None)
    resp = await client.put("/api/agent/rex/model", json={"model": {"providerID": "openai", "modelID": "gpt-4o"}, "temperature": 0.2})
    assert resp.status_code == 200 and resp.json()["model"] == {"modelID": "gpt-4o", "providerID": "openai"}
    resp = await client.put("/api/agent/rex/model", json={"temperature": 0.9})  # SES-0624
    assert resp.status_code == 200
    assert resp.json()["model"] == {"modelID": "gpt-4o", "providerID": "openai"}
    assert resp.json()["temperature"] == 0.9
    resp = await client.put("/api/agent/rex/model", json={"model": None})  # SES-0623
    assert resp.status_code == 200
    overrides = await Storage.read("agent/model_overrides")
    assert "modelID" not in overrides["rex"] and overrides["rex"]["temperature"] == 0.9
    # SES-0625: nothing left in the entry -> key removed
    await Storage.write("agent/model_overrides", {"rex": {"modelID": "gpt-4o", "providerID": "openai"}})
    resp = await client.put("/api/agent/rex/model", json={"model": None})
    assert resp.status_code == 200
    assert "rex" not in (await Storage.read("agent/model_overrides") or {})


@pytest.mark.asyncio
async def test_model_override_rejects_unknown_provider(client: AsyncClient):
    """SES-0630."""
    resp = await client.put("/api/agent/rex/model", json={"model": {"providerID": "nope", "modelID": "x"}})
    assert resp.status_code == 400
    assert "nope" in resp.json()["message"]


# ---------------------------------------------------------------------------
# error paths  (SES-0561 / 0569 / 0634)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_error_returns_500(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(agent_routes.Agent, "refresh", AsyncMock(side_effect=RuntimeError("disk full")))
    resp = await client.post("/api/agent/refresh")
    assert resp.status_code == 500
    assert "disk full" in resp.json()["message"]


@pytest.mark.asyncio
async def test_list_survives_override_storage_error(client: AsyncClient, monkeypatch):
    from flocks.storage.storage import Storage

    monkeypatch.setattr(Storage, "read", AsyncMock(side_effect=RuntimeError("storage down")))
    resp = await client.get("/api/agent")
    assert resp.status_code == 200
    rex = next(a for a in resp.json() if a["name"] == "rex")
    assert rex["model"] is None


@pytest.mark.asyncio
async def test_agent_test_loop_error_published(client: AsyncClient, monkeypatch):
    from flocks.server.routes import event as event_routes
    from flocks.session.session_loop import SessionLoop

    published = []

    async def fake_publish(event_type, properties=None, directory=None):
        published.append((event_type, properties))

    monkeypatch.setattr(event_routes, "publish_event", fake_publish)
    monkeypatch.setattr(SessionLoop, "run", AsyncMock(side_effect=RuntimeError("provider unavailable")))
    resp = await client.post("/api/agent/rex/test", json={"test_prompt": "ping"})
    assert resp.status_code == 200 and resp.json()["status"] == "started"
    for _ in range(50):
        if any(t == "session.error" for t, _ in published):
            break
        await asyncio.sleep(0.02)
    errors = [p for t, p in published if t == "session.error"]
    assert errors and errors[0]["error"]["name"] == "LoopError"
    assert errors[0]["sessionID"] == resp.json()["sessionId"]


# ---------------------------------------------------------------------------
# user-level YAML plugin agents  (SES-0558 / 0560 / 0602 / 0618)
# ---------------------------------------------------------------------------

@pytest.fixture
def user_plugin_agents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the user-level plugin agent dir (normally ~/.flocks/plugins/agents) at a temp dir."""
    from flocks.agent import agent_factory

    root = tmp_path / "plugins" / "agents"
    root.mkdir(parents=True)
    monkeypatch.setattr(agent_factory, "_PLUGIN_AGENTS_DIR", root)
    return root


def _write_yaml(root: Path, name: str, body: str) -> Path:
    (root / name).mkdir(parents=True, exist_ok=True)
    path = root / name / "agent.yaml"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_user_yaml_agent_is_not_native_and_supports_update_and_delete(client: AsyncClient, user_plugin_agents: Path):
    from flocks.storage.storage import Storage

    _write_yaml(user_plugin_agents, "foo", "name: foo\nmode: subagent\nprompt: hello\ntemperature: 0.7\n")
    assert (await client.post("/api/agent/refresh")).status_code == 200
    resp = await client.get("/api/agent/foo")
    assert resp.status_code == 200 and resp.json()["native"] is False  # SES-0558

    resp = await client.put("/api/agent/foo", json={"temperature": 0.3, "skills": ["s1"]})  # SES-0602
    assert resp.status_code == 200
    assert "temperature: 0.3" in (user_plugin_agents / "foo" / "agent.yaml").read_text()
    assert await Storage.read("agent/custom/foo") == {"skills": ["s1"]}
    assert (await client.get("/api/agent/foo")).json()["skills"] == ["s1"]

    resp = await client.delete("/api/agent/foo")  # SES-0618
    assert resp.status_code == 200
    assert not (user_plugin_agents / "foo").exists()


def test_missing_name_uses_directory_name_and_flat_file_without_name_is_skipped(user_plugin_agents: Path):
    """SES-0560: subdirectory layout falls back to the folder name; a nameless flat file is ignored."""
    from flocks.agent import agent_factory

    _write_yaml(user_plugin_agents, "noname", "mode: subagent\nprompt: x\n")
    (user_plugin_agents / "flat.yaml").write_text("mode: subagent\nprompt: x\n", encoding="utf-8")
    loaded = agent_factory.scan_and_load()
    assert "noname" in loaded and loaded["noname"].native is False
    assert not any(info.native is False and info.name == "flat" for info in loaded.values())

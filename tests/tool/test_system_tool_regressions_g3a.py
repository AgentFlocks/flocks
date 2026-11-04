"""Regressions for the system/task tools found in the 2026-09-14 full-suite run (G3a)."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from flocks.tool.registry import PermissionRequest, ToolContext, ToolRegistry


@pytest.fixture(autouse=True)
async def _isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point FLOCKS_ROOT at a throwaway tree so config/secret/storage writes stay local."""
    root = tmp_path / "root"
    (root / "config").mkdir(parents=True)
    (root / "config" / "flocks.json").write_text("{}")
    (root / "config" / ".secret.json").write_text("{}")
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(root / "data"))
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(root / "workspace"))
    monkeypatch.delenv("FLOCKS_CONFIG_DIR", raising=False)
    monkeypatch.delenv("_FLOCKS_SERVER_PORT", raising=False)
    from flocks.config.config import Config
    from flocks.storage.storage import Storage

    monkeypatch.setattr(Config, "_global_config", None)
    monkeypatch.setattr(Config, "_cached_config", None)
    monkeypatch.setattr(Storage, "_db_path", None)
    monkeypatch.setattr(Storage, "_initialized", False)
    ToolRegistry.init()
    yield root
    # channel_message's in-process fallback opens the binding DB (own aiosqlite
    # thread); close it so the interpreter does not hang at exit.
    try:
        from flocks.channel.inbound.session_binding import close_binding_db

        await close_binding_db()
    except Exception:
        pass


class _Ask:
    def __init__(self, deny: bool = False):
        self.calls: list[PermissionRequest] = []
        self.deny = deny

    async def __call__(self, req: PermissionRequest) -> None:
        self.calls.append(req)
        if self.deny:
            raise PermissionError("denied")


def _ctx(session_id: str = "s", ask: _Ask | None = None, **extra) -> ToolContext:
    return ToolContext(session_id=session_id, message_id="m", extra=extra, permission_callback=ask)


# ---------------------------------------------------------------- add_provider / add_model (TOL-0462/0464/0470)

async def test_add_provider_rejects_empty_name(_isolated_root: Path):
    result = await ToolRegistry.execute("add_provider", _ctx(), name="  ", base_url="https://x", api_key="k")

    assert result.success is False and "name" in result.error.lower()
    assert "custom-" not in json.loads((_isolated_root / "config" / "flocks.json").read_text()).get("provider", {})


async def test_add_provider_masks_short_keys_completely():
    result = await ToolRegistry.execute("add_provider", _ctx(), name="Short", base_url="https://x", api_key="abc123")

    assert result.success is True
    assert "abc123" not in result.output and "abc1***c123" not in result.output
    assert "API Key: ***" in result.output


async def test_add_provider_still_hints_long_keys():
    result = await ToolRegistry.execute("add_provider", _ctx(), name="Long", base_url="https://x", api_key="sk-1234567890abcdef")

    assert "API Key: sk-1***cdef" in result.output


async def test_add_model_rejects_non_positive_limits(_isolated_root: Path):
    await ToolRegistry.execute("add_provider", _ctx(), name="P", base_url="https://x", api_key="sk-1234567890abcdef")
    result = await ToolRegistry.execute("add_model", _ctx(), provider_id="P", model_id="neg", context_window=-1)

    assert result.success is False and "context_window" in result.error
    models = json.loads((_isolated_root / "config" / "flocks.json").read_text())["provider"]["custom-p"].get("models", {})
    assert "neg" not in models


# ---------------------------------------------------------------- session_manage (TOL-0481/0482/0490)

async def _make_session(title: str, owner: str | None = None):
    from flocks.session.session import Session

    kwargs = {}
    if owner:
        kwargs = {"owner_user_id": owner, "owner_username": owner}
    return await Session.create(project_id="default", directory=os.getcwd(), title=title, **kwargs)


async def test_session_list_hides_other_users_sessions():
    from flocks.auth.context import AuthUser, reset_current_auth_user, set_current_auth_user

    mine = await _make_session("mine", owner="user-b")
    theirs = await _make_session("theirs", owner="user-a")
    token = set_current_auth_user(AuthUser(id="user-b", username="user-b", role="member"))
    try:
        result = await ToolRegistry.execute("session_manage", _ctx(session_id=mine.id), action="list")
    finally:
        reset_current_auth_user(token)

    ids = [s["id"] for s in json.loads(result.output)["sessions"]]
    assert mine.id in ids and theirs.id not in ids


async def test_session_list_rejects_negative_offset():
    result = await ToolRegistry.execute("session_manage", _ctx(), action="list", offset=-5, limit=2)

    assert result.success is False and "offset" in result.error


async def test_session_delete_refuses_current_session():
    from flocks.session.session import Session

    me = await _make_session("self")
    ask = _Ask()
    result = await ToolRegistry.execute("session_manage", _ctx(session_id=me.id, ask=ask), action="delete", session_id=me.id)

    assert result.success is False and "自身" in result.error
    assert not ask.calls
    assert await Session.get_by_id(me.id) is not None


# ---------------------------------------------------------------- flocks_mcp (TOL-0511/0517)

async def test_flocks_mcp_local_add_goes_through_permission_gate(tmp_path: Path, _isolated_root: Path):
    marker = tmp_path / "pwned"
    ask = _Ask(deny=True)
    result = await ToolRegistry.execute(
        "flocks_mcp", _ctx(ask=ask), subcommand="add", name="evil",
        config={"type": "local", "command": ["bash", "-c", f"echo x > {marker}"]},
    )

    assert ask.calls and ask.calls[0].permission == "bash"
    assert ask.calls[0].patterns == [f"bash -c echo x > {marker}"]
    assert result.success is False and not marker.exists()
    assert "evil" not in json.loads((_isolated_root / "config" / "flocks.json").read_text()).get("mcp", {})


async def test_flocks_mcp_remote_add_does_not_ask(monkeypatch: pytest.MonkeyPatch):
    ask = _Ask(deny=True)
    result = await ToolRegistry.execute(
        "flocks_mcp", _ctx(ask=ask), subcommand="add", name="pend",
        config={"type": "remote", "url": "https://x/mcp", "headers": {"X-API-Key": ""}},
    )

    assert not ask.calls and result.success is True


async def test_flocks_mcp_connect_reports_reason_when_client_gives_none(monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace

    from flocks.config.config_writer import ConfigWriter
    from flocks.mcp import MCP

    ConfigWriter.add_mcp_server("slow", {"type": "remote", "url": "http://127.0.0.1:9/mcp", "timeout": 1})

    async def fake_connect(name, config):
        return False

    async def fake_status():
        return {"slow": SimpleNamespace(status="error", error="", tools_count=0)}

    monkeypatch.setattr(MCP, "connect", fake_connect)
    monkeypatch.setattr(MCP, "status", fake_status)
    result = await ToolRegistry.execute("flocks_mcp", _ctx(), subcommand="connect", name="slow")

    assert result.success is False
    assert result.error == "Failed to connect to 'slow'. Check the config and server availability."


async def test_flocks_mcp_connect_times_out_even_if_client_swallows_cancel(monkeypatch: pytest.MonkeyPatch):
    from flocks.config.config_writer import ConfigWriter
    from flocks.mcp import MCP

    ConfigWriter.add_mcp_server("hang", {"type": "remote", "url": "http://127.0.0.1:9/mcp", "timeout": 0.5})

    async def hanging_connect(name, config):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            return False  # a transport that swallows cancellation
        return True

    monkeypatch.setattr(MCP, "connect", hanging_connect)
    started = time.monotonic()
    result = await ToolRegistry.execute("flocks_mcp", _ctx(), subcommand="connect", name="hang")

    assert result.error == "Connection timed out while connecting to 'hang'."
    assert time.monotonic() - started < 10


# ---------------------------------------------------------------- todo (TOL-0541)

async def test_todo_write_requires_status_field():
    from flocks.session.session import Session

    session = await Session.create(project_id="default", directory=os.getcwd(), title="todo")
    result = await ToolRegistry.execute(
        "todo", _ctx(session_id=session.id), action="write", todos=[{"id": "a", "content": "x"}],
    )

    assert result.success is False
    assert result.error.startswith("todos[0]") and "status" in result.error


# ---------------------------------------------------------------- run_workflow_node (TOL-0558)

async def test_run_workflow_node_resolves_registered_workflow_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = tmp_path / "proj"
    wf_dir = project / ".flocks" / "plugins" / "workflows" / "wf_g3a"
    wf_dir.mkdir(parents=True)
    (wf_dir / "workflow.json").write_text(json.dumps({
        "name": "wf_g3a", "start": "n1",
        "nodes": [{"id": "n1", "type": "python", "code": "outputs = {'echo': dict(inputs)}"}],
        "edges": [],
    }))
    monkeypatch.chdir(project)

    result = await ToolRegistry.execute("run_workflow_node", _ctx(), workflow="wf_g3a", node_id="n1", inputs={"k": "v"})

    assert result.success is True, result.error
    assert result.metadata["outputs"] == {"echo": {"k": "v"}}


# ---------------------------------------------------------------- channel_message (TOL-0593)

@pytest.mark.parametrize(
    "media",
    [
        "config/.secret.json",
        "data/flocks.db",
        "logs/2026-09-16/flocks.log",
        "vendor-state/keys/license.json",  # any extension's own state dir under the runtime root
        "~/.ssh/id_rsa",
        "/somewhere/server.pem",
    ],
)
async def test_channel_message_refuses_sensitive_local_media(_isolated_root: Path, media: str):
    path = str(_isolated_root / media) if not media.startswith(("~", "/")) else media
    result = await ToolRegistry.execute("channel_message", _ctx(), session_id="s", message="x", media=path)

    assert result.success is False and "拒绝发送" in result.error


async def test_channel_message_still_accepts_urls_and_shareable_files(_isolated_root: Path, tmp_path: Path):
    from flocks.tool.channel import channel_message as cm

    assert cm._sensitive_media_error("https://example.com/a.png") is None
    plain = tmp_path / "report.png"
    plain.write_bytes(b"x")
    assert cm._sensitive_media_error(str(plain)) is None
    # tool outputs / project files under the runtime root stay shareable
    for sub in ("workspace/outputs/2026-09-16/report.pdf", "projects/demo/summary.png"):
        target = _isolated_root / sub
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
        assert cm._sensitive_media_error(str(target)) is None, sub


# ---------------------------------------------------------------- flocks_skills (TOL-0626 follow-up)

async def test_flocks_skills_timeout_kills_cli_process_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from flocks.tool.skill import flocks_skills as fs

    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    pidfile = tmp_path / "child.pid"
    fake = fake_dir / "flocks"
    # The wrapper records its child's pid so the assertion targets exactly that
    # process (a bare ``pgrep -f "sleep 30"`` would also match unrelated shells).
    fake.write_text(f"#!/bin/sh\nsleep 30 &\necho $! > {pidfile}\nwait\n")
    fake.chmod(0o755)
    monkeypatch.setattr(fs, "_flocks_executable", lambda: str(fake))
    monkeypatch.setattr(fs, "_TIMEOUT_SEC", 1)

    result = await ToolRegistry.execute("flocks_skills", _ctx(), subcommand="status")

    assert result.success is False and result.error.startswith("Command timed out after 1s:")
    child_pid = int(pidfile.read_text().strip())
    for _ in range(20):  # SIGKILL delivery is asynchronous; give it up to 2s
        await asyncio.sleep(0.1)
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
    else:
        pytest.fail(f"CLI child {child_pid} survived the timeout")

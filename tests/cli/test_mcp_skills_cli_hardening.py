"""Regression tests for `flocks mcp` / `flocks skills` defects found in the 2026-09 full test pass.

Covers:
- ``mcp list`` reads the validated ``Config.mcp`` models (it used to skip every entry and report
  "No MCP servers configured"), and shows ``enabled: false`` servers as ``disabled``
- the status icon/text maps only reference existing ``McpStatus`` members (table mode crashed
  with ``AttributeError: NEEDS_CLIENT_REGISTRATION`` as soon as one server was configured)
- ``mcp add --oauth/--no-oauth`` skips the interactive prompt; corrupt ``flocks.json`` yields a
  readable error and is left untouched; ``-c`` honours shell quoting for paths with spaces
- ``mcp logout`` / ``mcp debug`` work against ``McpAuth`` entries (``list_all``, dict tokens)
- ``mcp tools`` / ``mcp refresh`` initialise the manager so a fresh process sees MCP tools
- ``skills install --scope`` rejects unknown scopes instead of silently installing globally
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import flocks.cli.commands.mcp as mcp_cmd
import flocks.cli.commands.skill as skill_cmd
from flocks.config.config import ConfigInfo, McpLocalConfig, McpRemoteConfig
from flocks.mcp.auth import McpAuth
from flocks.mcp.types import McpStatus, McpStatusInfo

runner = CliRunner(mix_stderr=False)


def _flat(text: str) -> str:
    return " ".join(text.replace("│", " ").split())


class _FakeManager:
    def __init__(self, statuses):
        self._statuses = statuses
        self.status_calls = 0

    async def status(self):
        self.status_calls += 1
        return dict(self._statuses)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "root"))
    # Config caches the resolved runtime root; drop it so each test sees its own FLOCKS_ROOT.
    monkeypatch.setattr(mcp_cmd.Config, "_global_config", None)
    McpAuth.clear()
    yield
    McpAuth.clear()


def _config_with(models: dict) -> ConfigInfo:
    return ConfigInfo(mcp=models)


# --------------------------------------------------------------------------- mcp list
def test_mcp_list_shows_validated_config_models_and_disabled_servers(monkeypatch) -> None:
    config = _config_with({
        "good": McpLocalConfig(type="local", command=["python", "server.py"]),
        "off": McpLocalConfig(type="local", command=["echo"], enabled=False),
        "remote": McpRemoteConfig(type="remote", url="https://mcp.example/sse"),
    })
    manager = _FakeManager({
        "good": McpStatusInfo(status=McpStatus.CONNECTED),
        "remote": McpStatusInfo(status=McpStatus.FAILED, error="boom"),
    })

    async def fake_get():
        return config

    monkeypatch.setattr(mcp_cmd.Config, "get", fake_get)
    monkeypatch.setattr(mcp_cmd, "get_manager", lambda: manager)

    table = runner.invoke(mcp_cmd.mcp_app, ["list"])
    as_json = runner.invoke(mcp_cmd.mcp_app, ["list", "--format", "json"])

    assert table.exit_code == 0, table.output
    assert "No MCP servers configured" not in table.stdout
    assert "good connected" in _flat(table.stdout)
    assert "off disabled" in _flat(table.stdout)
    assert "remote failed" in _flat(table.stdout) and "boom" in table.stdout
    assert "3 server(s)" in table.stdout

    data = {item["name"]: item for item in json.loads(as_json.stdout)}
    assert data["good"]["status"] == "connected"
    assert data["off"]["status"] == "disabled"
    assert data["remote"]["error"] == "boom"


@pytest.mark.parametrize("status", list(McpStatus))
def test_status_maps_cover_every_mcp_status(status) -> None:
    assert isinstance(mcp_cmd._get_status_icon(status), str)
    assert mcp_cmd._get_status_text(status) != "unknown"


# --------------------------------------------------------------------------- mcp add
def _write_config(tmp_path, payload: str) -> None:
    cfg_dir = tmp_path / "root" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "flocks.json").write_text(payload, encoding="utf-8")


def _read_mcp(tmp_path) -> dict:
    return json.loads((tmp_path / "root" / "config" / "flocks.json").read_text(encoding="utf-8"))["mcp"]


def test_mcp_add_remote_oauth_flags_skip_the_prompt(monkeypatch, tmp_path) -> None:
    _write_config(tmp_path, "{}")

    def _no_prompt(*_args, **_kwargs):
        raise AssertionError("must not prompt when --oauth/--no-oauth is given")

    monkeypatch.setattr(mcp_cmd.Confirm, "ask", _no_prompt)

    off = runner.invoke(mcp_cmd.mcp_app, ["add", "-n", "r1", "-t", "remote", "--url", "https://x/sse", "--no-oauth"])
    on = runner.invoke(mcp_cmd.mcp_app, ["add", "-n", "r2", "-t", "remote", "--url", "https://y/sse", "--oauth"])

    assert off.exit_code == 0, off.output
    assert on.exit_code == 0, on.output
    mcp = _read_mcp(tmp_path)
    assert mcp["r1"] == {"type": "remote", "url": "https://x/sse"}
    assert mcp["r2"] == {"type": "remote", "url": "https://y/sse", "oauth": {}}


def test_mcp_add_reports_corrupt_config_without_touching_it(tmp_path) -> None:
    _write_config(tmp_path, "{oops")

    result = runner.invoke(mcp_cmd.mcp_app, ["add", "-n", "a", "-t", "local", "-c", "echo"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "not valid JSON" in _flat(result.stdout)
    assert (tmp_path / "root" / "config" / "flocks.json").read_text(encoding="utf-8") == "{oops"


def test_mcp_add_local_command_honours_shell_quoting(tmp_path) -> None:
    _write_config(tmp_path, "{}")

    result = runner.invoke(mcp_cmd.mcp_app, ["add", "-n", "sp", "-t", "local", "-c", '"/opt/my tools/server" --x'])

    assert result.exit_code == 0, result.output
    assert _read_mcp(tmp_path)["sp"]["command"] == ["/opt/my tools/server", "--x"]


# --------------------------------------------------------------------------- mcp logout / debug
def test_mcp_logout_and_debug_use_mcp_auth_entries(monkeypatch) -> None:
    config = _config_with({"r": McpRemoteConfig(type="remote", url="https://r/sse")})

    async def fake_get():
        return config

    monkeypatch.setattr(mcp_cmd.Config, "get", fake_get)
    asyncio.run(McpAuth.set("r", {"access_token": "tok-" + "A" * 40, "refresh_token": "ref"}, expires_in=-7200))

    debug = runner.invoke(mcp_cmd.mcp_app, ["debug", "r"])
    assert debug.exit_code == 0, debug.output
    assert "Auth status: ⚠ expired" in debug.stdout
    assert "Access token: tok-AAAAAAAAAAAAAAAA..." in debug.stdout
    assert "A" * 40 not in debug.stdout
    assert "(EXPIRED)" in debug.stdout and "Refresh token: present" in debug.stdout

    missing = runner.invoke(mcp_cmd.mcp_app, ["logout", "nope"])
    assert missing.exit_code == 1 and "No credentials found for: nope" in missing.stdout

    removed = runner.invoke(mcp_cmd.mcp_app, ["logout", "r"])
    assert removed.exit_code == 0 and "Removed OAuth credentials for r" in removed.stdout
    assert asyncio.run(McpAuth.get("r")) is None


# --------------------------------------------------------------------------- mcp tools / refresh
def test_mcp_tools_and_refresh_initialise_the_manager(monkeypatch) -> None:
    manager = _FakeManager({})
    monkeypatch.setattr(mcp_cmd, "get_manager", lambda: manager)
    monkeypatch.setattr("flocks.mcp.McpToolRegistry.get_server_tools", staticmethod(lambda server: []))
    monkeypatch.setattr("flocks.mcp.McpToolRegistry.get_all_servers", staticmethod(lambda: []))

    tools = runner.invoke(mcp_cmd.mcp_app, ["tools", "good"])
    refresh = runner.invoke(mcp_cmd.mcp_app, ["refresh"])

    assert tools.exit_code == 0 and "No tools from server: good" in tools.stdout
    assert refresh.exit_code == 0 and "No MCP servers with tools" in refresh.stdout
    assert manager.status_calls == 2


# --------------------------------------------------------------------------- skills install --scope
def test_skills_install_rejects_unknown_scope(monkeypatch) -> None:
    calls: list = []

    async def fake_install(source, scope="global", yes=False):
        calls.append((source, scope))
        return SimpleNamespace(success=False, error="unreachable")

    monkeypatch.setattr(skill_cmd.SkillInstaller, "install_from_source", staticmethod(fake_install))

    result = runner.invoke(skill_cmd.skill_app, ["install", "/tmp/s", "--scope", "team"])

    assert result.exit_code == 2
    assert "global | project" in _flat(result.stderr)
    assert calls == []

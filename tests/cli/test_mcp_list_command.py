import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from rich.console import Console
from typer.testing import CliRunner

import flocks.cli.commands.mcp as mcp_cmd
from flocks.config.config import ConfigInfo
from flocks.mcp.types import McpStatus, McpStatusInfo

runner = CliRunner()

LONG_COMMAND = ["/opt/" + "very-long-virtualenv-" * 6 + "/bin/python", "/srv/tools/[red]server[/red].py"]


def _use_config(monkeypatch, servers: dict, statuses: dict | None = None) -> ConfigInfo:
    config = ConfigInfo.model_validate({"mcp": servers})
    monkeypatch.setattr(mcp_cmd.Config, "get", AsyncMock(return_value=config))
    monkeypatch.setattr(mcp_cmd, "get_manager", lambda: SimpleNamespace(status=AsyncMock(return_value=statuses or {})))
    monkeypatch.setattr(mcp_cmd.McpAuth, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(mcp_cmd, "console", Console(width=80))
    return config


def test_mcp_list_shows_servers_that_config_parsed_into_models(monkeypatch):
    config = _use_config(monkeypatch, {
        "local-tool": {"type": "local", "command": LONG_COMMAND},
        "remote-tool": {"type": "remote", "url": "https://mcp.example.com/sse"},
    })
    # This is what Config.get() hands the CLI: models, not dicts.
    assert not isinstance(config.mcp["local-tool"], dict)

    listed = runner.invoke(mcp_cmd.mcp_app, ["list", "--format", "json"])

    assert listed.exit_code == 0, listed.output
    rows = json.loads(listed.stdout)
    assert [row["name"] for row in rows] == ["local-tool", "remote-tool"]
    assert rows[0]["command"] == LONG_COMMAND
    assert rows[1]["url"] == "https://mcp.example.com/sse"
    assert rows[0]["status"] == "not_initialized"

    table = runner.invoke(mcp_cmd.mcp_app, ["list"])
    assert table.exit_code == 0
    assert "local-tool" in table.stdout and "No MCP servers configured" not in table.stdout


def test_mcp_list_json_without_servers_is_an_empty_array(monkeypatch):
    _use_config(monkeypatch, {})

    listed = runner.invoke(mcp_cmd.mcp_app, ["list", "--format", "json"])

    assert listed.exit_code == 0
    assert json.loads(listed.stdout) == []


def test_mcp_debug_reads_a_remote_server_from_the_config_model(monkeypatch):
    _use_config(monkeypatch, {"remote-tool": {"type": "remote", "url": "https://mcp.example.com/sse"}})

    result = runner.invoke(mcp_cmd.mcp_app, ["debug", "remote-tool"])

    assert result.exit_code == 0, result.output
    assert "URL: https://mcp.example.com/sse" in result.stdout


def test_mcp_list_table_shows_every_runtime_status(monkeypatch):
    statuses = {
        "connected-tool": McpStatusInfo(status=McpStatus.CONNECTED, tools_count=1),
        "failed-tool": McpStatusInfo(status=McpStatus.FAILED, error="spawn failed: [Errno 2] [/x]"),
        "connecting-tool": McpStatusInfo(status=McpStatus.CONNECTING),
        "disconnected-tool": McpStatusInfo(status=McpStatus.DISCONNECTED),
    }
    _use_config(monkeypatch, {name: {"type": "local", "command": ["python", f"{name}.py"]} for name in statuses}, statuses)
    monkeypatch.setattr(mcp_cmd, "console", Console(width=200))

    table = runner.invoke(mcp_cmd.mcp_app, ["list"])

    assert table.exit_code == 0, table.output
    for text in ("connected", "failed", "connecting", "disconnected", "spawn failed: [Errno 2] [/x]"):
        assert text in table.stdout


def test_sse_servers_count_as_remote(monkeypatch):
    # "sse" is an accepted alias of "remote": the runtime connects it as one.
    _use_config(monkeypatch, {"sse-tool": {"type": "sse", "url": "https://mcp.example.com/sse"}})
    monkeypatch.setattr(mcp_cmd, "console", Console(width=200))

    table = runner.invoke(mcp_cmd.mcp_app, ["list"])
    assert table.exit_code == 0, table.output
    assert "https://mcp.example.com/sse" in table.stdout

    auth = runner.invoke(mcp_cmd.mcp_app, ["auth", "sse-tool"])
    assert auth.exit_code == 0, auth.output
    assert "Starting OAuth flow" in auth.stdout

    debug = runner.invoke(mcp_cmd.mcp_app, ["debug", "sse-tool"])
    assert debug.exit_code == 0, debug.output
    assert "URL: https://mcp.example.com/sse" in debug.stdout

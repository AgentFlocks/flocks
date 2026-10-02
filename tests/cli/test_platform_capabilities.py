import json
from pathlib import Path
from unittest.mock import AsyncMock

from typer.testing import CliRunner

from flocks.cli.commands.agent import agent_app
from flocks.cli.commands.device import device_app
from flocks.cli.commands.model import model_app
from flocks.cli.commands.plugin import plugin_app, install_local, list_plugins, PluginKind
from flocks.cli.commands.session import session_app
from flocks.cli.commands.workflow import workflow_app
from flocks.config.config import Config, ConfigInfo

runner = CliRunner(mix_stderr=False)


def test_agent_commands_use_async_registry(monkeypatch):
    from flocks.agent.registry import Agent
    from flocks.agent.agent import AgentInfo
    from flocks.permission.rule import PermissionRule, PermissionLevel
    agent = AgentInfo(name="test", mode="primary", native=True, permission=[PermissionRule(permission="read", level=PermissionLevel.ALLOW)])
    monkeypatch.setattr(Agent, "list_visible", AsyncMock(return_value=[agent]))
    monkeypatch.setattr(Agent, "get", AsyncMock(return_value=agent))
    monkeypatch.setattr(Agent, "has_tool", AsyncMock(return_value=True))
    for args in [["list"], ["show", "test"], ["permissions", "test", "read"]]:
        result = runner.invoke(agent_app, args)
        assert result.exit_code == 0, result.exception
    result = runner.invoke(agent_app, ["list", "--format", "json"])
    assert json.loads(result.stdout)[0]["name"] == "test"


def test_device_and_model_delegate_to_existing_registries(monkeypatch):
    from types import SimpleNamespace
    from flocks.provider.provider import Provider, ModelInfo
    import flocks.tool.device.store as store
    calls = AsyncMock(return_value=[SimpleNamespace(model_dump=lambda **_: {"id": "dev", "fields": {"token": "***"}})])
    monkeypatch.setattr(store, "list_devices", calls)
    result = runner.invoke(device_app, ["--group", "g1", "--format", "json"])
    assert result.exit_code == 0, result.exception
    assert json.loads(result.stdout)[0]["fields"] == {"token": "***"}
    calls.assert_awaited_once_with("g1")
    monkeypatch.setattr(Provider, "init", AsyncMock())
    monkeypatch.setattr(Provider, "apply_config", AsyncMock())
    monkeypatch.setattr(Config, "_cached_config", ConfigInfo())
    monkeypatch.setattr(Provider, "list_models", lambda provider: [ModelInfo(id="m", name="Model", provider_id=provider)])
    result = runner.invoke(model_app, ["--provider", "p", "--format", "json"])
    assert result.exit_code == 0, result.exception
    assert json.loads(result.stdout)[0]["provider_id"] == "p"


def test_local_plugin_install_is_discoverable_and_loadable(tmp_path):
    from flocks.plugin import PluginLoader, ExtensionPoint
    source = tmp_path / "hello.py"
    source.write_text('CLI_TEST = ["hello"]\n')
    root = tmp_path / "plugins"
    result = runner.invoke(plugin_app, ["install", str(source), "--kind", "tools", "--root", str(root)])
    assert result.exit_code == 0, result.exception
    installed = Path(json.loads(result.stdout)["installed"][0])
    assert installed.read_text() == source.read_text()
    found = list_plugins(root)
    assert found[0]["path"] == str(installed)
    collected = []
    PluginLoader.register_extension_point(ExtensionPoint("CLI_TEST", "tools", lambda items, _: collected.extend(items)))
    try:
        PluginLoader.load_for_extension("CLI_TEST", [str(installed)], installed.parent)
        assert collected == ["hello"]
    finally:
        PluginLoader._extension_points.pop("CLI_TEST", None)
    result = runner.invoke(plugin_app, ["install", str(source), "--kind", "tools", "--root", str(root)])
    assert result.exit_code == 1
    assert installed.read_text() == source.read_text()


def test_plugin_symlinks_and_empty_directory_rejected(tmp_path):
    import pytest
    source = tmp_path / "source.py"
    source.write_text("TOOLS=[]")
    link = tmp_path / "link.py"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="Symlink"):
        install_local(link, tmp_path / "out", PluginKind.tools)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="No discoverable"):
        install_local(empty, tmp_path / "out", PluginKind.tools)


def test_session_commands_share_headless_entrypoint(monkeypatch, tmp_path):
    import flocks.cli.commands.exec as execution
    seen = []
    def invoke(options, format):
        seen.append(options)
    monkeypatch.setattr(execution, "invoke_headless", invoke)
    for args in [["new"], ["new", "hello"], ["resume", "s1", "--prompt", "next"], ["resume", "--last"]]:
        result = runner.invoke(session_app, args + ["-C", str(tmp_path)])
        assert result.exit_code == 0, result.exception
    assert seen[0].create_only
    assert seen[1].prompt == "hello"
    assert seen[2].session_id == "s1"
    assert seen[3].last
    for args in [["resume"], ["resume", "s1", "--last"]]:
        assert runner.invoke(session_app, args).exit_code == 2


def test_real_workflow_run_and_failure(monkeypatch, tmp_path):
    from flocks.storage.storage import Storage
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(Config, "_global_config", None)
    monkeypatch.setattr(Config, "_cached_config", ConfigInfo(sandbox={"mode": "off"}))
    monkeypatch.setattr(Storage, "_initialized", False)
    monkeypatch.setattr(Storage, "_db_path", tmp_path / "state.db")
    path = tmp_path / "workflow.json"
    graph = {"name": "cli-test", "start": "compute", "nodes": [{"id": "compute", "type": "python", "code": "outputs['answer'] = inputs['n'] + 1"}], "edges": []}
    path.write_text(json.dumps(graph))
    result = runner.invoke(workflow_app, ["run", str(path), "--inputs", '{"n":41}', "-C", str(tmp_path)])
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    payload = json.loads(result.stdout)
    assert payload["status"] == "SUCCEEDED"
    assert payload["outputs"]["answer"] == 42
    graph["nodes"][0]["code"] = "raise ValueError('expected failure')"
    path.write_text(json.dumps(graph))
    result = runner.invoke(workflow_app, ["run", str(path), "-C", str(tmp_path)])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "FAILED"


def test_agent_discovery_closes_stores_and_preserves_json(monkeypatch):
    from flocks.agent.registry import Agent
    from flocks.agent.agent import AgentInfo
    import flocks.cli.runtime as runtime
    close = AsyncMock()
    async def discover():
        print("discovery diagnostic")
        return [AgentInfo(name="test")]
    monkeypatch.setattr(Agent, "list_visible", discover)
    monkeypatch.setattr(runtime, "close_cli_resources", close)
    result = runner.invoke(agent_app, ["list", "--format", "json"])
    assert result.exit_code == 0, result.exception
    assert json.loads(result.stdout)[0]["name"] == "test"
    assert "discovery diagnostic" in result.stderr
    close.assert_awaited_once()
    monkeypatch.setattr(Agent, "list_visible", AsyncMock(return_value=[]))
    empty = runner.invoke(agent_app, ["list", "--format", "json"])
    assert empty.exit_code == 0
    assert json.loads(empty.stdout) == []

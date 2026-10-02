"""Regression paths from CLI review R01–R10; providers and writes are isolated."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from flocks.cli.headless import ExecOptions, build_config, run_headless
from flocks.config.config import Config, ConfigInfo
from flocks.session.runtime_controls import RuntimeControls, runtime_controls
from tests.cli.test_exec_command import runtime  # noqa: F401


async def test_r01_real_scheduler_dispatcher_cannot_enqueue(monkeypatch):
    from flocks.tool.task import schedule_task_center
    from flocks.tool.registry import ToolRegistry, ToolContext
    from flocks.task.manager import TaskManager
    sink = AsyncMock(side_effect=AssertionError("scheduler escaped"))
    monkeypatch.setattr(TaskManager, "create_scheduler", sink)
    tool = ToolRegistry._tools["schedule_task"]
    assert tool.handler is schedule_task_center.schedule_task
    from flocks.hooks.pipeline import HookPipeline, HookContext, HookStage
    monkeypatch.setattr(HookPipeline, "run_tool_before", AsyncMock(return_value=HookContext(HookStage.TOOL_BEFORE, {})))
    monkeypatch.setattr(HookPipeline, "run_tool_after", AsyncMock(return_value=None))
    monkeypatch.setattr(ToolRegistry, "_failure_auto_disable_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(ToolRegistry, "get", lambda name: tool)
    token = runtime_controls.set(RuntimeControls(permission_mode="bypassPermissions", allowed_tools=("schedule_task",), disallowed_tools=("bash",), max_budget=1))
    try:
        result = await ToolRegistry.execute("schedule_task", ToolContext("s", "m"), action="create", resource_type="scheduler", title="test", description="test", type="queued")
        assert not result.success and "Detached" in result.error
        sink.assert_not_called()
    finally:
        runtime_controls.reset(token)


@pytest.fixture
async def provider_runtime(runtime, monkeypatch):
    from flocks.provider.provider import Provider
    from flocks.session.runner import SessionRunner
    from flocks.session.prompt import SessionPrompt
    from flocks.memory.bootstrap import MemoryBootstrap
    from flocks.hooks.pipeline import HookPipeline
    import flocks.provider.options as provider_options
    from flocks.tool.registry import ToolRegistry, ToolInfo, ToolResult
    from flocks.provider.types import PriceConfig
    import flocks.provider.usage_service as usage
    tool = SimpleNamespace(info=ToolInfo(name="read", description="test", source="builtin"), execute=AsyncMock(return_value=ToolResult(success=True, output="read")))
    monkeypatch.setattr(ToolRegistry, "get", lambda name: tool)
    monkeypatch.setattr(ToolRegistry, "_failure_auto_disable_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(HookPipeline, "run_tool_before", AsyncMock(return_value=None))
    monkeypatch.setattr(HookPipeline, "run_tool_after", AsyncMock(return_value=None))
    monkeypatch.setattr(Provider, "get_model", lambda _: None)
    monkeypatch.setattr(Provider, "resolve_model", lambda *_: None)
    monkeypatch.setattr(SessionRunner, "_build_callable_tool_schema", AsyncMock(return_value=[]))
    monkeypatch.setattr(SessionRunner, "_build_turn_prompt_context", AsyncMock(return_value=None))
    monkeypatch.setattr(SessionPrompt, "build_system_prompt_blocks", AsyncMock(return_value=[]))
    monkeypatch.setattr(MemoryBootstrap, "bootstrap", AsyncMock(return_value={}))
    monkeypatch.setattr(HookPipeline, "has_stage_handlers", AsyncMock(return_value=False))
    monkeypatch.setattr(HookPipeline, "run_session_start", AsyncMock())
    monkeypatch.setattr(provider_options, "build_provider_options", lambda *_: {})
    monkeypatch.setattr(usage, "resolve_usage_pricing", lambda *_: PriceConfig(input=1, output=1, unit=1, currency="USD"))
    return runtime, tool


@pytest.mark.parametrize("scenario", ["error", "same_chunk", "usage_first", "usage_error", "cumulative", "missing"])
async def test_r02_r03_real_provider_stream(provider_runtime, monkeypatch, scenario):
    from flocks.provider.provider import Provider, StreamChunk
    directory, tool = provider_runtime
    calls = []
    class FakeProvider:
        _config_models = []
        def is_configured(self):
            return True
        async def chat_stream(self, **kw):
            calls.append(kw)
            if scenario == "error":
                raise ValueError("synthetic provider authentication failure")
            if scenario == "missing":
                yield StreamChunk(delta="ok", finish_reason="stop")
                return
            usage = {"prompt_tokens": 3, "completion_tokens": 2}
            tc = [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "read", "arguments": "{}"}}]
            if scenario == "usage_first":
                yield StreamChunk(usage=usage)
                yield StreamChunk(tool_calls=tc, finish_reason="tool_calls")
            elif scenario == "same_chunk":
                yield StreamChunk(usage=usage, tool_calls=tc, finish_reason="tool_calls")
            elif scenario == "usage_error":
                yield StreamChunk(usage=usage)
                raise ConnectionError("synthetic stream disconnected")
            else:
                yield StreamChunk(usage={"prompt_tokens": 3, "completion_tokens": 1})
                yield StreamChunk(usage=usage)
                yield StreamChunk(usage=usage, delta="ok", finish_reason="stop")
    monkeypatch.setattr(Provider, "get", lambda _: FakeProvider())
    budget = 10 if scenario == "cumulative" else (None if scenario == "error" else .5)
    result = await run_headless(ExecOptions(directory, prompt="hello", controls=RuntimeControls(max_budget=budget)))
    assert result["success"] == (scenario == "cumulative"), result
    if scenario == "error":
        assert "synthetic" in result["error"]
    elif scenario == "missing":
        assert "usage" in result["error"]
    else:
        assert result["usage"]["cost_by_currency"]["USD"] == 5
        assert result["usage"]["requests"] == 1
        assert result["usage"]["input_tokens"] == 3
        assert len(calls) == 1
        tool.execute.assert_not_called()


async def test_r02_real_provider_cli_and_mcp(provider_runtime, monkeypatch):
    from flocks.provider.provider import Provider
    from flocks.cli.commands.exec import invoke_headless, OutputFormat
    from flocks.cli.mcp_server import create_server
    import asyncio
    directory, _ = provider_runtime
    class BadProvider:
        _config_models = []
        def is_configured(self): return True
        async def chat_stream(self, **kw):
            raise ValueError("synthetic authentication rejected")
            yield
    monkeypatch.setattr(Provider, "get", lambda _: BadProvider())
    # CLI is synchronous; execute in a worker thread without mocking headless.
    import typer
    app = typer.Typer()
    output = directory / "result.txt"
    @app.command()
    def call():
        invoke_headless(ExecOptions(directory, prompt="hi"), OutputFormat.json, output)
    result = await asyncio.to_thread(CliRunner(mix_stderr=False).invoke, app, [])
    assert result.exit_code == 1, result.exception
    assert not json.loads(result.stdout)["success"]
    assert not output.exists()
    server = create_server(directory)
    with pytest.raises(Exception, match="synthetic"):
        await server.call_tool("flocks_exec", {"prompt": "hi"})


def test_r04_startup_log_omits_prompt_and_config(monkeypatch, tmp_path):
    import sys
    from flocks.cli.main import app
    from flocks.utils.log import Log
    import flocks.cli.commands.exec as cmd
    records = []
    monkeypatch.setattr(Log, "init", AsyncMock())
    monkeypatch.setattr(Log.Default, "info", lambda *args: records.append(args))
    monkeypatch.setattr(cmd, "run_headless", AsyncMock(return_value={"type": "result", "success": True, "session_id": "s", "result": "ok", "error": None}))
    argv = ["exec", "SENTINEL_PROMPT", "-C", str(tmp_path), "-c", "provider.p.options.apiKey=SENTINEL_SECRET", "--output-format", "json"]
    monkeypatch.setattr(sys, "argv", ["flocks", *argv])
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 0, result.exception
    start = [r for r in records if r[0] == "flocks.start"]
    assert start
    assert "SENTINEL" not in repr(start)


@pytest.mark.parametrize("mode", ["ro", "rw"])
@pytest.mark.parametrize("path_kind", ["relative", "absolute", "mount"])
async def test_r05_project_read_paths(tmp_path, monkeypatch, mode, path_kind):
    from flocks.tool.file.read import read_tool
    from flocks.tool.registry import ToolContext
    project = tmp_path / "project"
    project.mkdir()
    source = project / "README.md"
    source.write_text("project source sentinel")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    ctx = ToolContext("s", "m", extra={"sandbox": {"workspace_dir": str(project if mode == "rw" else sandbox), "agent_workspace_dir": str(project), "workspace_access": mode, "container_name": "fake"}})
    monkeypatch.setattr(Config, "_cached_config", ConfigInfo())
    path = {"relative": "README.md", "absolute": str(source), "mount": "/agent/README.md"}[path_kind]
    if mode == "rw" and path_kind == "mount":
        path = str(source)  # rw mounts project directly as the container workspace.
    token = runtime_controls.set(RuntimeControls(sandbox_required=True))
    try:
        result = await read_tool(ctx, filePath=path)
        assert result.success, result.error
        assert "project source sentinel" in result.output
        outside = await read_tool(ctx, filePath=str(tmp_path / "outside"))
        assert not outside.success and "escapes" in outside.error
        link = project / "escape"
        link.symlink_to(tmp_path)
        escaped = await read_tool(ctx, filePath="escape/outside")
        assert not escaped.success
        assert ctx.extra["sandbox"]["workspace_dir"] == str(project if mode == "rw" else sandbox)
    finally:
        runtime_controls.reset(token)


async def test_r06_headless_instance_real_bash(runtime, monkeypatch):
    from flocks.session.session_loop import SessionLoop, LoopResult
    from flocks.session.message import Message, MessageRole
    from flocks.tool.code.bash import _execute_host
    from flocks.tool.registry import ToolContext, PermissionRequest
    from flocks.project.instance import Instance
    async def loop(**kw):
        callback = kw["callbacks"].runner_callbacks.on_permission_request
        assert Instance.contains_path(str(runtime))
        assert not await callback(PermissionRequest(permission="external_directory", patterns=[str(runtime.parent)]))
        async def ask(request):
            assert await callback(request)
        ctx = ToolContext(kw["session_id"], "m", permission_callback=ask)
        result = await _execute_host(ctx, "pwd", str(runtime), 5, 5000, None)
        assert result.success, result.error
        assert str(runtime) in result.output
        msg = await Message.create(kw["session_id"], MessageRole.ASSISTANT, "ok")
        return LoopResult(action="stop", last_message=msg)
    monkeypatch.setattr(SessionLoop, "run", loop)
    result = await run_headless(ExecOptions(runtime, prompt="pwd", controls=RuntimeControls(allowed_tools=("bash",))))
    assert result["success"], result
    assert not Instance.contains_path(str(runtime))


@pytest.mark.parametrize("selection", ["agent", "explicit", "pinned"])
async def test_r07_resume_effective_agent_model(runtime, monkeypatch, selection):
    from flocks.agent.registry import Agent
    from flocks.agent.agent import AgentInfo
    from flocks.session.session import Session
    from flocks.session.session_loop import SessionLoop, LoopResult
    from flocks.session.message import Message, MessageRole
    session = await Session.create(project_id="default", directory=str(runtime), agent="old")
    if selection == "pinned":
        session.provider = "pin"
        session.model = "pin-model"
        session.model_pinned = True
    monkeypatch.setattr(Session, "get_by_id", AsyncMock(return_value=session))
    monkeypatch.setattr(Agent, "get", AsyncMock(side_effect=lambda name: AgentInfo(name=name, mode="primary", model={"provider_id": name, "model_id": name + "-model"})))
    seen = []
    async def loop(**kw):
        seen.append((kw["provider_id"], kw["model_id"]))
        msg = await Message.create(session.id, MessageRole.ASSISTANT, "ok")
        return LoopResult(action="stop", last_message=msg)
    monkeypatch.setattr(SessionLoop, "run", loop)
    result = await run_headless(ExecOptions(runtime, prompt="hi", session_id=session.id, agent="new", model="explicit/model" if selection == "explicit" else None))
    assert result["success"], result
    assert seen == [{"agent": ("new", "new-model"), "explicit": ("explicit", "model"), "pinned": ("pin", "pin-model")}[selection]]
    assert session.agent == "old"


@pytest.mark.parametrize("source", ["example", "fallback", "dynamic"])
async def test_r08_strict_product_configs(tmp_path, monkeypatch, source):
    from flocks.config.config_writer import _FALLBACK_CONFIG_TEMPLATES
    data = json.loads((Path(__file__).parents[2] / ".flocks/flocks.json.example").read_text()) if source == "example" else _FALLBACK_CONFIG_TEMPLATES["flocks.json"]
    if source == "dynamic":
        data = {**data, "provider": {"custom": {"name": "custom", "options": {"custom_option": True}, "models": {"m": {"pricing": {"input": 1}}}}}}
    monkeypatch.setattr(Config, "_cached_config", ConfigInfo.model_validate(data))
    config = await build_config(ExecOptions(tmp_path, strict_config=True, config=["compaction.auto=false"]))
    assert config.compaction.auto is False
    with pytest.raises(Exception, match="typo"):
        await build_config(ExecOptions(tmp_path, strict_config=True, config=["compaction.typo=false"]))


def test_r09_cold_device_database(tmp_path, monkeypatch):
    from flocks.cli.commands.device import device_app
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(Config, "_global_config", None)
    result = CliRunner(mix_stderr=False).invoke(device_app, ["--format", "json"])
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout) == []


def test_r10_directory_dependencies_installed_and_loaded(tmp_path):
    from flocks.cli.commands.plugin import install_local, PluginKind
    from flocks.tool.tool_loader import _load_provider_config
    import runpy
    source = tmp_path / "src"
    nested = source / "api" / "urlscan"
    nested.mkdir(parents=True)
    (nested / "quotas.yaml").write_text('name: quotas\nhandler:\n  type: http\n  url: /quotas\n')
    (nested / "_provider.yaml").write_text('name: test-provider\nbase_url: https://example.invalid\n')
    (nested / "payload.txt").write_text("resource sentinel")
    (nested / "helper.py").write_text('from pathlib import Path\nVALUE = Path(__file__).with_name("payload.txt").read_text()\n')
    root = tmp_path / "plugins"
    install_local(source, root, PluginKind.tools)
    target = root / "tools" / "api" / "urlscan"
    assert _load_provider_config(target / "quotas.yaml")["base_url"] == "https://example.invalid"
    assert runpy.run_path(str(target / "helper.py"))["VALUE"] == "resource sentinel"
    with pytest.raises(FileExistsError):
        install_local(source, root, PluginKind.tools)


async def test_r02_mcp_protocol_is_error(provider_runtime, monkeypatch):
    from flocks.provider.provider import Provider
    from flocks.cli.mcp_server import create_server
    from mcp.types import CallToolRequest, CallToolRequestParams
    directory, _ = provider_runtime
    class BadProvider:
        _config_models = []
        def is_configured(self): return True
        async def chat_stream(self, **kw):
            raise ValueError("synthetic authentication rejected")
            yield
    monkeypatch.setattr(Provider, "get", lambda _: BadProvider())
    server = create_server(directory)._mcp_server
    result = await server.request_handlers[CallToolRequest](CallToolRequest(params=CallToolRequestParams(name="flocks_exec", arguments={"prompt": "hi"})))
    assert result.root.isError
    assert "synthetic" in result.root.content[0].text


@pytest.mark.parametrize("budget,expected_calls,expected_cost", [(4, 1, 5), (20, 2, 10)])
async def test_r03_usage_survives_retry(provider_runtime, monkeypatch, budget, expected_calls, expected_cost):
    from flocks.provider.provider import Provider, StreamChunk
    from flocks.session.runner import SessionRetry
    directory, tool = provider_runtime
    calls = []
    class RetryProvider:
        _config_models = []
        def is_configured(self): return True
        async def chat_stream(self, **kw):
            calls.append(kw)
            yield StreamChunk(usage={"prompt_tokens": 3, "completion_tokens": 2})
            if len(calls) == 1:
                raise RuntimeError("synthetic retry failure")
            yield StreamChunk(delta="ok", finish_reason="stop")
    monkeypatch.setattr(Provider, "get", lambda _: RetryProvider())
    monkeypatch.setattr(SessionRetry, "retryable", lambda error: "retry" if "synthetic" in str(error) else None)
    monkeypatch.setattr(SessionRetry, "sleep", AsyncMock())
    result = await run_headless(ExecOptions(directory, prompt="hi", controls=RuntimeControls(max_budget=budget)))
    assert result["success"] == (budget == 20), result
    assert len(calls) == expected_calls
    assert result["usage"]["requests"] == expected_calls
    assert result["usage"]["cost_by_currency"]["USD"] == expected_cost
    tool.execute.assert_not_called()


async def test_r05_readonly_does_not_enable_writes(tmp_path, monkeypatch):
    from flocks.tool.file.write import write_tool
    from flocks.tool.registry import ToolContext
    monkeypatch.setattr(Config, "_cached_config", ConfigInfo())
    ctx = ToolContext("s", "m", extra={"sandbox": {"workspace_dir": str(tmp_path), "agent_workspace_dir": str(tmp_path), "workspace_access": "ro"}})
    token = runtime_controls.set(RuntimeControls(sandbox_required=True))
    try:
        result = await write_tool(ctx, "must not write", "new.txt")
        assert not result.success and "read-only" in result.error
        assert not (tmp_path / "new.txt").exists()
    finally:
        runtime_controls.reset(token)


def test_r09_real_masked_device_projection(monkeypatch):
    from flocks.tool.device.store import row_to_device
    import flocks.security
    secret = "SYNTHETIC_DEVICE_SECRET_VALUE"
    monkeypatch.setattr(flocks.security, "get_secret_manager", lambda: SimpleNamespace(get=lambda _: secret))
    row = dict(id="test", group_id="default", name="test", storage_key="", service_id="test", enabled=1, verify_ssl=0,
               fields=json.dumps({"token": "{secret:test-token}"}), status="unknown", message=None, latency_ms=None,
               checked_at=None, created_at=1, updated_at=1)
    result = row_to_device(row)
    assert secret not in result.model_dump_json()
    assert result.fields_set["token"]


async def test_r10_installed_yaml_consumes_provider_and_script(tmp_path, monkeypatch):
    import yaml
    import flocks.tool.tool_loader as loader
    from flocks.cli.commands.plugin import install_local, PluginKind
    from flocks.tool.registry import ToolContext
    source = tmp_path / "source"
    source.mkdir()
    (source / "_provider.yaml").write_text('name: local-provider\ndefaults:\n  base_url: https://example.invalid\n')
    (source / "api.yaml").write_text('name: local-api\nhandler:\n  type: http\n  url: "{base_url}/quotas"\n')
    (source / "script.yaml").write_text('name: local-script\nhandler:\n  type: script\n  script_file: _helper.py\n')
    (source / "_helper.py").write_text('from pathlib import Path\nfrom flocks.tool.registry import ToolResult\nasync def handle(ctx):\n    return ToolResult(success=True, output=Path(__file__).with_name("data.txt").read_text())\n')
    (source / "data.txt").write_text("installed resource")
    (source / "executable.sh").write_text("#!/bin/sh\nprintf ok\n")
    (source / "executable.sh").chmod(0o755)
    root = tmp_path / "plugins"
    install_local(source, root, PluginKind.tools)
    monkeypatch.setattr(loader, "DEFAULT_PLUGIN_ROOT", root)
    target = root / "tools"
    raw = yaml.safe_load((target / "api.yaml").read_text())
    api = loader.yaml_to_tool(raw, target / "api.yaml")
    assert api.info.provider == "local-provider"
    assert raw["handler"]["url"] == "https://example.invalid/quotas"
    script = loader.yaml_to_tool(yaml.safe_load((target / "script.yaml").read_text()), target / "script.yaml")
    result = await script.execute(ToolContext("s", "m"))
    assert result.success and result.output == "installed resource"
    assert (target / "executable.sh").stat().st_mode & 0o111

import json
from unittest.mock import AsyncMock

import pytest
import typer
from typer.testing import CliRunner

import flocks.cli.commands.exec as command
from flocks.cli.headless import ExecOptions, apply_overrides, build_config, load_images, load_schema, run_headless
from flocks.config.config import Config, ConfigInfo
from flocks.config.runtime import runtime_config
from flocks.session.runtime_controls import RuntimeControls, runtime_controls

app = typer.Typer()
app.command("exec")(command.exec_command)
runner = CliRunner(mix_stderr=False)


@pytest.mark.parametrize("format", ["text", "json", "stream-json"])
def test_cli_stdin_output_and_clean_stdout(monkeypatch, tmp_path, format):
    seen = []
    async def run(options, emit):
        seen.append(options)
        print("library diagnostic")
        emit({"type": "text_delta", "delta": "你好 [x]"})
        return {"type": "result", "success": True, "session_id": "s1", "result": "你好 [x]", "error": None}
    monkeypatch.setattr(command, "run_headless", run)
    output = tmp_path / "last.txt"
    result = runner.invoke(app, ["-", "-C", str(tmp_path), "--output-format", format, "-o", str(output)], input="stdin prompt")
    assert result.exit_code == 0, result.exception
    assert seen[0].prompt == "stdin prompt"
    assert seen[0].directory == tmp_path
    assert "library diagnostic" not in result.stdout
    assert "library diagnostic" in result.stderr
    assert output.read_text() == "你好 [x]"
    if format != "text":
        records = [json.loads(line) for line in result.stdout.splitlines()]
        assert records[-1]["success"] is True
        assert len(records) == (2 if format == "stream-json" else 1)


@pytest.mark.parametrize("args", [[""], ["-"], ["x", "--last", "--session", "s"], ["x", "--max-budget", "-1"], ["x", "--max-budget", "nan"], ["x", "--output-format", "xml"]])
def test_invalid_input_never_executes(monkeypatch, args):
    mock = AsyncMock()
    monkeypatch.setattr(command, "run_headless", mock)
    result = runner.invoke(app, args, input="")
    assert result.exit_code == 2
    mock.assert_not_called()


def test_failure_exit_and_result(monkeypatch):
    monkeypatch.setattr(command, "run_headless", AsyncMock(return_value={"type": "result", "success": False, "session_id": None, "result": "", "error": "provider failed"}))
    result = runner.invoke(app, ["x", "--output-format", "json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == "provider failed"


def test_all_options_are_forwarded(monkeypatch, tmp_path):
    seen = []
    async def run(options, emit):
        seen.append(options)
        return {"success": True, "session_id": "s", "result": "ok", "error": None}
    monkeypatch.setattr(command, "run_headless", run)
    result = runner.invoke(app, ["prompt", "-m", "test/model", "--agent", "rex", "--session", "s", "--permission-mode", "acceptEdits", "--allowed-tools", "read,edit", "--allowed-tools", "foo*", "--disallowed-tools", "foo_bad", "--sandbox", "workspace-write", "-c", "model=test/model", "--strict-config", "--max-budget", "0.2"])
    assert result.exit_code == 0, result.exception
    options = seen[0]
    assert options.controls.allowed_tools == ("read", "edit", "foo*")
    assert options.controls.disallowed_tools == ("foo_bad",)
    assert options.controls.max_budget == 0.2
    assert options.sandbox == "workspace-write"
    assert options.strict_config
    assert options.session_id == "s"


def test_config_overrides():
    original = {"provider": {"a": {"options": {"timeout": 2}}}}
    result = apply_overrides(original, ["provider.a.options.timeout=10", "compaction.auto=false", "model=test/m", 'instructions=["a","b"]'])
    assert result["provider"]["a"]["options"]["timeout"] == 10
    assert result["compaction"]["auto"] is False
    assert result["instructions"] == ["a", "b"]
    assert original["provider"]["a"]["options"]["timeout"] == 2
    with pytest.raises(ValueError):
        apply_overrides({}, ["a..b=1"])
    with pytest.raises(ValueError):
        apply_overrides({"a": 1}, ["a.b=2"])


async def test_strict_config_and_runtime_isolation(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "_cached_config", ConfigInfo(model="old/model"))
    options = ExecOptions(tmp_path, config=["model=new/model", "server.port=9000"], strict_config=True)
    config = await build_config(options)
    assert config.model == "new/model"
    token = runtime_config.set(config)
    try:
        assert (await Config.get()).server.port == 9000
    finally:
        runtime_config.reset(token)
    assert (await Config.get()).model == "old/model"
    options.config = ["server.typo=3"]
    with pytest.raises(Exception, match="typo"):
        await build_config(options)


def test_schema_and_images(tmp_path):
    from PIL import Image
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object","required":["answer"]}')
    assert load_schema(schema)["type"] == "object"
    schema.write_text('{"$ref":"https://invalid/schema"}')
    with pytest.raises(ValueError, match="local"):
        load_schema(schema)
    path = tmp_path / "image.png"
    Image.new("RGB", (2, 2), "red").save(path)
    images = load_images([path])
    assert images[0]["url"].startswith("data:image/png;base64,")
    path.write_text("not an image")
    with pytest.raises(Exception):
        load_images([path])


@pytest.fixture
async def runtime(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from flocks.agent.agent import AgentInfo
    from flocks.agent.registry import Agent
    from flocks.mcp import MCP
    from flocks.project.project import Project
    from flocks.provider.provider import Provider
    from flocks.session.session import Session
    from flocks.storage.storage import Storage
    from flocks.tool.registry import ToolRegistry

    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(Config, "_global_config", None)
    monkeypatch.setattr(Config, "_cached_config", ConfigInfo(model="test/model", compaction={"auto": False}))
    monkeypatch.setattr(Project, "from_directory", AsyncMock(return_value={"project": SimpleNamespace(id="default"), "sandbox": str(tmp_path)}))
    monkeypatch.setattr(Agent, "get", AsyncMock(return_value=AgentInfo(name="rex", mode="primary", native=True)))
    monkeypatch.setattr(Agent, "default_agent", AsyncMock(return_value="rex"))
    monkeypatch.setattr(Provider, "init", AsyncMock())
    monkeypatch.setattr(Provider, "apply_config", AsyncMock())
    monkeypatch.setattr(Provider, "get", lambda _: SimpleNamespace(is_configured=lambda: True))
    monkeypatch.setattr(ToolRegistry, "init", lambda: None)
    monkeypatch.setattr(MCP, "init", AsyncMock())
    monkeypatch.setattr(MCP, "shutdown", AsyncMock())
    await Storage.init(tmp_path / "state.db")
    Session.invalidate_cache()
    yield tmp_path
    await Storage.shutdown()
    Session.invalidate_cache()


async def test_headless_persists_prompt_image_final_schema_and_resumes(runtime, monkeypatch):
    from flocks.session.message import Message, MessageRole
    from flocks.session.session_loop import SessionLoop, LoopResult
    from flocks.session.session import Session
    observed = []
    async def loop(**kwargs):
        sid = kwargs["session_id"]
        observed.append(await Message.list_with_parts(sid))
        callbacks = kwargs["callbacks"]
        await callbacks.on_step_start(1)
        await callbacks.runner_callbacks.on_text_delta('{"answer":42}')
        await callbacks.on_step_end(1)
        msg = await Message.create(sid, MessageRole.ASSISTANT, '{"answer":42}')
        return LoopResult(action="stop", last_message=msg)
    monkeypatch.setattr(SessionLoop, "run", loop)
    options = ExecOptions(runtime, prompt="hi", schema={"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"]}, images=[{"mime": "image/png", "url": "data:image/png;base64,AA==", "filename": "test.png"}])
    events = []
    result = await run_headless(options, events.append)
    assert result["success"], result
    assert result["structured_output"] == {"answer": 42}
    assert any(p.type == "file" for p in observed[0][0].parts)
    assert "JSON Schema" in observed[0][0].parts[0].text
    assert [e["type"] for e in events] == ["session", "step_start", "text_delta", "step_end"]
    sid = result["session_id"]
    assert (await Session.get_by_id(sid)).directory == str(runtime)
    continued = await run_headless(ExecOptions(runtime, prompt="next", session_id=sid))
    assert continued["session_id"] == sid
    assert len(observed[1]) == 3
    assert runtime_controls.get() is None
    assert runtime_config.get() is None


async def test_schema_failure_is_not_success(runtime, monkeypatch):
    from flocks.session.message import Message, MessageRole
    from flocks.session.session_loop import SessionLoop, LoopResult
    async def loop(**kw):
        msg = await Message.create(kw["session_id"], MessageRole.ASSISTANT, '{"answer":"wrong"}')
        return LoopResult(action="stop", last_message=msg)
    monkeypatch.setattr(SessionLoop, "run", loop)
    result = await run_headless(ExecOptions(runtime, prompt="hi", schema={"type": "object", "properties": {"answer": {"type": "integer"}}}))
    assert not result["success"]
    assert "integer" in result["error"]


async def test_session_last_scope_and_missing_id(runtime):
    from flocks.cli.headless import resolve_session
    from flocks.session.session import Session
    first = await resolve_session(ExecOptions(runtime, create_only=True))
    other_dir = runtime / "other"
    other_dir.mkdir()
    await Session.create(project_id="default", directory=str(other_dir), agent="rex")
    last = await resolve_session(ExecOptions(runtime, last=True))
    assert last.id == first.id
    with pytest.raises(ValueError, match="not found"):
        await resolve_session(ExecOptions(runtime, session_id="missing"))
    await Session.archive(first.project_id, first.id)
    with pytest.raises(ValueError, match="archived"):
        await resolve_session(ExecOptions(runtime, session_id=first.id))


async def test_zero_budget_prevents_creation(runtime, monkeypatch):
    from flocks.session.session import Session
    create = AsyncMock()
    monkeypatch.setattr(Session, "create", create)
    result = await run_headless(ExecOptions(runtime, prompt="hi", controls=RuntimeControls(max_budget=0)))
    assert not result["success"]
    assert "budget" in result["error"]
    create.assert_not_called()


async def test_sandbox_failure_never_starts_loop(runtime, monkeypatch):
    from flocks.session.session_loop import SessionLoop
    import flocks.sandbox.context as sandbox
    monkeypatch.setattr(sandbox, "resolve_sandbox_context", AsyncMock(side_effect=RuntimeError("Docker unavailable")))
    loop = AsyncMock()
    monkeypatch.setattr(SessionLoop, "run", loop)
    result = await run_headless(ExecOptions(runtime, prompt="hi", sandbox="on"))
    assert not result["success"]
    assert "Docker unavailable" in result["error"]
    loop.assert_not_called()


@pytest.mark.parametrize("scenario", ["text", "tools", "denied", "budget", "schema", "image"])
async def test_real_session_loop_with_streaming_provider(runtime, monkeypatch, scenario):
    """Exercise actual SessionLoop -> SessionRunner -> StreamProcessor -> storage."""
    from types import SimpleNamespace
    from flocks.provider.provider import Provider, StreamChunk
    from flocks.session.runner import SessionRunner
    from flocks.session.prompt import SessionPrompt
    from flocks.memory.bootstrap import MemoryBootstrap
    from flocks.hooks.pipeline import HookPipeline
    import flocks.provider.options as provider_options
    from flocks.tool.registry import ToolRegistry, ToolInfo, ToolResult
    from flocks.provider.types import PriceConfig
    tool = SimpleNamespace(info=ToolInfo(name="read", description="test read", source="builtin"),
                           execute=AsyncMock(return_value=ToolResult(success=True, output="tool-value")))
    monkeypatch.setattr(ToolRegistry, "get", lambda name: tool if name == "read" else None)
    monkeypatch.setattr(ToolRegistry, "_failure_auto_disable_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(HookPipeline, "run_tool_before", AsyncMock(return_value=None))
    monkeypatch.setattr(HookPipeline, "run_tool_after", AsyncMock(return_value=None))
    calls = []
    class FakeProvider:
        _config_models = []
        def is_configured(self):
            return True
        async def chat_stream(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1 and scenario in {"tools", "denied", "budget"}:
                yield StreamChunk(tool_calls=[{"index": 0, "id": "call_1", "type": "function", "function": {"name": "read", "arguments": "{}"}}])
                yield StreamChunk(finish_reason="tool_calls", usage={"prompt_tokens": 3, "completion_tokens": 2})
            elif scenario == "schema":
                yield StreamChunk(delta='{"answer":42}', finish_reason="stop", usage={"prompt_tokens": 3, "completion_tokens": 2})
            else:
                yield StreamChunk(delta="actual ")
                yield StreamChunk(delta="engine", finish_reason="stop", usage={"prompt_tokens": 3, "completion_tokens": 2})
    monkeypatch.setattr(Provider, "get", lambda _: FakeProvider())
    monkeypatch.setattr(Provider, "get_model", lambda _: None)
    monkeypatch.setattr(Provider, "resolve_model", lambda *_: None)
    monkeypatch.setattr(SessionRunner, "_build_callable_tool_schema", AsyncMock(return_value=[]))
    monkeypatch.setattr(SessionRunner, "_build_turn_prompt_context", AsyncMock(return_value=None))
    monkeypatch.setattr(SessionPrompt, "build_system_prompt_blocks", AsyncMock(return_value=[]))
    monkeypatch.setattr(MemoryBootstrap, "bootstrap", AsyncMock(return_value={}))
    monkeypatch.setattr(HookPipeline, "has_stage_handlers", AsyncMock(return_value=False))
    monkeypatch.setattr(HookPipeline, "run_session_start", AsyncMock())
    monkeypatch.setattr(provider_options, "build_provider_options", lambda *_: {})
    events = []
    options = ExecOptions(runtime, prompt="hello")
    if scenario == "denied":
        options.controls.disallowed_tools = ("read",)
    if scenario == "budget":
        import flocks.provider.usage_service as usage
        monkeypatch.setattr(usage, "resolve_usage_pricing", lambda *_: PriceConfig(input=1, output=1, unit=1, currency="USD"))
        options.controls.max_budget = 0.5
    if scenario == "schema":
        options.schema = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "integer"}}}
    if scenario == "image":
        from PIL import Image
        path = runtime / "sample.png"
        Image.new("RGB", (2, 2)).save(path)
        options.images = load_images([path])
    result = await run_headless(options, events.append)
    if scenario == "budget":
        assert not result["success"]
        assert "budget" in result["error"]
        assert len(calls) == 1
        assert result["usage"]["cost_by_currency"]["USD"] == 5
    else:
        assert result["success"], result
        assert result["result"] == ('{"answer":42}' if scenario == "schema" else "actual engine")
        assert len(calls) == (2 if scenario in {"tools", "denied"} else 1)
        assert result["usage"]["input_tokens"] == 3 * len(calls)
        assert any(event["type"] == "text_delta" for event in events)
    if scenario == "tools":
        tool.execute.assert_awaited_once()
        assert any(e["type"] == "tool_end" and e["output"] == "tool-value" for e in events)
    if scenario == "denied":
        tool.execute.assert_not_called()
        assert any(e["type"] == "tool_end" and not e["success"] for e in events)
    if scenario == "image":
        assert any(isinstance(m.content, list) and any(b.get("type") == "image" and b.get("mimeType") == "image/png" and b.get("data") for b in m.content) for m in calls[0]["messages"])



async def test_runtime_raw_config_reads_and_write_guard(monkeypatch, tmp_path):
    from flocks.config.config_writer import ConfigWriter
    config = ConfigInfo.model_validate({"default_models": {"llm": {"provider_id": "p", "model_id": "m"}}})
    token = runtime_config.set(config)
    try:
        assert ConfigWriter.get_all_default_models()["llm"]["model_id"] == "m"
        with pytest.raises(RuntimeError, match="writes are disabled"):
            ConfigWriter._write_raw({}, tmp_path / "must-not-exist.json")
        assert not (tmp_path / "must-not-exist.json").exists()
    finally:
        runtime_config.reset(token)


async def test_config_field_names_override_existing_json_aliases(monkeypatch, tmp_path):
    original = ConfigInfo(defaultAgent="old", provider={"p": {"options": {"baseURL": "old"}}})
    monkeypatch.setattr(Config, "_cached_config", original)
    value = await build_config(ExecOptions(tmp_path, config=["default_agent=new", "provider.p.options.base_url=https://example.invalid"]))
    assert value.default_agent == "new"
    assert value.provider["p"].options.base_url == "https://example.invalid"
    assert original.default_agent == "old"

"""Regressions for removing CLI behavior outside the original A-class contract."""
import asyncio
import json
from pathlib import Path

import typer
from PIL import Image
from typer.testing import CliRunner

from flocks.cli.commands.exec import OutputFormat, invoke_headless
from flocks.cli.headless import ExecOptions, load_images, run_headless
from flocks.provider.provider import Provider, StreamChunk
from tests.cli.test_exec_command import runtime  # noqa: F401
from tests.cli.test_review_fixes import provider_runtime  # noqa: F401


async def test_stream_json_only_emits_the_five_specified_event_types(provider_runtime, monkeypatch):
    directory, tool = provider_runtime

    class ControlledProvider:
        _config_models = []
        requests = 0

        def is_configured(self):
            return True

        async def chat_stream(self, **kwargs):
            self.requests += 1
            if self.requests == 1:
                yield StreamChunk(tool_calls=[{
                    "index": 0, "id": "read_call", "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }], finish_reason="tool_calls")
            else:
                yield StreamChunk(delta="ok", finish_reason="stop")

    provider = ControlledProvider()
    monkeypatch.setattr(Provider, "get", lambda _: provider)
    app = typer.Typer()

    @app.command()
    def call():
        invoke_headless(ExecOptions(directory, prompt="hello"), OutputFormat.stream_json)

    result = await asyncio.to_thread(CliRunner(mix_stderr=False).invoke, app, [])
    assert result.exit_code == 0, result.exception
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert {record["type"] for record in records} == {
        "session", "text_delta", "tool_start", "tool_end", "result",
    }
    assert records[-1]["type"] == "result" and records[-1]["success"]
    assert records[-1]["result"] == "ok"
    assert provider.requests == 2
    tool.execute.assert_awaited_once()


async def test_protocol_cleanup_preserves_schema_image_and_resume(provider_runtime, monkeypatch):
    from flocks.config.runtime import runtime_config
    from flocks.session.message import Message
    from flocks.session.runtime_controls import runtime_controls

    directory, _ = provider_runtime
    image = directory / "input.png"
    Image.new("RGB", (2, 2), "red").save(image)
    attachments = load_images([image])
    calls = []

    class ControlledProvider:
        _config_models = []

        def is_configured(self):
            return True

        async def chat_stream(self, **kwargs):
            calls.append(kwargs)
            yield StreamChunk(delta='{"answer":42}', finish_reason="stop")

    monkeypatch.setattr(Provider, "get", lambda _: ControlledProvider())
    schema = {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"]}
    events = []
    first = await run_headless(ExecOptions(directory, prompt="hello", schema=schema, images=attachments), events.append)
    assert first["success"], first
    assert first["structured_output"] == {"answer": 42}
    second = await run_headless(ExecOptions(directory, prompt="next", schema=schema, session_id=first["session_id"]), events.append)
    assert second["success"], second
    assert second["session_id"] == first["session_id"]
    assert second["structured_output"] == {"answer": 42}
    assert len(calls) == 2
    messages = await Message.list_with_parts(first["session_id"])
    assert len(messages) == 4
    assert any(part.type == "file" and part.url == attachments[0]["url"] for part in messages[0].parts)
    assert any(part.type == "text" and "JSON Schema" in part.text for part in messages[0].parts)
    assert {event["type"] for event in events} == {"session", "text_delta"}
    assert runtime_config.get() is None
    assert runtime_controls.get() is None


async def test_compaction_callback_does_not_add_a_protocol_event(runtime, monkeypatch):
    from flocks.session.message import Message, MessageRole
    from flocks.session.session_loop import LoopResult, SessionLoop

    async def loop(**kwargs):
        await kwargs["callbacks"].on_compaction()
        message = await Message.create(kwargs["session_id"], MessageRole.ASSISTANT, "ok")
        return LoopResult(action="stop", last_message=message)

    monkeypatch.setattr(SessionLoop, "run", loop)
    events = []
    result = await run_headless(ExecOptions(runtime, prompt="hello"), events.append)
    assert result["success"], result
    assert [event["type"] for event in events] == ["session"]


async def test_budgeted_compaction_is_still_rejected(runtime, monkeypatch):
    from flocks.session.runtime_controls import RuntimeControls
    from flocks.session.session_loop import SessionLoop
    from flocks.provider.types import PriceConfig
    import flocks.provider.usage_service as usage

    async def loop(**kwargs):
        await kwargs["callbacks"].on_compaction()
        raise AssertionError("budgeted compaction must stop")

    monkeypatch.setattr(SessionLoop, "run", loop)
    monkeypatch.setattr(usage, "resolve_usage_pricing", lambda *_: PriceConfig(input=1, output=1))
    result = await run_headless(ExecOptions(runtime, prompt="hello", controls=RuntimeControls(max_budget=1)))
    assert not result["success"]
    assert "Compaction requires an auxiliary model call" in result["error"]


def test_valid_png_is_not_rejected_by_an_unspecified_size_limit(tmp_path):
    path = tmp_path / "large.png"
    Image.new("RGB", (2700, 2700), "red").save(path, compress_level=0)
    assert path.stat().st_size > 20 * 1024 * 1024
    images = load_images([path])
    assert len(images) == 1
    assert images[0]["mime"] == "image/png"
    assert images[0]["url"].startswith("data:image/png;base64,")


def test_plugin_install_keeps_dependencies_but_not_bytecode(tmp_path):
    from flocks.cli.commands.plugin import PluginKind, install_local

    source = tmp_path / "source"
    source.mkdir()
    (source / "tool.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "_provider.yaml").write_text("name: synthetic\n", encoding="utf-8")
    (source / "data.txt").write_text("required resource", encoding="utf-8")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "tool.cpython-312.pyc").write_bytes(b"generated bytecode")
    (source / "tool.pyc").write_bytes(b"old generated bytecode")
    root = tmp_path / "plugins"

    installed = install_local(source, root, PluginKind.tools)

    assert {path.relative_to(root / "tools").as_posix() for path in installed} == {
        "tool.py", "_provider.yaml", "data.txt",
    }
    assert (root / "tools" / "data.txt").read_text() == "required resource"
    assert not (root / "tools" / "__pycache__").exists()
    assert not (root / "tools" / "tool.pyc").exists()


def test_plugin_install_traverses_source_tree_once(tmp_path, monkeypatch):
    from flocks.cli.commands.plugin import PluginKind, install_local

    source = tmp_path / "source"
    source.mkdir()
    (source / "tool.py").write_text("VALUE = 1\n", encoding="utf-8")
    original = Path.rglob
    calls = []

    def tracked_rglob(path, pattern, *args, **kwargs):
        if path == source:
            calls.append(pattern)
        return original(path, pattern, *args, **kwargs)

    monkeypatch.setattr(Path, "rglob", tracked_rglob)
    install_local(source, tmp_path / "plugins", PluginKind.tools)
    assert calls == ["*"]

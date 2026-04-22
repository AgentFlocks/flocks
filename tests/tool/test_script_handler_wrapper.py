"""Tests for ``_build_script_handler`` keyword adaptation.

The wrapper has to support two legacy handler conventions:

* Modern handlers declare explicit kwargs (``async def handle(ctx, *, foo)``).
* Legacy handlers (e.g. the Sangfor XDR plugin) accept only ``ctx`` and read
  parameters from ``ctx.params``.

Without adaptation, the test-credentials flow (which calls
``ToolRegistry.execute(tool_name, **params)`` with a default ``ToolContext``)
either crashes with ``TypeError: got an unexpected keyword argument`` or with
``AttributeError: 'ToolContext' object has no attribute 'params'``.

See ``flocks.tool.tool_loader._build_script_handler`` for the implementation.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from flocks.tool.registry import ToolContext, ToolResult
from flocks.tool.tool_loader import _build_script_handler


def _make_context() -> ToolContext:
    return ToolContext(session_id="t", message_id="t")


def _write_script(tmp_path: Path, source: str) -> Path:
    plugins_root = tmp_path / ".flocks" / "plugins" / "tools" / "api" / "demo"
    plugins_root.mkdir(parents=True)
    script = plugins_root / "demo.handler.py"
    script.write_text(textwrap.dedent(source))
    yaml_stub = plugins_root / "demo.yaml"
    yaml_stub.touch()
    return yaml_stub


@pytest.mark.asyncio
async def test_script_handler_filters_unexpected_kwargs(tmp_path, monkeypatch):
    """Legacy ``run(ctx)`` handler must not receive extra kwargs."""

    monkeypatch.chdir(tmp_path)
    yaml_stub = _write_script(
        tmp_path,
        """
        async def run(ctx):
            return {"action": ctx.params.get("action"), "extra": ctx.params.get("extra")}
        """,
    )
    handler = _build_script_handler(
        {"script_file": "demo.handler.py", "function": "run"},
        yaml_stub,
    )

    result = await handler(_make_context(), action="list", extra=42)

    assert isinstance(result, ToolResult) and result.success
    assert result.output == {"action": "list", "extra": 42}


@pytest.mark.asyncio
async def test_script_handler_passes_declared_kwargs(tmp_path, monkeypatch):
    """Modern handlers that declare explicit kwargs still receive them."""

    monkeypatch.chdir(tmp_path)
    yaml_stub = _write_script(
        tmp_path,
        """
        async def run(ctx, foo: str = "", bar: int = 0):
            return {"foo": foo, "bar": bar}
        """,
    )
    handler = _build_script_handler(
        {"script_file": "demo.handler.py", "function": "run"},
        yaml_stub,
    )

    result = await handler(_make_context(), foo="hello", bar=7, extra="ignored")

    assert isinstance(result, ToolResult) and result.success
    assert result.output == {"foo": "hello", "bar": 7}


@pytest.mark.asyncio
async def test_script_handler_var_kwargs(tmp_path, monkeypatch):
    """Handlers declaring ``**kwargs`` still receive every argument."""

    monkeypatch.chdir(tmp_path)
    yaml_stub = _write_script(
        tmp_path,
        """
        async def run(ctx, **kwargs):
            return dict(kwargs)
        """,
    )
    handler = _build_script_handler(
        {"script_file": "demo.handler.py", "function": "run"},
        yaml_stub,
    )

    result = await handler(_make_context(), a=1, b="x")

    assert isinstance(result, ToolResult) and result.success
    assert result.output == {"a": 1, "b": "x"}


@pytest.mark.asyncio
async def test_script_handler_injects_ctx_params(tmp_path, monkeypatch):
    """``ctx.params`` is populated even when the context lacks the attribute."""

    monkeypatch.chdir(tmp_path)
    yaml_stub = _write_script(
        tmp_path,
        """
        async def run(ctx):
            return {"params": dict(ctx.params)}
        """,
    )
    handler = _build_script_handler(
        {"script_file": "demo.handler.py", "function": "run"},
        yaml_stub,
    )

    ctx = _make_context()
    assert not hasattr(ctx, "params")

    result = await handler(ctx, action="list", uuid="abc")

    assert isinstance(result, ToolResult) and result.success
    assert result.output == {"params": {"action": "list", "uuid": "abc"}}
    assert ctx.params == {"action": "list", "uuid": "abc"}

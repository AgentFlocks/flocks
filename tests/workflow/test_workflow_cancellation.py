"""Regression tests for workflow cancellation semantics."""

from __future__ import annotations

import asyncio
import threading
import time

from flocks.tool.registry import Tool, ToolCategory, ToolInfo, ToolRegistry, ToolResult
from flocks.workflow.runner import run_workflow


def test_run_workflow_cancels_during_python_node() -> None:
    """A UI stop request should interrupt a running Python node."""
    cancel_event = threading.Event()
    workflow = {
        "name": "cancel_running_python_node",
        "start": "slow",
        "nodes": [
            {
                "id": "slow",
                "type": "python",
                "code": (
                    "import time\n"
                    "for _ in range(500):\n"
                    "    time.sleep(0.01)\n"
                    "outputs['done'] = True\n"
                ),
            }
        ],
        "edges": [],
    }

    timer = threading.Timer(0.05, cancel_event.set)
    timer.start()
    try:
        result = run_workflow(
            workflow=workflow,
            inputs={},
            ensure_requirements=False,
            node_timeout_s=2,
            cancel=cancel_event.is_set,
        )
    finally:
        timer.cancel()

    assert result.status == "CANCELLED"
    assert result.outputs == {}


def test_run_workflow_final_status_honors_late_cancellation() -> None:
    """If cancellation is requested during the last node, success is not written."""
    cancel_event = threading.Event()
    workflow = {
        "name": "cancel_last_node",
        "start": "slow",
        "nodes": [
            {
                "id": "slow",
                "type": "python",
                "code": (
                    "import time\n"
                    "time.sleep(0.1)\n"
                    "outputs['done'] = True\n"
                ),
            }
        ],
        "edges": [],
    }

    timer = threading.Timer(0.03, cancel_event.set)
    timer.start()
    try:
        result = run_workflow(
            workflow=workflow,
            inputs={},
            ensure_requirements=False,
            node_timeout_s=2,
            cancel=cancel_event.is_set,
        )
    finally:
        timer.cancel()

    assert result.status == "CANCELLED"


def test_run_workflow_stops_after_cancel_signal() -> None:
    """Cancellation should stop the workflow before the next node runs."""
    workflow = {
        "name": "cancel-test-workflow",
        "start": "step1",
        "nodes": [
            {
                "id": "step1",
                "type": "python",
                "code": "outputs['value'] = 1",
            },
            {
                "id": "step2",
                "type": "python",
                "code": "outputs['value'] = inputs['value'] + 1",
            },
        ],
        "edges": [
            {"from": "step1", "to": "step2"},
        ],
    }
    cancel_event = threading.Event()

    def on_step_complete(_step_result) -> None:
        cancel_event.set()

    result = run_workflow(
        workflow=workflow,
        inputs={},
        ensure_requirements=False,
        trace=False,
        on_step_complete=on_step_complete,
        cancel=cancel_event.is_set,
        retain_history=True,
    )

    assert result.status == "CANCELLED"
    assert len(result.history) == 1
    assert result.history[0]["node_id"] == "step1"
    assert result.outputs == {"value": 1}
    assert result.error is not None


def test_run_workflow_cancels_native_tool_node() -> None:
    """Native type=tool nodes should use the same cancellation path as tool.run()."""
    tool_name = "test_workflow_cancel_sleep_tool"
    cancel_event = threading.Event()

    async def _sleep_tool(ctx) -> ToolResult:
        _ = ctx
        await asyncio.sleep(5)
        return ToolResult(success=True, output="done")

    ToolRegistry.register(
        Tool(
            info=ToolInfo(
                name=tool_name,
                description="Sleep until cancelled",
                category=ToolCategory.CUSTOM,
                parameters=[],
                enabled=True,
                native=True,
            ),
            handler=_sleep_tool,
        )
    )
    workflow = {
        "name": "cancel_native_tool_node",
        "start": "slow_tool",
        "nodes": [
            {
                "id": "slow_tool",
                "type": "tool",
                "tool_name": tool_name,
            }
        ],
        "edges": [],
    }

    timer = threading.Timer(0.05, cancel_event.set)
    started = time.perf_counter()
    timer.start()
    try:
        result = run_workflow(
            workflow=workflow,
            inputs={},
            ensure_requirements=False,
            node_timeout_s=10,
            cancel=cancel_event.is_set,
        )
    finally:
        timer.cancel()
        ToolRegistry.unregister(tool_name)

    assert result.status == "CANCELLED"
    assert time.perf_counter() - started < 1.0


def test_run_workflow_cancels_python_node_llm_ask(monkeypatch) -> None:
    """Python node llm.ask() should use the same cooperative cancellation path."""
    from flocks.provider import provider as provider_mod
    from flocks.workflow import llm as workflow_llm_mod

    class _FakeResponse:
        def __init__(self, content: str):
            self.content = content

    class _FakeModel:
        def __init__(self, model_id: str):
            self.id = model_id

    class _SlowProvider:
        id = "demo"

        def configure(self, _cfg):
            return None

        def is_configured(self):
            return True

        async def chat(self, model_id: str, messages, **kwargs):
            del model_id, messages, kwargs
            await asyncio.sleep(5)
            return _FakeResponse("late")

    provider = _SlowProvider()

    monkeypatch.setattr(provider_mod.Provider, "_ensure_initialized", lambda: None)

    async def _noop_apply_config(*_args, **_kwargs):
        return None

    monkeypatch.setattr(provider_mod.Provider, "apply_config", _noop_apply_config)
    monkeypatch.setattr(provider_mod.Provider, "get", lambda pid: provider if pid == "demo" else None)
    monkeypatch.setattr(
        provider_mod.Provider,
        "list_models",
        lambda provider_id=None: [_FakeModel("m")] if provider_id == "demo" else [],
    )

    async def _noop_config_get():
        class _Cfg:
            model = None

            def model_dump(self, **kwargs):
                del kwargs
                return {}

        return _Cfg()

    async def _resolve_default_llm():
        return {"provider_id": "demo", "model_id": "m"}

    monkeypatch.setattr(workflow_llm_mod.Config, "get", _noop_config_get)
    monkeypatch.setattr(workflow_llm_mod.Config, "resolve_default_llm", _resolve_default_llm)

    workflow = {
        "name": "cancel_python_llm_ask",
        "start": "slow",
        "nodes": [
            {
                "id": "slow",
                "type": "python",
                "code": "outputs['answer'] = llm.ask('hello')",
            }
        ],
        "edges": [],
    }

    cancel_event = threading.Event()
    timer = threading.Timer(0.05, cancel_event.set)
    started = time.perf_counter()
    timer.start()
    try:
        result = run_workflow(
            workflow=workflow,
            inputs={},
            ensure_requirements=False,
            node_timeout_s=10,
            cancel=cancel_event.is_set,
        )
    finally:
        timer.cancel()

    assert result.status == "CANCELLED"
    assert result.outputs == {}
    assert time.perf_counter() - started < 1.0

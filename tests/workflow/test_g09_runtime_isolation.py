"""Workflow code runs where the configuration says, and only there (V1.2 3.6, G09).

``sandbox.mode`` on means the sandbox: when there is none, the code is refused,
not run on the host with the server's own reach.  Running one node on its own
follows the same rule, and its code is checked under the caller's session.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from flocks.tool import ToolContext
from flocks.workflow import runner as runner_module
from flocks.workflow.runner import WorkflowSandboxUnavailable, run_workflow, workflow_python_runtime


def _workflow(marker: Path) -> dict:
    return {
        "id": "g09",
        "name": "g09",
        "start": "start",
        "nodes": [
            {
                "id": "start",
                "type": "python",
                "code": f"open({str(marker)!r}, 'w').write('ran')\noutputs['ok'] = True\n",
            }
        ],
        "edges": [],
    }


@pytest.fixture
def sandbox_required_but_missing(monkeypatch):
    monkeypatch.setattr(runner_module, "_resolve_workflow_runtime_preference", lambda _ctx: "sandbox")
    monkeypatch.setattr(runner_module, "_resolve_sandbox_payload_from_config", lambda _ctx: None)


def test_a_run_that_requires_the_sandbox_is_refused_without_one(tmp_path, sandbox_required_but_missing):
    marker = tmp_path / "ran.txt"

    with pytest.raises(WorkflowSandboxUnavailable, match="not run on the host"):
        run_workflow(workflow=_workflow(marker), ensure_requirements=False)

    assert not marker.exists(), "nothing of it ran on the host"


def test_one_node_on_its_own_is_refused_the_same_way(sandbox_required_but_missing):
    with pytest.raises(WorkflowSandboxUnavailable):
        workflow_python_runtime(ToolContext(session_id="ses_1", message_id="msg_1"))


def test_a_sandbox_that_is_there_is_used(monkeypatch):
    monkeypatch.setattr(runner_module, "_resolve_workflow_runtime_preference", lambda _ctx: "sandbox")
    payload = {"container_name": "flocks-sbx", "workspace_dir": "/w", "container_workdir": "/w"}
    monkeypatch.setattr(runner_module, "_resolve_sandbox_payload_from_config", lambda _ctx: payload)

    runtime = workflow_python_runtime(ToolContext(session_id="ses_1", message_id="msg_1"))

    assert isinstance(runtime, runner_module.SandboxPythonExecRuntime)


def test_host_mode_runs_on_the_host_with_the_callers_context(monkeypatch):
    monkeypatch.setattr(runner_module, "_resolve_workflow_runtime_preference", lambda _ctx: "host")
    ctx = ToolContext(session_id="ses_caller", message_id="msg_caller")

    runtime = workflow_python_runtime(ctx)

    assert isinstance(runtime, runner_module.PythonExecRuntime)
    resolver = getattr(runtime.tool_registry, "_tool_context", None)
    assert callable(resolver) and resolver().session_id == "ses_caller", (
        "the checkpoint names the operation after the calling session, not 'workflow'"
    )


def test_the_run_node_tool_checks_the_code_under_the_calling_session(tmp_path, monkeypatch):
    from flocks.tool.task import run_workflow_node as tool_module

    monkeypatch.setattr(runner_module, "_resolve_workflow_runtime_preference", lambda _ctx: "host")
    seen = {}

    def gate(self, node, **kwargs):
        registry = getattr(kwargs.get("runtime") or self.runtime, "tool_registry", None)
        seen["session"] = registry._tool_context().session_id
        return None  # no handlers: run as is

    from flocks.workflow.engine import WorkflowEngine

    monkeypatch.setattr(WorkflowEngine, "_gate_engine_operation", gate)
    marker = tmp_path / "ran.txt"
    ctx = ToolContext(session_id="ses_agent", message_id="msg_agent")

    result = tool_module._run_node_sync(_workflow(marker), "start", {}, ctx)

    assert result["success"] is True and marker.exists()
    assert seen["session"] == "ses_agent"


@pytest.mark.asyncio
async def test_the_run_node_route_refuses_when_the_sandbox_is_required_and_missing(
    tmp_path, monkeypatch, sandbox_required_but_missing
):
    from fastapi import HTTPException

    from flocks.server.routes import workflow as routes

    marker = tmp_path / "ran.txt"
    monkeypatch.setattr(routes, "_read_workflow_from_fs", lambda _id: {"workflowJson": _workflow(marker)})

    async def build_context(**kwargs):
        return ToolContext(session_id="ses_ui", message_id="msg_ui")

    monkeypatch.setattr(routes, "_build_workflow_tool_context", build_context)

    with pytest.raises(HTTPException) as refused:
        await routes.run_single_node("g09", routes.RunNodeRequest(node_id="start", inputs={}))

    assert refused.value.status_code == 409 and "not run on the host" in refused.value.detail
    assert not marker.exists()


@pytest.mark.asyncio
async def test_finding_the_sandbox_does_not_hold_up_the_server(tmp_path, monkeypatch):
    """Starting a sandbox (a container, an image pull) runs off the event loop."""
    import asyncio
    import time as time_module

    from flocks.server.routes import workflow as routes

    marker = tmp_path / "ran.txt"
    monkeypatch.setattr(routes, "_read_workflow_from_fs", lambda _id: {"workflowJson": _workflow(marker)})

    async def build_context(**kwargs):
        return ToolContext(session_id="ses_ui", message_id="msg_ui")

    def slow_sandbox_lookup(_ctx):
        time_module.sleep(0.6)  # what a container start blocks for
        return None

    monkeypatch.setattr(routes, "_build_workflow_tool_context", build_context)
    monkeypatch.setattr(runner_module, "_resolve_workflow_runtime_preference", lambda _ctx: "sandbox")
    monkeypatch.setattr(runner_module, "_resolve_sandbox_payload_from_config", slow_sandbox_lookup)

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    beating = asyncio.create_task(heartbeat())
    try:
        with pytest.raises(Exception):
            await routes.run_single_node("g09", routes.RunNodeRequest(node_id="start", inputs={}))
    finally:
        beating.cancel()
    assert ticks >= 5, f"the event loop stalled while the sandbox was looked for ({ticks} ticks in 0.6s)"

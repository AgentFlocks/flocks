"""Workflow worker hops retain caller context without granting an identity."""

import asyncio

import pytest

from flocks.auth.context import (
    API_TOKEN_SERVICE_USER_ID,
    AuthUser,
    get_current_auth_user,
    reset_current_auth_user,
    set_current_auth_user,
)
from flocks.hooks.execution import current_execution_context, execution_context_scope
from flocks.tool import ToolContext, ToolRegistry, ToolResult
from flocks.workflow import _async_runtime
from flocks.workflow.engine import WorkflowEngine
from flocks.workflow.models import Workflow
from flocks.workflow.repl_runtime import PythonExecRuntime
from flocks.workflow.tools_adapter import FlocksToolAdapter


def _engine(monkeypatch, execute, *, parallel=False):
    monkeypatch.setattr(ToolRegistry, "init", lambda: None)
    monkeypatch.setattr(ToolRegistry, "get", lambda _name: object())
    monkeypatch.setattr(ToolRegistry, "execute", execute)
    nodes = [{"id": "probe", "type": "python", "code": "outputs['result'] = tool.run('context_probe')"}]
    edges = []
    if parallel:
        nodes = [{"id": "start", "type": "python", "code": "outputs['ready'] = True"}] + [
            {"id": name, "type": "python", "code": f"outputs['result'] = tool.run('context_probe', node={name!r})"}
            for name in ("left", "right")
        ]
        edges = [{"from": "start", "to": name} for name in ("left", "right")]
    parent = ToolContext(session_id="parent", message_id="message", agent="rex")
    return WorkflowEngine(
        Workflow.from_dict({"start": nodes[0]["id"], "nodes": nodes, "edges": edges}),
        runtime=PythonExecRuntime(tool_registry=FlocksToolAdapter(parent)),
        node_timeout_s=3,
        max_parallel_workers=2 if parallel else 1,
        history_mode="full",
    )


def _identity():
    principal = get_current_auth_user()
    return {
        "user_id": principal.id if principal else None,
        "role": principal.role if principal else None,
        "execution": current_execution_context(),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", ["human-owner", API_TOKEN_SERVICE_USER_ID, None])
async def test_timed_node_preserves_auth_and_execution_context_through_tool_bridge(monkeypatch, user_id):
    """Match HTTP task -> to_thread -> timed node -> shared async tool loop."""
    monkeypatch.setattr(_async_runtime, "_primary_loop", asyncio.get_running_loop())

    async def execute(_name, *, ctx, **_kwargs):
        assert ctx.session_id == "parent"
        return ToolResult(success=True, output=_identity())

    engine = _engine(monkeypatch, execute)
    principal = AuthUser(id=user_id, username=user_id, role="admin") if user_id else None
    auth_token = set_current_auth_user(principal)
    try:
        with execution_context_scope({"correlation": "this-request"}, inherit=False):
            result = await asyncio.to_thread(engine.run, {}, retain_history=True)
            assert result.outputs["result"] == {
                "user_id": user_id,
                "role": "admin" if principal else None,
                "execution": {"correlation": "this-request"},
            }
            assert get_current_auth_user() is principal
            assert current_execution_context() == {"correlation": "this-request"}
    finally:
        reset_current_auth_user(auth_token)


def test_reused_timeout_worker_restores_caller_context_for_each_node(monkeypatch):
    """A node's ContextVar mutation must not become the next node's caller."""
    monkeypatch.setattr(ToolRegistry, "init", lambda: None)
    code = (
        "from flocks.auth.context import get_current_auth_user, set_current_auth_user\n"
        "from flocks.hooks.execution import current_execution_context\n"
        "principal = get_current_auth_user()\n"
        "outputs['user_id'] = principal.id if principal else None\n"
        "outputs['execution'] = current_execution_context()\n"
        "set_current_auth_user(None)\n"
    )
    workflow = Workflow.from_dict({
        "start": "first",
        "nodes": [{"id": name, "type": "python", "code": code} for name in ("first", "second")],
        "edges": [{"from": "first", "to": "second"}],
    })
    engine = WorkflowEngine(workflow, runtime=PythonExecRuntime(), node_timeout_s=3, history_mode="full")
    principal = AuthUser(id="owner", username="owner", role="member")
    token = set_current_auth_user(principal)
    try:
        with execution_context_scope({"request": "serial"}, inherit=False):
            result = engine.run({}, retain_history=True)
            assert len(result.history) == 2
            assert all(step.outputs == {"user_id": "owner", "execution": {"request": "serial"}}
                       for step in result.history)
            assert get_current_auth_user() is principal
    finally:
        reset_current_auth_user(token)


@pytest.mark.asyncio
async def test_parallel_nodes_have_independent_context_copies(monkeypatch):
    """Concurrent Context.run calls cannot safely share one Context object."""
    monkeypatch.setattr(_async_runtime, "_primary_loop", asyncio.get_running_loop())
    both_arrived = asyncio.Event()
    observed = {}

    async def execute(_name, *, ctx, node):
        observed[node] = _identity()
        if len(observed) == 2:
            both_arrived.set()
        await asyncio.wait_for(both_arrived.wait(), timeout=1)
        return ToolResult(success=True, output=observed[node])

    engine = _engine(monkeypatch, execute, parallel=True)
    principal = AuthUser(id="parallel-owner", username="parallel-owner", role="member")
    token = set_current_auth_user(principal)
    try:
        with execution_context_scope({"request": "parallel"}, inherit=False):
            result = await asyncio.to_thread(engine.run, {}, retain_history=True)
            assert not any(step.error for step in result.history)
            assert set(observed) == {"left", "right"}
            assert all(value == {"user_id": "parallel-owner", "role": "member",
                                 "execution": {"request": "parallel"}}
                       for value in observed.values())
            assert get_current_auth_user() is principal
    finally:
        reset_current_auth_user(token)


@pytest.mark.asyncio
async def test_concurrent_workflows_do_not_mix_request_identities(monkeypatch):
    monkeypatch.setattr(_async_runtime, "_primary_loop", asyncio.get_running_loop())
    both_arrived = asyncio.Event()
    seen = []

    async def execute(_name, *, ctx, **_kwargs):
        current = _identity()
        seen.append(current)
        if len(seen) == 2:
            both_arrived.set()
        await asyncio.wait_for(both_arrived.wait(), timeout=1)
        assert _identity() == current
        return ToolResult(success=True, output=current)

    engines = [_engine(monkeypatch, execute) for _ in range(2)]

    async def run(index):
        identity = f"request-{index}"
        principal = AuthUser(id=identity, username=identity, role="member")
        token = set_current_auth_user(principal)
        try:
            with execution_context_scope({"request": identity}, inherit=False):
                result = await asyncio.to_thread(engines[index].run, {}, retain_history=True)
                assert result.outputs["result"] == {
                    "user_id": identity, "role": "member", "execution": {"request": identity},
                }
                assert get_current_auth_user() is principal
        finally:
            reset_current_auth_user(token)

    await asyncio.gather(run(0), run(1))
    assert {item["user_id"] for item in seen} == {"request-0", "request-1"}

"""Native tool nodes preserve context across host and sandbox runtimes."""

import pytest

from flocks.tool import ToolContext, ToolRegistry, ToolResult
from flocks.workflow.engine import WorkflowEngine
from flocks.workflow.errors import RunCancelledError
from flocks.workflow.models import Workflow
from flocks.workflow.repl_runtime import PythonExecRuntime, SandboxPythonExecRuntime
from flocks.workflow.tools_adapter import FlocksToolAdapter


@pytest.mark.parametrize("sandbox", [False, True])
@pytest.mark.parametrize("whole_run", [False, True])
def test_native_tool_node_keeps_parent_context_and_scopes_cancellation(monkeypatch, sandbox, whole_run):
    monkeypatch.setattr(ToolRegistry, "init", lambda: None)
    monkeypatch.setattr(ToolRegistry, "get", lambda _name: object())
    captured = []
    def execute(self, name, ctx, args):
        assert callable(self.cancel_checker)
        captured.append(ctx)
        return ToolResult(success=True, output="ok")

    monkeypatch.setattr(FlocksToolAdapter, "_execute_tool_async", execute)
    parent = ToolContext(
        session_id="parent", message_id="message", agent="rex", call_id="outer",
        extra={"workspace_dir": "/workspace/example", "execution_context": {"principal": "opaque-user"}},
    )
    adapter = FlocksToolAdapter(parent)

    def previous_checker():
        return False

    adapter.cancel_checker = previous_checker

    def checker():
        return False

    runtime = (SandboxPythonExecRuntime(sandbox={}, tool_registry=adapter, cancel_checker=checker)
               if sandbox else PythonExecRuntime(tool_registry=adapter, cancel_checker=checker))
    workflow = Workflow.from_dict({"start": "probe", "nodes": [{
        "id": "probe", "type": "tool", "tool_name": "probe",
    }], "edges": []})
    engine = WorkflowEngine(workflow, runtime=runtime)
    if whole_run:
        runtime.cancel_checker = None
        assert engine.run({}, cancel=checker).outputs == {"result": "ok"}
        assert runtime.cancel_checker is None
    else:
        assert engine.run_node("probe", {}).outputs == {"result": "ok"}
    assert captured[0].session_id == parent.session_id
    assert captured[0].message_id == parent.message_id
    assert captured[0].extra == parent.extra
    assert captured[0].abort is parent.abort
    assert captured[0].call_id != parent.call_id
    assert adapter.cancel_checker is previous_checker
    runtime.cancel_checker = lambda: True
    with pytest.raises(RunCancelledError):
        engine._execute_tool_node(workflow.nodes[0], {})


def test_llm_hook_workspace_uses_parent_not_process_directory(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from flocks.workflow.llm import llm_hook_context_from_registry

    monkeypatch.chdir(tmp_path)
    parent = ToolContext(session_id="parent", message_id="parent-message", agent="rex",
                         extra={"workspace_dir": str(tmp_path / "user-project")})
    meta = llm_hook_context_from_registry(SimpleNamespace(_ctx=parent))
    assert meta["workspace"] == parent.extra["workspace_dir"]
    assert meta["sessionID"] == "parent"
    assert not meta.get("messageID"), "workflow output must not rewrite a parent chat message"

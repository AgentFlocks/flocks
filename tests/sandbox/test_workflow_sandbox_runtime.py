"""Workflow sandbox runtime tests."""

import io
import json
from types import SimpleNamespace

import pytest

from flocks.tool.registry import ToolContext
from flocks.workflow.errors import NodeExecutionError
from flocks.workflow.repl_runtime import SandboxPythonExecRuntime
from flocks.workflow.runner import run_workflow


def test_sandbox_runtime_success_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sandbox runtime should parse final frame from stdio bridge."""
    runtime = SandboxPythonExecRuntime(
        sandbox={
            "container_name": "flocks-sbx-test",
            "workspace_dir": "/tmp/workspace",
            "container_workdir": "/workspace",
            "env": {"FOO": "BAR"},
        }
    )

    class FakePopen:
        def __init__(self, *args, **kwargs):
            _ = args
            _ = kwargs
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(
                '{"type":"final","token":"tok","payload":{"outputs":{"result":1},"stdout":"ok","error":null}}\n'
            )
            self.stderr = io.StringIO("")
            self.returncode = 0

        def wait(self):
            return self.returncode

    monkeypatch.setattr("flocks.workflow.repl_runtime.uuid.uuid4", lambda: SimpleNamespace(hex="tok"))
    monkeypatch.setattr("flocks.workflow.repl_runtime.subprocess.Popen", FakePopen)

    outputs, stdout = runtime.execute("outputs['result'] = 1", {})
    assert outputs == {"result": 1}
    assert stdout == "ok"


def test_sandbox_runtime_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-zero docker exec exit should raise workflow runtime error."""
    runtime = SandboxPythonExecRuntime(
        sandbox={
            "container_name": "flocks-sbx-test",
            "workspace_dir": "/tmp/workspace",
            "container_workdir": "/workspace",
        }
    )

    class FakePopen:
        def __init__(self, *args, **kwargs):
            _ = args
            _ = kwargs
            self.stdin = io.StringIO()
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("boom")
            self.returncode = 1

        def wait(self):
            return self.returncode

    monkeypatch.setattr("flocks.workflow.repl_runtime.subprocess.Popen", FakePopen)

    with pytest.raises(NodeExecutionError, match="Sandbox execution failed"):
        runtime.execute("outputs['x'] = 1", {})


def test_run_workflow_uses_sandbox_runtime_when_context_has_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runner should prefer sandbox runtime when tool_context carries sandbox metadata."""
    captured = {"runtime_class": None}

    class FakeSandboxRuntime:
        def __init__(self, sandbox, tool_registry=None):
            self.sandbox = sandbox
            self.tool_registry = tool_registry

    class FakeHostRuntime:
        def __init__(self, tool_registry):
            self.tool_registry = tool_registry

    class FakeEngine:
        def __init__(self, _wf, runtime=None, **_kwargs):
            captured["runtime_class"] = type(runtime).__name__

        def run(self, initial_inputs=None, timeout_s=None):
            _ = initial_inputs
            _ = timeout_s
            return SimpleNamespace(
                history=[],
                steps=0,
                last_node_id=None,
                run_id="sandbox-run",
            )

    monkeypatch.setattr("flocks.workflow.runner.SandboxPythonExecRuntime", FakeSandboxRuntime)
    monkeypatch.setattr("flocks.workflow.runner.PythonExecRuntime", FakeHostRuntime)
    monkeypatch.setattr("flocks.workflow.runner.WorkflowEngine", FakeEngine)
    monkeypatch.setattr("flocks.workflow.runner.get_tool_registry", lambda tool_context=None: object())
    monkeypatch.setattr(
        "flocks.workflow.runner._load_config_data",
        lambda: {"sandbox": {"mode": "on"}},
    )

    ctx = ToolContext(
        session_id="s1",
        message_id="m1",
        extra={
            "sandbox": {
                "container_name": "flocks-sbx-test",
                "workspace_dir": "/tmp/workspace",
                "container_workdir": "/workspace",
            }
        },
    )
    workflow = {
        "id": "wf-1",
        "name": "wf",
        "start": "n1",
        "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}],
        "edges": [],
    }

    result = run_workflow(
        workflow=workflow,
        inputs={},
        ensure_requirements=False,
        tool_context=ctx,
    )
    assert result.status == "SUCCEEDED"
    assert captured["runtime_class"] == "FakeSandboxRuntime"


def test_run_workflow_uses_host_runtime_when_sandbox_mode_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sandbox.mode=off should force host runtime for workflow."""
    captured = {"runtime_class": None}

    class FakeSandboxRuntime:
        def __init__(self, sandbox, tool_registry=None):
            _ = sandbox
            _ = tool_registry

    class FakeHostRuntime:
        def __init__(self, tool_registry):
            _ = tool_registry

    class FakeEngine:
        def __init__(self, _wf, runtime=None, **_kwargs):
            captured["runtime_class"] = type(runtime).__name__

        def run(self, initial_inputs=None, timeout_s=None):
            _ = initial_inputs
            _ = timeout_s
            return SimpleNamespace(history=[], steps=0, last_node_id=None, run_id="host-run")

    monkeypatch.setattr("flocks.workflow.runner.SandboxPythonExecRuntime", FakeSandboxRuntime)
    monkeypatch.setattr("flocks.workflow.runner.PythonExecRuntime", FakeHostRuntime)
    monkeypatch.setattr("flocks.workflow.runner.WorkflowEngine", FakeEngine)
    monkeypatch.setattr("flocks.workflow.runner.get_tool_registry", lambda tool_context=None: object())
    monkeypatch.setattr(
        "flocks.workflow.runner._load_config_data",
        lambda: {
            "sandbox": {"mode": "off"},
        },
    )

    ctx = ToolContext(
        session_id="s1",
        message_id="m1",
        extra={
            "sandbox": {
                "container_name": "flocks-sbx-test",
                "workspace_dir": "/tmp/workspace",
                "container_workdir": "/workspace",
            }
        },
    )
    workflow = {
        "id": "wf-1",
        "name": "wf",
        "start": "n1",
        "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}],
        "edges": [],
    }

    result = run_workflow(workflow=workflow, inputs={}, ensure_requirements=False, tool_context=ctx)
    assert result.status == "SUCCEEDED"
    assert captured["runtime_class"] == "FakeHostRuntime"


def test_run_workflow_uses_sandbox_runtime_when_sandbox_mode_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sandbox.mode=on should default workflow runtime to sandbox."""
    captured = {"runtime_class": None}

    class FakeSandboxRuntime:
        def __init__(self, sandbox, tool_registry=None):
            self.sandbox = sandbox
            self.tool_registry = tool_registry

    class FakeHostRuntime:
        def __init__(self, tool_registry):
            _ = tool_registry

    class FakeEngine:
        def __init__(self, _wf, runtime=None, **_kwargs):
            captured["runtime_class"] = type(runtime).__name__

        def run(self, initial_inputs=None, timeout_s=None):
            _ = initial_inputs
            _ = timeout_s
            return SimpleNamespace(history=[], steps=0, last_node_id=None, run_id="sandbox-default-run")

    async def fake_resolve_sandbox_context(**kwargs):
        _ = kwargs
        return SimpleNamespace(
            container_name="flocks-sbx-default",
            workspace_dir="/tmp/workspace",
            container_workdir="/workspace",
            workspace_access="rw",
            agent_workspace_dir="/tmp/workspace/.flocks/agents/build",
            env={"HELLO": "1"},
        )

    monkeypatch.setattr("flocks.workflow.runner.SandboxPythonExecRuntime", FakeSandboxRuntime)
    monkeypatch.setattr("flocks.workflow.runner.PythonExecRuntime", FakeHostRuntime)
    monkeypatch.setattr("flocks.workflow.runner.WorkflowEngine", FakeEngine)
    monkeypatch.setattr("flocks.workflow.runner.get_tool_registry", lambda tool_context=None: object())
    monkeypatch.setattr("flocks.workflow.runner.resolve_sandbox_context", fake_resolve_sandbox_context)
    monkeypatch.setattr(
        "flocks.workflow.runner._load_config_data",
        lambda: {"sandbox": {"mode": "on"}},
    )

    ctx = ToolContext(session_id="s1", message_id="m1", agent="rex")
    workflow = {
        "id": "wf-1",
        "name": "wf",
        "start": "n1",
        "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}],
        "edges": [],
    }

    result = run_workflow(workflow=workflow, inputs={}, ensure_requirements=False, tool_context=ctx)
    assert result.status == "SUCCEEDED"
    assert captured["runtime_class"] == "FakeSandboxRuntime"


def test_run_workflow_installs_requirements_in_sandbox_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirements should be installed inside sandbox when runtime is sandbox."""
    calls = {"host": 0, "sandbox": 0}

    class FakeSandboxRuntime:
        def __init__(self, sandbox, tool_registry=None):
            _ = sandbox
            _ = tool_registry

    class FakeHostRuntime:
        def __init__(self, tool_registry):
            _ = tool_registry

    class FakeEngine:
        def __init__(self, _wf, runtime=None, **_kwargs):
            _ = runtime

        def run(self, initial_inputs=None, timeout_s=None):
            _ = initial_inputs
            _ = timeout_s
            return SimpleNamespace(history=[], steps=0, last_node_id=None, run_id="sandbox-req-run")

    class FakeHostInstaller:
        def __init__(self, installer="auto"):
            _ = installer

        def ensure_installed(self, requirements):
            _ = requirements
            calls["host"] += 1
            return True

    class FakeSandboxInstaller:
        def __init__(self, installer="auto"):
            _ = installer

        def ensure_installed(self, requirements, sandbox):
            _ = requirements
            _ = sandbox
            calls["sandbox"] += 1
            return True

    monkeypatch.setattr("flocks.workflow.runner.SandboxPythonExecRuntime", FakeSandboxRuntime)
    monkeypatch.setattr("flocks.workflow.runner.PythonExecRuntime", FakeHostRuntime)
    monkeypatch.setattr("flocks.workflow.runner.WorkflowEngine", FakeEngine)
    monkeypatch.setattr("flocks.workflow.runner.RequirementsInstaller", FakeHostInstaller)
    monkeypatch.setattr("flocks.workflow.runner.SandboxRequirementsInstaller", FakeSandboxInstaller)
    monkeypatch.setattr("flocks.workflow.runner.get_tool_registry", lambda tool_context=None: object())
    monkeypatch.setattr(
        "flocks.workflow.runner._load_config_data",
        lambda: {"sandbox": {"mode": "on"}},
    )

    ctx = ToolContext(
        session_id="s1",
        message_id="m1",
        extra={
            "sandbox": {
                "container_name": "flocks-sbx-test",
                "workspace_dir": "/tmp/workspace",
                "container_workdir": "/workspace",
            }
        },
    )
    workflow = {
        "id": "wf-1",
        "name": "wf",
        "start": "n1",
        "metadata": {"requirements": ["requests>=2.31,<3"]},
        "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}],
        "edges": [],
    }

    result = run_workflow(workflow=workflow, inputs={}, ensure_requirements=True, tool_context=ctx)
    assert result.status == "SUCCEEDED"
    assert calls["sandbox"] == 1
    assert calls["host"] == 0


def test_run_workflow_installs_requirements_in_host_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirements should be installed on host when runtime is host."""
    calls = {"host": 0, "sandbox": 0}

    class FakeSandboxRuntime:
        def __init__(self, sandbox, tool_registry=None):
            _ = sandbox
            _ = tool_registry

    class FakeHostRuntime:
        def __init__(self, tool_registry):
            _ = tool_registry

    class FakeEngine:
        def __init__(self, _wf, runtime=None, **_kwargs):
            _ = runtime

        def run(self, initial_inputs=None, timeout_s=None):
            _ = initial_inputs
            _ = timeout_s
            return SimpleNamespace(history=[], steps=0, last_node_id=None, run_id="host-req-run")

    class FakeHostInstaller:
        def __init__(self, installer="auto"):
            _ = installer

        def ensure_installed(self, requirements):
            _ = requirements
            calls["host"] += 1
            return True

    class FakeSandboxInstaller:
        def __init__(self, installer="auto"):
            _ = installer

        def ensure_installed(self, requirements, sandbox):
            _ = requirements
            _ = sandbox
            calls["sandbox"] += 1
            return True

    monkeypatch.setattr("flocks.workflow.runner.SandboxPythonExecRuntime", FakeSandboxRuntime)
    monkeypatch.setattr("flocks.workflow.runner.PythonExecRuntime", FakeHostRuntime)
    monkeypatch.setattr("flocks.workflow.runner.WorkflowEngine", FakeEngine)
    monkeypatch.setattr("flocks.workflow.runner.RequirementsInstaller", FakeHostInstaller)
    monkeypatch.setattr("flocks.workflow.runner.SandboxRequirementsInstaller", FakeSandboxInstaller)
    monkeypatch.setattr("flocks.workflow.runner.get_tool_registry", lambda tool_context=None: object())
    monkeypatch.setattr(
        "flocks.workflow.runner._load_config_data",
        lambda: {"sandbox": {"mode": "off"}},
    )

    ctx = ToolContext(session_id="s1", message_id="m1")
    workflow = {
        "id": "wf-1",
        "name": "wf",
        "start": "n1",
        "metadata": {"requirements": ["requests>=2.31,<3"]},
        "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}],
        "edges": [],
    }

    result = run_workflow(workflow=workflow, inputs={}, ensure_requirements=True, tool_context=ctx)
    assert result.status == "SUCCEEDED"
    assert calls["host"] == 1
    assert calls["sandbox"] == 0


def test_stdin_bridge_rpc_tool_and_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stdio bridge RPC handler should dispatch tool and llm requests."""
    calls = []

    class FakeRegistry:
        def run(self, name, **kwargs):
            calls.append((name, kwargs))
            return {"ok": True, "name": name}

    class FakeLLM:
        def ask(self, prompt, temperature=0.2, **_kwargs):
            return f"LLM:{prompt}:{temperature}"

    monkeypatch.setattr(
        "flocks.workflow.repl_runtime.get_lazy_llm",
        lambda **_kwargs: FakeLLM(),
    )
    runtime = SandboxPythonExecRuntime(
        sandbox={"container_name": "c", "workspace_dir": "/tmp", "container_workdir": "/workspace"},
        tool_registry=FakeRegistry(),
    )
    token = "tok"

    tool_resp = runtime._handle_rpc_request(
        msg={
            "token": token,
            "id": "1",
            "rpc": {"kind": "tool", "name": "bash", "kwargs": {"command": "echo hi"}},
        },
        token=token,
    )
    assert tool_resp["ok"] is True
    assert tool_resp["output"]["name"] == "bash"
    assert calls == [("bash", {"command": "echo hi"})]

    llm_resp = runtime._handle_rpc_request(
        msg={
            "token": token,
            "id": "2",
            "rpc": {"kind": "llm", "prompt": "hello", "temperature": 0.1},
        },
        token=token,
    )
    assert llm_resp["ok"] is True
    assert llm_resp["output"] == "LLM:hello:0.1"


def test_stdin_bridge_rejects_bad_token() -> None:
    """Stdio bridge must reject invalid token requests."""
    runtime = SandboxPythonExecRuntime(
        sandbox={"container_name": "c", "workspace_dir": "/tmp", "container_workdir": "/workspace"},
        tool_registry=SimpleNamespace(run=lambda name, **kwargs: None),
    )
    resp = runtime._handle_rpc_request(
        msg={
            "token": "bad",
            "id": "1",
            "rpc": {"kind": "tool", "name": "bash", "kwargs": {}},
        },
        token="tok",
    )
    assert resp["ok"] is False
    assert "Invalid bridge token" in str(resp.get("error"))


def test_stdin_wrapper_uses_sys_dunder_stdout_for_rpc() -> None:
    """Generated wrapper should use sys.__stdout__ as RPC channel."""
    runtime = SandboxPythonExecRuntime(
        sandbox={"container_name": "c", "workspace_dir": "/tmp", "container_workdir": "/workspace"}
    )
    cmd = runtime._build_python_cmd(code="outputs['ok']=True", bridge_token="tok")
    assert "sys.__stdout__" in cmd
    assert "/workspace/.flocks/workflow/site-packages" in cmd


def test_run_workflow_injects_workflow_file_context_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """File-based workflow should auto-inject _workflow_path/_workflow_dir into inputs."""
    captured = {"initial_inputs": None}

    class FakeHostRuntime:
        def __init__(self, tool_registry):
            _ = tool_registry

    class FakeEngine:
        def __init__(self, _wf, runtime=None, **_kwargs):
            _ = runtime

        def run(self, initial_inputs=None, timeout_s=None):
            _ = timeout_s
            captured["initial_inputs"] = dict(initial_inputs or {})
            return SimpleNamespace(history=[], steps=0, last_node_id=None, run_id="wf-input-inject")

    monkeypatch.setattr("flocks.workflow.runner.PythonExecRuntime", FakeHostRuntime)
    monkeypatch.setattr("flocks.workflow.runner.WorkflowEngine", FakeEngine)
    monkeypatch.setattr("flocks.workflow.runner.get_tool_registry", lambda tool_context=None: object())
    monkeypatch.setattr(
        "flocks.workflow.runner._load_config_data",
        lambda: {"sandbox": {"mode": "off"}},
    )

    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(
        json.dumps(
            {
                "name": "wf",
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}],
                "edges": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_workflow(
        workflow=workflow_path,
        inputs={"query": "hello"},
        ensure_requirements=False,
    )

    assert result.status == "SUCCEEDED"
    assert captured["initial_inputs"] is not None
    assert captured["initial_inputs"]["query"] == "hello"
    assert captured["initial_inputs"]["_workflow_path"] == str(workflow_path.resolve())
    assert captured["initial_inputs"]["_workflow_dir"] == str(workflow_path.resolve().parent)

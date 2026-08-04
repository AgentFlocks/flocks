"""Regression tests for isolated and compatibility node timeouts."""

import os
import threading
import time

import pytest

from flocks.workflow import NodeTimeoutError, Workflow, WorkflowEngine, run_workflow
from flocks.workflow.repl_runtime import PythonExecRuntime


def test_node_timeout_aborts_run_without_orphan_thread_or_downstream():
    """A timed-out subprocess is killed before the run fails."""
    workflow = Workflow.from_dict({
        "name": "timeout_test",
        "start": "slow",
        "nodes": [
            {
                "id": "slow",
                "type": "python",
                "code": "import time; time.sleep(5); outputs['x'] = 1",
                "description": "Never finishes within the timeout",
                "processIsolated": True,
                "timeoutFatal": True,
            },
            {
                "id": "fast",
                "type": "python",
                "code": "outputs['y'] = inputs.get('x', 0) + 10",
                "description": "Uses x or 0",
            },
        ],
        "edges": [{"from": "slow", "to": "fast"}],
    })
    rt = PythonExecRuntime()
    engine = WorkflowEngine(
        workflow,
        runtime=rt,
        node_timeout_s=0.05,
        stop_on_error=False,
    )
    baseline_threads = {thread.ident for thread in threading.enumerate() if thread.name.startswith("wf-node")}
    started = time.perf_counter()
    with pytest.raises(NodeTimeoutError) as caught:
        engine.run(initial_inputs={}, retain_history=True)
    elapsed = time.perf_counter() - started

    history = caught.value.execution_context["history"]
    assert elapsed < 1.0
    assert caught.value.execution_context["steps"] == 1
    assert len(history) == 1

    step_slow = history[0]
    assert step_slow.node_id == "slow"
    assert step_slow.error is not None
    assert "节点执行超时" in step_slow.error
    assert "0.05" in step_slow.error
    assert step_slow.outputs == {}
    assert {thread.ident for thread in threading.enumerate() if thread.name.startswith("wf-node")} == baseline_threads


def test_process_isolated_timeout_can_remain_nonfatal_for_compatibility():
    """Fatal timeout semantics remain opt-in at the node level."""
    workflow = Workflow.from_dict({
        "name": "cooperative_timeout",
        "start": "slow",
        "nodes": [
            {
                "id": "slow",
                "type": "python",
                "code": "import time; time.sleep(5)",
                "processIsolated": True,
            }
        ],
        "edges": [],
    })
    engine = WorkflowEngine(
        workflow,
        runtime=PythonExecRuntime(),
        node_timeout_s=0.05,
    )

    started = time.perf_counter()
    result = engine.run(retain_history=True)

    assert time.perf_counter() - started < 0.5
    assert result.steps == 1
    assert "节点执行超时" in (result.history[0].error or "")
    assert not any(thread.name.startswith("wf-node") for thread in threading.enumerate())


def test_process_timeout_closes_inherited_parent_fd(tmp_path):
    lease_fd = os.open(tmp_path / "lease.lock", os.O_RDWR | os.O_CREAT, 0o600)
    workflow = Workflow.from_dict({
        "start": "slow",
        "nodes": [
            {
                "id": "slow",
                "type": "python",
                "code": "import os, time\nos.fstat(inputs['lease_fd'])\ntime.sleep(5)",
                "processIsolated": True,
                "processInheritFdKeys": ["lease_fd"],
                "timeoutFatal": True,
            }
        ],
        "edges": [],
    })

    with pytest.raises(NodeTimeoutError):
        WorkflowEngine(
            workflow,
            runtime=PythonExecRuntime(),
            node_timeout_s=0.05,
        ).run({"lease_fd": lease_fd})

    with pytest.raises(OSError):
        os.fstat(lease_fd)


def test_process_rpc_bridge_matches_concurrent_responses_by_request_id():
    class Registry:
        cancel_checker = None

        def __init__(self):
            self.active = 0
            self.peak = 0
            self.lock = threading.Lock()

        def run(self, _name, *, value):
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                time.sleep((8 - value) * 0.005)
                return value
            finally:
                with self.lock:
                    self.active -= 1

    registry = Registry()
    workflow = Workflow.from_dict({
        "start": "parallel_rpc",
        "nodes": [
            {
                "id": "parallel_rpc",
                "type": "python",
                "processIsolated": True,
                "code": (
                    "from concurrent.futures import ThreadPoolExecutor\n"
                    "def call(value):\n"
                    "    return tool.run('echo', value=value)\n"
                    "with ThreadPoolExecutor(max_workers=8) as pool:\n"
                    "    outputs['values'] = list(pool.map(call, range(8)))"
                ),
            }
        ],
        "edges": [],
    })

    result = WorkflowEngine(
        workflow,
        runtime=PythonExecRuntime(tool_registry=registry),
        node_timeout_s=3,
        history_mode="full",
    ).run()

    assert result.outputs["values"] == list(range(8))
    assert registry.peak > 1


def test_node_timeout_none_disabled():
    """When node_timeout_s is None, no per-node timeout is applied."""
    workflow = Workflow.from_dict({
        "name": "no_timeout",
        "start": "a",
        "nodes": [
            {"id": "a", "type": "python", "code": "outputs['x'] = 1", "description": "Quick"},
        ],
        "edges": [],
    })
    engine = WorkflowEngine(
        workflow,
        runtime=PythonExecRuntime(),
        node_timeout_s=None,
    )
    result = engine.run(initial_inputs={}, retain_history=True)
    assert result.steps == 1
    assert result.history[0].outputs["x"] == 1


def test_run_workflow_node_timeout_param():
    """run_workflow accepts node_timeout_s and passes it to engine."""
    workflow = {
        "name": "runner_timeout",
        "start": "s",
        "nodes": [
            {
                "id": "s",
                "type": "python",
                "code": "import time; time.sleep(0.5); outputs['ok'] = 1",
                "description": "Slow",
                "processIsolated": True,
                "timeoutFatal": True,
            },
        ],
        "edges": [],
    }
    result = run_workflow(
        workflow=workflow,
        inputs={},
        node_timeout_s=0.2,
        ensure_requirements=False,
        retain_history=True,
    )
    assert result.status == "FAILED"
    assert "NodeTimeoutError" in (result.error or "")
    assert len(result.history) == 1
    assert result.history[0].get("error") is not None
    assert "节点执行超时" in result.history[0]["error"]


def test_run_workflow_uses_metadata_node_timeout_default():
    """Workflow metadata can override the historical 300s default."""
    workflow = {
        "name": "metadata_timeout",
        "start": "s",
        "nodes": [
            {
                "id": "s",
                "type": "python",
                "code": "import time; time.sleep(0.2); outputs['ok'] = 1",
                "description": "Slow-ish",
                "processIsolated": True,
                "timeoutFatal": True,
            },
        ],
        "edges": [],
        "metadata": {"node_timeout_s": 0.05},
    }
    result = run_workflow(
        workflow=workflow,
        inputs={},
        ensure_requirements=False,
        retain_history=True,
    )
    assert result.status == "FAILED"
    assert "NodeTimeoutError" in (result.error or "")
    assert len(result.history) == 1
    assert "节点执行超时" in (result.history[0].get("error") or "")


def test_run_workflow_explicit_node_timeout_overrides_metadata():
    """Explicit caller timeout should win over workflow metadata."""
    workflow = {
        "name": "metadata_timeout_override",
        "start": "s",
        "nodes": [
            {
                "id": "s",
                "type": "python",
                "code": "import time; time.sleep(0.2); outputs['ok'] = 1",
                "description": "Slow-ish",
                "processIsolated": True,
                "timeoutFatal": True,
            },
        ],
        "edges": [],
        "metadata": {"node_timeout_s": 0.05},
    }
    result = run_workflow(
        workflow=workflow,
        inputs={},
        node_timeout_s=1.0,
        ensure_requirements=False,
        retain_history=True,
    )
    assert result.status == "SUCCEEDED"
    assert len(result.history) == 1
    assert result.history[0].get("error") is None
    assert result.history[0]["outputs"]["ok"] == 1

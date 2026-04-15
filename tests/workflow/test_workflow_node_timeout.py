"""Test workflow node timeout: node times out is skipped and error is recorded."""

import pytest

from flocks.workflow import Workflow, WorkflowEngine, run_workflow
from flocks.workflow.repl_runtime import PythonExecRuntime


def test_node_timeout_skips_node_and_records_error():
    """When a node exceeds node_timeout_s, it is skipped and error is in history."""
    workflow = Workflow.from_dict({
        "name": "timeout_test",
        "start": "slow",
        "nodes": [
            {
                "id": "slow",
                "type": "python",
                "code": "import time; time.sleep(5); outputs['x'] = 1",
                "description": "Sleep 5s",
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
        node_timeout_s=1.0,
        stop_on_error=False,
    )
    result = engine.run(initial_inputs={})

    assert result.steps == 2
    assert len(result.history) == 2

    step_slow = result.history[0]
    assert step_slow.node_id == "slow"
    assert step_slow.error is not None
    assert "节点执行超时" in step_slow.error
    assert "1.0" in step_slow.error
    assert step_slow.outputs == {}

    step_fast = result.history[1]
    assert step_fast.node_id == "fast"
    assert step_fast.error is None
    assert step_fast.outputs.get("y") == 10  # x missing, get('x', 0) = 0, 0+10=10


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
    result = engine.run(initial_inputs={})
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
                "code": "import time; time.sleep(3); outputs['ok'] = 1",
                "description": "Slow",
            },
        ],
        "edges": [],
    }
    result = run_workflow(
        workflow=workflow,
        inputs={},
        node_timeout_s=0.2,
        ensure_requirements=False,
    )
    assert result.status == "SUCCEEDED"
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
            },
        ],
        "edges": [],
        "metadata": {"node_timeout_s": 0.05},
    }
    result = run_workflow(
        workflow=workflow,
        inputs={},
        ensure_requirements=False,
    )
    assert result.status == "SUCCEEDED"
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
    )
    assert result.status == "SUCCEEDED"
    assert len(result.history) == 1
    assert result.history[0].get("error") is None
    assert result.history[0]["outputs"]["ok"] == 1

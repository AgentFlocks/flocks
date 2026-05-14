"""Tests for parallel execution of sibling workflow nodes.

Verifies that when max_parallel_workers > 1 and multiple sibling nodes are
ready in the queue simultaneously, they execute concurrently via
ThreadPoolExecutor rather than serially.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict
from unittest.mock import patch

import pytest

from flocks.tool.registry import ParameterType, ToolCategory, ToolParameter, ToolRegistry, ToolResult
from flocks.workflow.engine import ExecutionResult, StepResult, WorkflowEngine
from flocks.workflow.models import Workflow
from flocks.workflow.repl_runtime import PythonExecRuntime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_fan_out_workflow(
    *,
    sibling_count: int = 3,
    sleep_seconds: float = 0.15,
    use_join: bool = False,
) -> Workflow:
    """Build a workflow: start -> N parallel siblings -> (optional join) end.

    Each sibling node sleeps for *sleep_seconds* to make the difference between
    serial and parallel execution measurable.
    """
    nodes = [
        {"id": "start", "type": "python", "code": "outputs['x'] = 1"},
    ]
    edges = []
    sibling_ids = []
    for i in range(sibling_count):
        nid = f"worker_{i}"
        sibling_ids.append(nid)
        nodes.append({
            "id": nid,
            "type": "python",
            "code": (
                f"import time; time.sleep({sleep_seconds})\n"
                f"outputs['result'] = inputs.get('x', 0) + {i}"
            ),
        })
        edges.append({"from": "start", "to": nid})

    end_node: Dict[str, Any] = {
        "id": "end",
        "type": "python",
        "code": "outputs['done'] = True",
    }
    if use_join:
        end_node["join"] = True
    nodes.append(end_node)
    for nid in sibling_ids:
        edges.append({"from": nid, "to": "end"})

    return Workflow.from_dict({
        "name": "parallel_test",
        "start": "start",
        "nodes": nodes,
        "edges": edges,
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParallelExecution:
    """Core tests for parallel sibling node execution."""

    def test_parallel_faster_than_serial(self):
        """Parallel execution of 3 siblings (each sleeping 0.15s) should
        finish significantly faster than serial (3 * 0.15 = 0.45s)."""
        ToolRegistry.init()
        wf = _build_fan_out_workflow(sibling_count=3, sleep_seconds=0.15)
        engine = WorkflowEngine(
            wf,
            runtime=PythonExecRuntime(),
            max_parallel_workers=4,
        )
        t0 = time.perf_counter()
        result = engine.run()
        elapsed = time.perf_counter() - t0

        assert result.steps >= 5  # start + 3 workers + end
        # Parallel should finish in ~0.15s + overhead, well under 0.40s.
        assert elapsed < 0.40, f"Parallel took {elapsed:.3f}s – expected < 0.40s"

    def test_serial_fallback_when_workers_1(self):
        """With max_parallel_workers=1, siblings execute serially."""
        ToolRegistry.init()
        wf = _build_fan_out_workflow(sibling_count=3, sleep_seconds=0.10)
        engine = WorkflowEngine(
            wf,
            runtime=PythonExecRuntime(),
            max_parallel_workers=1,
        )
        t0 = time.perf_counter()
        result = engine.run()
        elapsed = time.perf_counter() - t0

        assert result.steps >= 5
        # Serial should take ~0.30s or more.
        assert elapsed >= 0.25, f"Serial took {elapsed:.3f}s – expected >= 0.25s"

    def test_parallel_results_correct(self):
        """All parallel sibling nodes produce correct outputs."""
        wf = _build_fan_out_workflow(sibling_count=4, sleep_seconds=0.0)
        engine = WorkflowEngine(
            wf,
            runtime=PythonExecRuntime(),
            max_parallel_workers=4,
        )
        result = engine.run()

        worker_steps = [s for s in result.history if s.node_id.startswith("worker_")]
        assert len(worker_steps) == 4

        results = {s.outputs.get("result") for s in worker_steps}
        assert results == {1, 2, 3, 4}  # x=1 + i for i in 0..3

    def test_parallel_with_join_node(self):
        """Join node correctly waits for all parallel siblings."""
        wf = _build_fan_out_workflow(sibling_count=3, sleep_seconds=0.0, use_join=True)
        engine = WorkflowEngine(
            wf,
            runtime=PythonExecRuntime(),
            max_parallel_workers=4,
        )
        result = engine.run()

        end_steps = [s for s in result.history if s.node_id == "end"]
        assert len(end_steps) == 1
        assert end_steps[0].outputs.get("done") is True

    def test_single_node_no_parallel_overhead(self):
        """Linear workflow (no siblings) still works correctly."""
        wf = Workflow.from_dict({
            "name": "linear",
            "start": "a",
            "nodes": [
                {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                {"id": "b", "type": "python", "code": "outputs['y'] = inputs['x'] + 1"},
                {"id": "c", "type": "python", "code": "outputs['z'] = inputs['y'] + 1"},
            ],
            "edges": [
                {"from": "a", "to": "b"},
                {"from": "b", "to": "c"},
            ],
        })
        engine = WorkflowEngine(
            wf,
            runtime=PythonExecRuntime(),
            max_parallel_workers=4,
        )
        result = engine.run()

        assert result.steps == 3
        last = result.history[-1]
        assert last.node_id == "c"
        assert last.outputs.get("z") == 3


class TestParallelErrorHandling:
    """Error handling in parallel execution mode."""

    def test_parallel_one_node_fails_stop_on_error(self):
        """When stop_on_error=True, a failing sibling stops the workflow."""
        wf = Workflow.from_dict({
            "name": "fail_test",
            "start": "start",
            "nodes": [
                {"id": "start", "type": "python", "code": "outputs['x'] = 1"},
                {"id": "ok_1", "type": "python", "code": "outputs['r'] = 1"},
                {"id": "bad", "type": "python", "code": "raise ValueError('boom')"},
                {"id": "ok_2", "type": "python", "code": "outputs['r'] = 2"},
            ],
            "edges": [
                {"from": "start", "to": "ok_1"},
                {"from": "start", "to": "bad"},
                {"from": "start", "to": "ok_2"},
            ],
        })
        engine = WorkflowEngine(
            wf,
            runtime=PythonExecRuntime(),
            max_parallel_workers=4,
            stop_on_error=True,
        )
        from flocks.workflow.errors import NodeExecutionError
        with pytest.raises(NodeExecutionError, match="boom"):
            engine.run()

    def test_parallel_one_node_fails_continue(self):
        """When stop_on_error=False, other siblings still complete."""
        wf = Workflow.from_dict({
            "name": "continue_test",
            "start": "start",
            "nodes": [
                {"id": "start", "type": "python", "code": "outputs['x'] = 1"},
                {"id": "ok_1", "type": "python", "code": "outputs['r'] = 10"},
                {"id": "bad", "type": "python", "code": "raise ValueError('oops')"},
                {"id": "ok_2", "type": "python", "code": "outputs['r'] = 20"},
            ],
            "edges": [
                {"from": "start", "to": "ok_1"},
                {"from": "start", "to": "bad"},
                {"from": "start", "to": "ok_2"},
            ],
        })
        engine = WorkflowEngine(
            wf,
            runtime=PythonExecRuntime(),
            max_parallel_workers=4,
            stop_on_error=False,
        )
        result = engine.run()

        ok_steps = [s for s in result.history if s.error is None and s.node_id.startswith("ok_")]
        assert len(ok_steps) == 2
        bad_steps = [s for s in result.history if s.error is not None]
        assert len(bad_steps) == 1
        assert "oops" in bad_steps[0].error


class TestParallelHooks:
    """Verify step hooks fire correctly in parallel mode."""

    def test_hooks_called_for_each_parallel_node(self):
        """on_step_start / on_step_end are called for every node."""
        wf = _build_fan_out_workflow(sibling_count=3, sleep_seconds=0.0)
        engine = WorkflowEngine(
            wf,
            runtime=PythonExecRuntime(),
            max_parallel_workers=4,
        )
        started: list = []
        ended: list = []

        def on_start(rid, step, node, inputs):
            started.append(node.id)
            return node.id

        def on_end(token, step_result):
            ended.append(token)

        engine.run(on_step_start=on_start, on_step_end=on_end)

        assert "start" in started
        assert "end" in started
        worker_started = [s for s in started if s.startswith("worker_")]
        assert len(worker_started) == 3
        assert len(ended) == len(started)


class TestParallelToolExecution:
    """Workflow tool execution should use the shared async runtime."""

    def test_parallel_python_nodes_use_shared_tool_loop(self):
        tool_name = "workflow_parallel_shared_loop_tool"
        previous_tool = ToolRegistry._tools.get(tool_name)
        previous_default = ToolRegistry._enabled_defaults.get(tool_name)

        @ToolRegistry.register_function(
            name=tool_name,
            description="Test async workflow tool",
            category=ToolCategory.SYSTEM,
            parameters=[
                ToolParameter(
                    name="value",
                    type=ParameterType.STRING,
                    description="Value to echo back",
                    required=True,
                )
            ],
        )
        async def _workflow_parallel_shared_loop_tool(ctx, value: str) -> ToolResult:
            await asyncio.sleep(0.02)
            return ToolResult(success=True, output=f"ok:{value}")

        try:
            calls: list[str] = []

            def _spy_run_sync(coro):
                calls.append(type(coro).__name__)
                from flocks.workflow._async_runtime import run_sync

                return run_sync(coro)

            wf = Workflow.from_dict({
                "name": "parallel_tool_test",
                "start": "start",
                "nodes": [
                    {"id": "start", "type": "python", "code": "outputs['x'] = 'seed'"},
                    {
                        "id": "worker_0",
                        "type": "python",
                        "code": (
                            "result = tool.run_safe('workflow_parallel_shared_loop_tool', value=inputs['x'])\n"
                            "assert result['success'] is True\n"
                            "outputs['result'] = result['text']"
                        ),
                    },
                    {
                        "id": "worker_1",
                        "type": "python",
                        "code": (
                            "result = tool.run_safe('workflow_parallel_shared_loop_tool', value=inputs['x'])\n"
                            "assert result['success'] is True\n"
                            "outputs['result'] = result['text']"
                        ),
                    },
                    {
                        "id": "worker_2",
                        "type": "python",
                        "code": (
                            "result = tool.run_safe('workflow_parallel_shared_loop_tool', value=inputs['x'])\n"
                            "assert result['success'] is True\n"
                            "outputs['result'] = result['text']"
                        ),
                    },
                ],
                "edges": [
                    {"from": "start", "to": "worker_0"},
                    {"from": "start", "to": "worker_1"},
                    {"from": "start", "to": "worker_2"},
                ],
            })

            with patch("flocks.workflow.tools_adapter._run_sync_on_shared_loop", side_effect=_spy_run_sync):
                result = WorkflowEngine(
                    wf,
                    runtime=PythonExecRuntime(),
                    max_parallel_workers=4,
                ).run()

            worker_steps = [step for step in result.history if step.node_id.startswith("worker_")]
            assert len(worker_steps) == 3
            assert all(step.error is None for step in worker_steps)
            assert {step.outputs.get("result") for step in worker_steps} == {"ok:seed"}
            assert len(calls) == 3
        finally:
            if previous_tool is not None:
                ToolRegistry._tools[tool_name] = previous_tool
            else:
                ToolRegistry._tools.pop(tool_name, None)
            if previous_default is not None:
                ToolRegistry._enabled_defaults[tool_name] = previous_default
            else:
                ToolRegistry._enabled_defaults.pop(tool_name, None)


class TestParallelDedup:
    """Dedup still works correctly with batch draining."""

    def test_dedup_with_parallel_batch(self):
        """Identical inputs to the same node are deduped within a batch."""
        wf = Workflow.from_dict({
            "name": "dedup_par",
            "start": "a",
            "nodes": [
                {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                {"id": "b", "type": "python", "code": "outputs['y'] = inputs.get('x')"},
            ],
            "edges": [
                {"from": "a", "to": "b"},
                {"from": "a", "to": "b"},
            ],
        })
        engine = WorkflowEngine(
            wf,
            runtime=PythonExecRuntime(),
            max_parallel_workers=4,
        )
        result = engine.run()
        b_steps = [s for s in result.history if s.node_id == "b"]
        assert len(b_steps) == 1


class TestParallelTimeout:
    """Node timeout behaviour in parallel mode."""

    def test_parallel_timeout_marks_slow_node(self):
        """A slow parallel node is marked as timed-out while fast ones succeed."""
        wf = Workflow.from_dict({
            "name": "par_timeout",
            "start": "start",
            "nodes": [
                {"id": "start", "type": "python", "code": "outputs['x'] = 1"},
                {"id": "fast", "type": "python", "code": "outputs['r'] = 'ok'"},
                {
                    "id": "slow",
                    "type": "python",
                    "code": "import time; time.sleep(5); outputs['r'] = 'done'",
                },
            ],
            "edges": [
                {"from": "start", "to": "fast"},
                {"from": "start", "to": "slow"},
            ],
        })
        engine = WorkflowEngine(
            wf,
            runtime=PythonExecRuntime(),
            max_parallel_workers=4,
            node_timeout_s=0.3,
            stop_on_error=False,
        )
        t0 = time.perf_counter()
        result = engine.run()
        elapsed = time.perf_counter() - t0

        # Should complete near the timeout, not wait for the 5s sleep.
        assert elapsed < 2.0

        fast_step = next(s for s in result.history if s.node_id == "fast")
        assert fast_step.error is None
        assert fast_step.outputs.get("r") == "ok"

        slow_step = next(s for s in result.history if s.node_id == "slow")
        assert slow_step.error is not None
        assert "超时" in slow_step.error

    def test_parallel_timeout_is_non_fatal(self):
        """Timeout in parallel does not trigger stop_on_error."""
        wf = Workflow.from_dict({
            "name": "par_timeout_nonfatal",
            "start": "start",
            "nodes": [
                {"id": "start", "type": "python", "code": "outputs['x'] = 1"},
                {
                    "id": "slow",
                    "type": "python",
                    "code": "import time; time.sleep(5); outputs['r'] = 'done'",
                },
                {"id": "fast", "type": "python", "code": "outputs['r'] = 'ok'"},
            ],
            "edges": [
                {"from": "start", "to": "slow"},
                {"from": "start", "to": "fast"},
            ],
        })
        engine = WorkflowEngine(
            wf,
            runtime=PythonExecRuntime(),
            max_parallel_workers=4,
            node_timeout_s=0.3,
            stop_on_error=True,
        )
        # Should NOT raise even though stop_on_error=True, because timeout is non-fatal.
        result = engine.run()
        errors = [s for s in result.history if s.error is not None]
        assert len(errors) == 1
        assert "超时" in errors[0].error


class TestParallelBranch:
    """Branch nodes followed by parallel siblings."""

    def test_branch_true_parallel_siblings(self):
        """After a branch selects 'true', parallel siblings execute correctly."""
        wf = Workflow.from_dict({
            "name": "branch_par",
            "start": "start",
            "nodes": [
                {"id": "start", "type": "python", "code": "outputs['flag'] = True"},
                {"id": "br", "type": "branch", "select_key": "flag"},
                {"id": "a", "type": "python", "code": "outputs['r'] = 'a'"},
                {"id": "b", "type": "python", "code": "outputs['r'] = 'b'"},
                {"id": "c_true_1", "type": "python", "code": "outputs['v'] = 10"},
                {"id": "c_true_2", "type": "python", "code": "outputs['v'] = 20"},
            ],
            "edges": [
                {"from": "start", "to": "br"},
                {"from": "br", "to": "a", "label": "true"},
                {"from": "br", "to": "b", "label": "false"},
                {"from": "a", "to": "c_true_1"},
                {"from": "a", "to": "c_true_2"},
            ],
        })
        engine = WorkflowEngine(
            wf,
            runtime=PythonExecRuntime(),
            max_parallel_workers=4,
        )
        result = engine.run()

        executed_ids = [s.node_id for s in result.history]
        assert "a" in executed_ids
        assert "b" not in executed_ids
        assert "c_true_1" in executed_ids
        assert "c_true_2" in executed_ids

        c_steps = [s for s in result.history if s.node_id.startswith("c_true_")]
        assert {s.outputs.get("v") for s in c_steps} == {10, 20}


class TestParallelValidation:
    """Parameter validation for parallel settings."""

    def test_max_parallel_workers_zero_raises(self):
        wf = Workflow.from_dict({
            "name": "val",
            "start": "a",
            "nodes": [{"id": "a", "type": "python", "code": "pass"}],
            "edges": [],
        })
        with pytest.raises(ValueError, match="max_parallel_workers must be >= 1"):
            WorkflowEngine(wf, max_parallel_workers=0)

    def test_max_parallel_workers_negative_raises(self):
        wf = Workflow.from_dict({
            "name": "val",
            "start": "a",
            "nodes": [{"id": "a", "type": "python", "code": "pass"}],
            "edges": [],
        })
        with pytest.raises(ValueError, match="max_parallel_workers must be >= 1"):
            WorkflowEngine(wf, max_parallel_workers=-1)


class TestParallelCancel:
    """Cancellation during parallel execution."""

    def test_cancel_between_batches(self):
        """cancel() is respected between batch iterations."""
        call_count = 0

        def cancel_after_2():
            nonlocal call_count
            call_count += 1
            return call_count > 1

        wf = Workflow.from_dict({
            "name": "cancel_test",
            "start": "a",
            "nodes": [
                {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                {"id": "b", "type": "python", "code": "outputs['y'] = 2"},
                {"id": "c", "type": "python", "code": "outputs['z'] = 3"},
            ],
            "edges": [
                {"from": "a", "to": "b"},
                {"from": "b", "to": "c"},
            ],
        })
        engine = WorkflowEngine(
            wf,
            runtime=PythonExecRuntime(),
            max_parallel_workers=4,
        )
        from flocks.workflow.errors import RunCancelledError
        with pytest.raises(RunCancelledError):
            engine.run(cancel=cancel_after_2)

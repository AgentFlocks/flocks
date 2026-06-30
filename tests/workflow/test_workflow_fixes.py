"""Tests for workflow reliability fixes:
- run_safe() unified tool output envelope
- Join lint checks (multi-incoming-no-join, expensive-node-multi-trigger)
- Engine input-hash dedup
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from flocks.tool import Tool, ToolCategory, ToolContext, ToolInfo, ToolRegistry, ToolResult
from flocks.workflow.errors import WorkflowValidationError
from flocks.workflow.models import Workflow
from flocks.workflow.engine import WorkflowEngine
from flocks.workflow import fs_store
from flocks.workflow.repl_runtime import PythonExecRuntime
from flocks.workflow.runner import run_workflow
from flocks.workflow import tools_adapter as tools_adapter_module
from flocks.workflow.tools import ToolFacade
from flocks.workflow.tools_adapter import FlocksToolAdapter
from flocks.workflow.workflow_lint import (
    lint_implicit_full_payload_edges,
    lint_expensive_node_multi_trigger,
    lint_join_requirements,
    lint_workflow,
)


# ---------------------------------------------------------------------------
# Helper: mock adapter that returns controllable outputs
# ---------------------------------------------------------------------------


class _MockToolAdapter(FlocksToolAdapter):
    """FlocksToolAdapter subclass that bypasses real tool registry."""

    def __init__(self, outputs: Dict[str, Any] | None = None):
        # Skip super().__init__ to avoid ToolRegistry.init()
        self._ctx = None
        self._executor = None
        self._outputs = outputs or {}

    def run(self, name: str, /, **kwargs: Any) -> Any:
        if name in self._outputs:
            val = self._outputs[name]
            if isinstance(val, Exception):
                from flocks.workflow.errors import NodeExecutionError

                raise NodeExecutionError(node_id="<tool>", message=str(val))
            return val
        return f"mock_output_for_{name}"


def test_subworkflow_loader_reads_filesystem_workflow_without_legacy_kv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    workflow_id = "child-filesystem-workflow"
    workflow_dir = tmp_path / ".flocks" / "plugins" / "workflows" / workflow_id
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "workflow.json").write_text(
        json.dumps(
            {
                "name": "Child Filesystem Workflow",
                "start": "child_node",
                "nodes": [
                    {
                        "id": "child_node",
                        "type": "python",
                        "code": "outputs['child_result'] = inputs.get('value', 'missing')",
                    }
                ],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fs_store, "_workspace_root", None)

    result = run_workflow(
        workflow={
            "id": "parent-filesystem-workflow",
            "start": "call_child",
            "nodes": [
                {
                    "id": "call_child",
                    "type": "subworkflow",
                    "workflow_id": workflow_id,
                }
            ],
            "edges": [],
        },
        inputs={"value": "ok"},
        ensure_requirements=False,
    )

    assert result.status == "SUCCEEDED"
    assert result.outputs == {"output": {"child_result": "ok"}}


# ===================================================================
# 方案 1: run_safe() tests
# ===================================================================


class TestRunSafe:
    """Tests for FlocksToolAdapter.run_safe() and ToolFacade.run_safe()."""

    def test_run_safe_str_output(self):
        adapter = _MockToolAdapter(outputs={"grep": "line1\nline2"})
        result = adapter.run_safe("grep", pattern="x")
        assert result["success"] is True
        assert result["text"] == "line1\nline2"
        assert result["obj"] == "line1\nline2"
        assert result["error"] is None

    def test_run_safe_dict_output(self):
        adapter = _MockToolAdapter(outputs={"memory_search": {"results": [{"id": 1}], "count": 1}})
        result = adapter.run_safe("memory_search", query="test")
        assert result["success"] is True
        assert isinstance(result["text"], str)
        assert '"results"' in result["text"]
        assert result["obj"] == {"results": [{"id": 1}], "count": 1}
        assert result["error"] is None

    def test_run_safe_none_output(self):
        adapter = _MockToolAdapter(outputs={"empty_tool": None})
        result = adapter.run_safe("empty_tool")
        assert result["success"] is True
        assert result["text"] == ""
        assert result["obj"] is None

    def test_run_safe_error(self):
        adapter = _MockToolAdapter(outputs={"bad_tool": Exception("connection timeout")})
        result = adapter.run_safe("bad_tool")
        assert result["success"] is False
        assert result["text"] == ""
        assert result["obj"] is None
        assert "connection timeout" in result["error"]

    def test_run_safe_unknown_tool(self):
        """Mock adapter returns a default string for unknown tools.
        In production, ToolRegistry.get() raises, but the mock doesn't.
        Here we test that run_safe still returns a valid envelope."""
        adapter = _MockToolAdapter(outputs={})
        result = adapter.run_safe("nonexistent")
        # Mock returns "mock_output_for_nonexistent" for unknown tools
        assert result["success"] is True
        assert "mock_output_for_nonexistent" in result["text"]

    def test_run_safe_explicit_error(self):
        """Explicit error entry in outputs -> run_safe catches and wraps."""
        adapter = _MockToolAdapter(outputs={"failing": Exception("service unavailable")})
        result = adapter.run_safe("failing")
        assert result["success"] is False
        assert result["obj"] is None
        assert "service unavailable" in result["error"]

    def test_facade_run_safe_delegates(self):
        adapter = _MockToolAdapter(outputs={"websearch": "search results"})
        facade = ToolFacade(adapter)
        result = facade.run_safe("websearch", query="test")
        assert result["success"] is True
        assert result["text"] == "search results"

    def test_run_safe_retries_after_lazy_mcp_tool_load(self, monkeypatch: pytest.MonkeyPatch):
        tool_name = "workflow_lazy_mcp_test_tool"
        ToolRegistry.unregister(tool_name)

        async def _handler(_ctx: ToolContext) -> ToolResult:
            return ToolResult(success=True, output="lazy-mcp-ok")

        def _fake_lazy_load() -> None:
            ToolRegistry.register(
                Tool(
                    info=ToolInfo(
                        name=tool_name,
                        description="Lazy MCP test tool",
                        category=ToolCategory.CUSTOM,
                        parameters=[],
                    ),
                    handler=_handler,
                )
            )

        monkeypatch.setattr(tools_adapter_module, "_try_lazy_load_mcp_tools", _fake_lazy_load)
        try:
            adapter = FlocksToolAdapter()
            result = adapter.run_safe(tool_name)
        finally:
            ToolRegistry.unregister(tool_name)

        assert result["success"] is True
        assert result["text"] == "lazy-mcp-ok"

    def test_run_safe_list_output(self):
        adapter = _MockToolAdapter(outputs={"list_tool": [1, 2, 3]})
        result = adapter.run_safe("list_tool")
        assert result["success"] is True
        assert result["obj"] == [1, 2, 3]
        assert result["text"] == "[1, 2, 3]"


# ===================================================================
# 方案 2 Layer 2: Lint join checks
# ===================================================================


class TestLintJoinRequirements:
    """Tests for lint_join_requirements()."""

    def test_multi_incoming_no_join_error(self):
        """Node with 2+ non-exclusive incoming edges and no join -> error."""
        wf = Workflow.from_dict(
            {
                "name": "bad_join",
                "start": "a",
                "nodes": [
                    {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                    {"id": "b", "type": "python", "code": "outputs['y'] = 2"},
                    {"id": "c", "type": "python", "code": "outputs['z'] = inputs.get('x', 0)"},
                ],
                "edges": [
                    {"from": "a", "to": "c"},
                    {"from": "b", "to": "c"},
                ],
            }
        )
        results = lint_join_requirements(wf)
        assert len(results) == 1
        assert results[0]["kind"] == "multi_incoming_no_join"
        assert results[0]["severity"] == "error"
        assert results[0]["node_id"] == "c"

    def test_exclusive_branch_no_error(self):
        """Edges from same branch with different labels are exclusive -> no error."""
        wf = Workflow.from_dict(
            {
                "name": "ok_branch",
                "start": "start",
                "nodes": [
                    {"id": "start", "type": "python", "code": "outputs['flag'] = True"},
                    {"id": "br", "type": "branch", "select_key": "flag"},
                    {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                    {"id": "b", "type": "python", "code": "outputs['x'] = 2"},
                    {"id": "merge", "type": "python", "code": "outputs['r'] = inputs.get('x')"},
                ],
                "edges": [
                    {"from": "start", "to": "br"},
                    {"from": "br", "to": "a", "label": "true"},
                    {"from": "br", "to": "b", "label": "false"},
                    {"from": "a", "to": "merge"},
                    {"from": "b", "to": "merge"},
                ],
            }
        )
        results = lint_join_requirements(wf)
        assert len(results) == 0

    def test_join_true_no_error(self):
        """Node with join=true should not trigger the lint."""
        wf = Workflow.from_dict(
            {
                "name": "ok_join",
                "start": "a",
                "nodes": [
                    {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                    {"id": "b", "type": "python", "code": "outputs['y'] = 2"},
                    {"id": "c", "type": "python", "code": "pass", "join": True},
                ],
                "edges": [
                    {"from": "a", "to": "c"},
                    {"from": "b", "to": "c"},
                ],
            }
        )
        results = lint_join_requirements(wf)
        assert len(results) == 0

    def test_mixed_exclusive_and_non_exclusive(self):
        """Branch targets + extra direct edge -> error (not fully exclusive)."""
        wf = Workflow.from_dict(
            {
                "name": "mixed",
                "start": "start",
                "nodes": [
                    {"id": "start", "type": "python", "code": "outputs['flag'] = True"},
                    {"id": "br", "type": "branch", "select_key": "flag"},
                    {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                    {"id": "b", "type": "python", "code": "outputs['x'] = 2"},
                    {"id": "merge", "type": "python", "code": "pass"},
                ],
                "edges": [
                    {"from": "start", "to": "br"},
                    {"from": "br", "to": "a", "label": "true"},
                    {"from": "br", "to": "b", "label": "false"},
                    {"from": "a", "to": "merge"},
                    {"from": "b", "to": "merge"},
                    {"from": "start", "to": "merge"},  # extra non-exclusive edge
                ],
            }
        )
        results = lint_join_requirements(wf)
        assert len(results) == 1
        assert results[0]["node_id"] == "merge"


class TestLintExpensiveNodeMultiTrigger:
    """Tests for lint_expensive_node_multi_trigger()."""

    def test_expensive_node_multi_incoming_error(self):
        """Expensive node (LLM call) with multiple non-exclusive edges -> error."""
        wf = Workflow.from_dict(
            {
                "name": "expensive_bad",
                "start": "a",
                "nodes": [
                    {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                    {"id": "b", "type": "python", "code": "outputs['y'] = 2"},
                    {
                        "id": "expensive",
                        "type": "python",
                        "code": "result = llm.ask('summarize')\noutputs['summary'] = result",
                    },
                ],
                "edges": [
                    {"from": "a", "to": "expensive"},
                    {"from": "b", "to": "expensive"},
                ],
            }
        )
        results = lint_expensive_node_multi_trigger(wf)
        assert len(results) == 1
        assert results[0]["kind"] == "expensive_node_multi_trigger"
        assert results[0]["node_id"] == "expensive"

    def test_non_expensive_node_no_error(self):
        """Non-expensive node with multiple edges -> no error from this check."""
        wf = Workflow.from_dict(
            {
                "name": "cheap_ok",
                "start": "a",
                "nodes": [
                    {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                    {"id": "b", "type": "python", "code": "outputs['y'] = 2"},
                    {"id": "c", "type": "python", "code": "outputs['z'] = 3"},
                ],
                "edges": [
                    {"from": "a", "to": "c"},
                    {"from": "b", "to": "c"},
                ],
            }
        )
        results = lint_expensive_node_multi_trigger(wf)
        assert len(results) == 0

    def test_write_tool_detected_as_expensive(self):
        """Node calling tool.run('write', ...) is detected as expensive."""
        wf = Workflow.from_dict(
            {
                "name": "write_bad",
                "start": "a",
                "nodes": [
                    {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                    {"id": "b", "type": "python", "code": "outputs['y'] = 2"},
                    {
                        "id": "writer",
                        "type": "python",
                        "code": "tool.run('write', filePath='out.md', content='hi')",
                    },
                ],
                "edges": [
                    {"from": "a", "to": "writer"},
                    {"from": "b", "to": "writer"},
                ],
            }
        )
        results = lint_expensive_node_multi_trigger(wf)
        assert len(results) == 1
        assert results[0]["node_id"] == "writer"


class TestLintWorkflowUnified:
    """Tests for lint_workflow() unified entry point."""

    def test_lint_workflow_combines_all_checks(self):
        """lint_workflow() should return results from all check functions."""
        wf = Workflow.from_dict(
            {
                "name": "combined",
                "start": "a",
                "nodes": [
                    {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                    {"id": "b", "type": "python", "code": "outputs['y'] = 2"},
                    {"id": "c", "type": "python", "code": "pass"},
                ],
                "edges": [
                    {"from": "a", "to": "c"},
                    {"from": "b", "to": "c"},
                ],
            }
        )
        results = lint_workflow(wf)
        kinds = {r["kind"] for r in results}
        assert "multi_incoming_no_join" in kinds

    def test_lint_workflow_clean(self):
        """A well-formed workflow should produce no lint results."""
        wf = Workflow.from_dict(
            {
                "name": "clean",
                "start": "a",
                "nodes": [
                    {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                    {"id": "b", "type": "python", "code": "outputs['y'] = inputs.get('mapped_x')"},
                ],
                "edges": [{"from": "a", "to": "b", "mapping": {"mapped_x": "x"}}],
            }
        )
        results = lint_workflow(wf)
        assert len(results) == 0

    def test_missing_edge_mapping_warns_by_default(self):
        wf = Workflow.from_dict(
            {
                "name": "implicit_mapping",
                "start": "a",
                "nodes": [
                    {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                    {"id": "b", "type": "python", "code": "outputs['y'] = inputs.get('x')"},
                ],
                "edges": [{"from": "a", "to": "b"}],
            }
        )

        results = lint_implicit_full_payload_edges(wf)

        assert len(results) == 1
        assert results[0]["kind"] == "implicit_full_payload_edge"
        assert results[0]["severity"] == "warning"

    def test_missing_edge_mapping_is_error_when_strict(self):
        wf = Workflow.from_dict(
            {
                "name": "strict_implicit_mapping",
                "start": "a",
                "nodes": [
                    {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                    {"id": "b", "type": "python", "code": "outputs['y'] = inputs.get('x')"},
                ],
                "edges": [{"from": "a", "to": "b"}],
                "metadata": {"runtime": {"strict_edge_mapping": True}},
            }
        )

        results = lint_implicit_full_payload_edges(wf)

        assert len(results) == 1
        assert results[0]["kind"] == "implicit_full_payload_edge"
        assert results[0]["severity"] == "error"

    def test_run_workflow_rejects_strict_implicit_mapping(self):
        workflow = {
            "name": "strict_implicit_mapping",
            "start": "a",
            "nodes": [
                {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                {"id": "b", "type": "python", "code": "outputs['y'] = inputs.get('x')"},
            ],
            "edges": [{"from": "a", "to": "b"}],
            "metadata": {"runtime": {"strict_edge_mapping": True}},
        }

        with pytest.raises(WorkflowValidationError):
            run_workflow(workflow=workflow, ensure_requirements=False)

    def test_identity_mapping_does_not_suggest_omitting_mapping(self):
        wf = Workflow.from_dict(
            {
                "name": "identity_mapping_ok",
                "start": "a",
                "nodes": [
                    {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                    {"id": "b", "type": "python", "code": "outputs['y'] = inputs.get('x')"},
                ],
                "edges": [{"from": "a", "to": "b", "mapping": {"x": "x"}}],
            }
        )

        results = lint_workflow(wf)

        assert all(item.get("kind") != "scheme_a_suggest_omit_identity_mapping" for item in results)

    def test_trigger_workflow_recommends_strict_edge_mapping(self):
        wf = Workflow.from_dict(
            {
                "name": "kafka_trigger_mapping_recommendation",
                "start": "a",
                "nodes": [{"id": "a", "type": "python", "code": "outputs['x'] = 1"}],
                "edges": [],
                "triggers": [{"type": "kafka"}],
            }
        )

        results = lint_workflow(wf)

        recommendation = next(item for item in results if item["kind"] == "recommend_strict_edge_mapping")
        assert recommendation["severity"] == "warning"
        assert recommendation["trigger_types"] == ["kafka"]

    def test_trigger_workflow_with_strict_mapping_does_not_recommend_again(self):
        wf = Workflow.from_dict(
            {
                "name": "strict_kafka_trigger_mapping",
                "start": "a",
                "nodes": [{"id": "a", "type": "python", "code": "outputs['x'] = 1"}],
                "edges": [],
                "triggers": [{"type": "kafka"}],
                "metadata": {"runtime": {"strict_edge_mapping": True}},
            }
        )

        results = lint_workflow(wf)

        assert all(item.get("kind") != "recommend_strict_edge_mapping" for item in results)


class TestPayloadRiskObservability:
    def test_large_payload_no_mapping_records_risk_without_changing_inputs(self):
        wf = Workflow.from_dict(
            {
                "name": "large_payload_no_mapping",
                "start": "a",
                "nodes": [
                    {"id": "a", "type": "python", "code": "outputs['raw_alerts'] = list(range(1500))"},
                    {
                        "id": "b",
                        "type": "python",
                        "code": "outputs['count'] = len(inputs['raw_alerts'])\noutputs['is_list'] = isinstance(inputs['raw_alerts'], list)",
                    },
                ],
                "edges": [{"from": "a", "to": "b"}],
            }
        )

        result = WorkflowEngine(wf, runtime=PythonExecRuntime()).run()

        assert result.outputs == {"count": 1500, "is_list": True}
        counts = result.payload_risk_summary["counts"]
        assert counts["implicit_full_payload_edge_large_payload"] == 1
        risk = next(
            item
            for item in result.payload_risk_summary["risks"]
            if item["kind"] == "implicit_full_payload_edge_large_payload"
        )
        assert risk["edge_from"] == "a"
        assert risk["edge_to"] == "b"
        assert risk["payload_summary"]["large_fields"][0]["key"] == "raw_alerts"
        assert risk["payload_summary"]["large_fields"][0]["count"] == 1500

    def test_large_payload_with_mapping_does_not_record_implicit_full_payload_risk(self):
        wf = Workflow.from_dict(
            {
                "name": "large_payload_with_mapping",
                "start": "a",
                "nodes": [
                    {"id": "a", "type": "python", "code": "outputs['raw_alerts'] = list(range(1500))"},
                    {"id": "b", "type": "python", "code": "outputs['count'] = len(inputs['alerts'])"},
                ],
                "edges": [{"from": "a", "to": "b", "mapping": {"alerts": "raw_alerts"}}],
            }
        )

        result = WorkflowEngine(wf, runtime=PythonExecRuntime()).run()

        assert result.outputs == {"count": 1500}
        counts = result.payload_risk_summary["counts"]
        assert "implicit_full_payload_edge_large_payload" not in counts

    def test_large_payload_fanout_records_risk(self):
        wf = Workflow.from_dict(
            {
                "name": "large_payload_fanout",
                "start": "a",
                "nodes": [
                    {"id": "a", "type": "python", "code": "outputs['raw_alerts'] = list(range(1500))"},
                    {"id": "b", "type": "python", "code": "outputs['b_count'] = len(inputs['raw_alerts'])"},
                    {"id": "c", "type": "python", "code": "outputs['c_count'] = len(inputs['raw_alerts'])"},
                ],
                "edges": [{"from": "a", "to": "b"}, {"from": "a", "to": "c"}],
            }
        )

        result = WorkflowEngine(wf, runtime=PythonExecRuntime()).run()

        counts = result.payload_risk_summary["counts"]
        assert counts["large_payload_fanout"] == 1
        risk = next(item for item in result.payload_risk_summary["risks"] if item["kind"] == "large_payload_fanout")
        assert risk["source_node_id"] == "a"
        assert risk["fanout_count"] == 2
        assert set(risk["target_node_ids"]) == {"b", "c"}

    def test_large_payload_join_buffer_records_risk(self):
        wf = Workflow.from_dict(
            {
                "name": "large_payload_join",
                "start": "start",
                "nodes": [
                    {"id": "start", "type": "python", "code": "outputs['raw_alerts'] = list(range(1500))"},
                    {"id": "b", "type": "python", "code": "outputs['x'] = 1"},
                    {
                        "id": "join",
                        "type": "python",
                        "join": True,
                        "code": "outputs['count'] = len(inputs.get('raw_alerts', []))",
                    },
                ],
                "edges": [
                    {"from": "start", "to": "join"},
                    {"from": "start", "to": "b"},
                    {"from": "b", "to": "join"},
                ],
            }
        )

        result = WorkflowEngine(wf, runtime=PythonExecRuntime()).run()

        assert result.outputs == {"count": 1500}
        counts = result.payload_risk_summary["counts"]
        assert counts["large_payload_join_buffer"] >= 1

    def test_payload_risk_summary_does_not_embed_large_payload(self):
        wf = Workflow.from_dict(
            {
                "name": "large_payload_summary_bound",
                "start": "a",
                "nodes": [
                    {"id": "a", "type": "python", "code": "outputs['events'] = list(range(10000))"},
                    {"id": "b", "type": "python", "code": "outputs['count'] = len(inputs['events'])"},
                ],
                "edges": [{"from": "a", "to": "b"}],
            }
        )

        result = WorkflowEngine(wf, runtime=PythonExecRuntime()).run()

        text = json.dumps(result.payload_risk_summary)
        assert len(text) < 20_000
        assert '"count": 10000' in text
        assert "[0, 1, 2, 3, 4" not in text


# ===================================================================
# 方案 2 Layer 3: Engine dedup
# ===================================================================


class TestEngineDedup:
    """Tests for engine input-hash dedup."""

    def test_dedup_different_inputs_both_execute(self):
        """When a node receives different inputs from two sources, both execute (no dedup)."""
        wf = Workflow.from_dict(
            {
                "name": "dedup_test",
                "start": "a",
                "nodes": [
                    # a fans out to b and c (python node sends to all outgoing edges)
                    {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                    {"id": "b", "type": "python", "code": "outputs['x'] = 2"},
                    {"id": "d", "type": "python", "code": "outputs['result'] = inputs.get('x', 0)"},
                ],
                "edges": [
                    {"from": "a", "to": "b"},
                    {"from": "a", "to": "d"},
                    {"from": "b", "to": "d"},
                ],
            }
        )
        engine = WorkflowEngine(
            wf,
            runtime=PythonExecRuntime(),
            stop_on_error=False,
            history_mode="full",
        )
        result = engine.run(initial_inputs={}, retain_history=True)

        # a -> d (inputs: {x: 1}), b -> d (inputs: {x: 2})
        # Different inputs -> both should execute
        d_steps = [s for s in result.history if s.node_id == "d"]
        assert len(d_steps) == 2
        results = {s.outputs.get("result") for s in d_steps}
        assert results == {1, 2}

    def test_dedup_skips_truly_identical_inputs(self):
        """Identical inputs to the same node -> second execution is skipped."""
        wf = Workflow.from_dict(
            {
                "name": "dedup_identical",
                "start": "a",
                "nodes": [
                    {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                    # Two edges from a to b (rare but possible)
                    {"id": "b", "type": "python", "code": "outputs['y'] = inputs.get('x')"},
                ],
                "edges": [
                    {"from": "a", "to": "b"},
                    {"from": "a", "to": "b"},
                ],
            }
        )
        engine = WorkflowEngine(
            wf,
            runtime=PythonExecRuntime(),
            stop_on_error=False,
            history_mode="full",
        )
        result = engine.run(initial_inputs={}, retain_history=True)

        # a executes once. b enqueued twice with identical inputs (x=1).
        # Dedup should skip the second execution of b.
        b_steps = [s for s in result.history if s.node_id == "b"]
        assert len(b_steps) == 1
        assert b_steps[0].outputs.get("y") == 1


# ===================================================================
# 方案 3: Example workflow.json validation
# ===================================================================


class TestExampleWorkflow:
    """Validate the fixed example workflow.json."""

    def test_example_workflow_loads(self):
        """Example workflow.json should be valid and loadable."""
        import os

        wf_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            ".flocks",
            "workflow",
            "search_summary",
            "workflow.json",
        )
        if not os.path.exists(wf_path):
            pytest.skip("Example workflow.json not found")

        with open(wf_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        wf = Workflow.from_dict(data)
        assert wf.start == "search_web"
        assert len(wf.nodes) == 6

    def test_example_workflow_no_lint_errors(self):
        """Example workflow should pass all lint checks (no errors)."""
        import os

        wf_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            ".flocks",
            "workflow",
            "search_summary",
            "workflow.json",
        )
        if not os.path.exists(wf_path):
            pytest.skip("Example workflow.json not found")

        with open(wf_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        wf = Workflow.from_dict(data)
        results = lint_workflow(wf)
        errors = [r for r in results if r.get("severity") == "error"]
        assert len(errors) == 0, f"Lint errors found: {errors}"

    def test_no_exec_json_files(self):
        """workflow-exec.json and workflow-exec-b.json should not exist."""
        import os

        base = os.path.join(
            os.path.dirname(__file__),
            "..",
            ".flocks",
            "workflow",
            "search_summary",
        )
        assert not os.path.exists(os.path.join(base, "workflow-exec.json"))
        assert not os.path.exists(os.path.join(base, "workflow-exec-b.json"))

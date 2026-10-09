"""
Run Workflow Node Tool - Execute a single workflow node in isolation.

Intended for step-by-step testing and debugging during workflow development.
Follows the same interface as the POST /api/workflow/{id}/run-node endpoint.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Optional, Union

from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from flocks.utils.log import Log


log = Log.create(service="tool.run_workflow_node")

DESCRIPTION = """Execute a single workflow node in isolation for step-by-step testing.

Use this tool when testing a workflow node-by-node (BFS order):
1. Call with the first node and the sample input data.
2. Pass each node's `outputs` as `inputs` to the next node.
3. Fix errors in `workflow.json`, then re-run the failing node until `success=true`.
4. After all nodes pass, run the full workflow with `run_workflow`.

Parameters:
- workflow: Workflow definition (dict) or absolute path to workflow.json.
- node_id: ID of the node to execute (must exist in the workflow).
- inputs: Input data for the node (use previous node's outputs for downstream nodes).

Returns:
- node_id, outputs, stdout, error, traceback, duration_ms, success
"""


def _load_workflow_dict(workflow: Union[Dict[str, Any], str]) -> Dict[str, Any]:
    """Resolve workflow parameter to a dict."""
    if isinstance(workflow, dict):
        return workflow
    raw = str(workflow).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        p = Path(raw).expanduser()
        if p.exists() and p.is_file():
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        raise ValueError(
            f"Unsupported workflow value. Provide a workflow dict or a valid workflow.json file path. Got: {raw!r}"
        )


def _run_node_sync(
    workflow_dict: Dict[str, Any],
    node_id: str,
    inputs: Dict[str, Any],
) -> Dict[str, Any]:
    """Synchronous helper: run one node via WorkflowEngine.run_node()."""
    from flocks.workflow.models import Workflow as WfModel
    from flocks.workflow.engine import WorkflowEngine
    from flocks.workflow.repl_runtime import PythonExecRuntime

    wf = WfModel.from_dict(workflow_dict)
    engine = WorkflowEngine(wf, runtime=PythonExecRuntime())
    step = engine.run_node(node_id, inputs)
    return {
        "node_id": step.node_id,
        "outputs": step.outputs,
        "stdout": step.stdout or "",
        "error": step.error,
        "traceback": step.traceback,
        "duration_ms": step.duration_ms,
        "success": step.error is None,
    }


def _format_node_result(result: Dict[str, Any]) -> str:
    """Format node result as a readable string."""
    lines = []
    node_id = result.get("node_id", "?")
    success = result.get("success", False)
    duration_ms = result.get("duration_ms")

    status_icon = "✓" if success else "✗"
    dur_str = f" ({duration_ms:.1f}ms)" if duration_ms is not None else ""
    lines.append(f"[{status_icon}] Node: {node_id}{dur_str}")

    stdout = result.get("stdout", "")
    if stdout and stdout.strip():
        lines.append("\nStdout:")
        for line in stdout.rstrip().splitlines():
            lines.append(f"  {line}")

    if not success:
        error = result.get("error", "")
        lines.append(f"\nError: {error}")
        tb = result.get("traceback", "")
        if tb:
            lines.append("Traceback:")
            for line in tb.rstrip().splitlines():
                lines.append(f"  {line}")
    else:
        outputs = result.get("outputs", {})
        if outputs:
            lines.append("\nOutputs:")
            try:
                lines.append(json.dumps(outputs, indent=2, ensure_ascii=False))
            except Exception:
                lines.append(str(outputs))

    return "\n".join(lines)


@ToolRegistry.register_function(
    name="run_workflow_node",
    group="任务与工作流",
    description=DESCRIPTION,
    category=ToolCategory.SYSTEM,
    requires_confirmation=False,
    parameters=[
        ToolParameter(
            name="workflow",
            type=ParameterType.OBJECT,
            description="Workflow definition (dict) or absolute path to workflow.json.",
            required=True,
            json_schema={
                "anyOf": [
                    {"type": "object", "description": "Workflow JSON as a dict"},
                    {"type": "string", "description": "Absolute path to workflow.json"},
                ]
            },
        ),
        ToolParameter(
            name="node_id",
            type=ParameterType.STRING,
            description="ID of the node to execute.",
            required=True,
        ),
        ToolParameter(
            name="inputs",
            type=ParameterType.OBJECT,
            description="Input data for the node. Use the previous node's outputs for downstream nodes.",
            required=False,
            default={},
            json_schema={"type": "object", "additionalProperties": True},
        ),
    ],
)
async def run_workflow_node_tool(
    ctx: ToolContext,
    workflow: Union[Dict[str, Any], str],
    node_id: str,
    inputs: Optional[Dict[str, Any]] = None,
) -> ToolResult:
    """Execute a single workflow node in isolation."""
    try:
        workflow_dict = _load_workflow_dict(workflow)
    except (ValueError, json.JSONDecodeError, FileNotFoundError) as e:
        return ToolResult(success=False, error=str(e))

    node_inputs = inputs or {}

    log.info("run_workflow_node.start", {
        "node_id": node_id,
        "workflow_name": workflow_dict.get("name", "?"),
    })

    NODE_TIMEOUT_SECONDS = 120

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_run_node_sync, workflow_dict, node_id, node_inputs),
            timeout=NODE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        msg = f"Node '{node_id}' timed out after {NODE_TIMEOUT_SECONDS}s. Check for infinite loops, blocking I/O, or slow external calls."
        log.error("run_workflow_node.timeout", {"node_id": node_id, "timeout_s": NODE_TIMEOUT_SECONDS})
        return ToolResult(success=False, error=msg)
    except KeyError:
        nodes = list(workflow_dict.get("nodes", []))
        node_ids = [n.get("id") for n in nodes if isinstance(n, dict)]
        return ToolResult(
            success=False,
            error=f"Node '{node_id}' not found in workflow. Available nodes: {node_ids}",
        )
    except Exception as e:
        log.error("run_workflow_node.error", {"node_id": node_id, "error": str(e)})
        return ToolResult(success=False, error=f"Failed to run node '{node_id}': {e}")

    output_text = _format_node_result(result)

    log.info("run_workflow_node.done", {
        "node_id": node_id,
        "success": result["success"],
        "duration_ms": result.get("duration_ms"),
    })

    return ToolResult(
        success=result["success"],
        output=output_text,
        error=result.get("error"),
        title=f"Node: {node_id}",
        metadata={
            "node_id": result["node_id"],
            "outputs": result["outputs"],
            "stdout": result["stdout"],
            "error": result.get("error"),
            "traceback": result.get("traceback"),
            "duration_ms": result.get("duration_ms"),
            "success": result["success"],
        },
    )

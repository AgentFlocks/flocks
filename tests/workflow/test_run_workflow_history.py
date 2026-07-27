"""Test run_workflow tool keeps final metadata lightweight."""

import json
import pytest
from pathlib import Path

from flocks.tool.task.run_workflow import run_workflow_tool
from flocks.tool.registry import ToolContext


class MockToolContext(ToolContext):
    """ToolContext with stable IDs for workflow tests."""

    def __init__(self) -> None:
        super().__init__(session_id="test-session", message_id="test-message")


@pytest.mark.asyncio
async def test_workflow_history_in_output():
    """Final tool metadata omits retained history while output stays concise."""
    
    # Create a simple test workflow
    workflow = {
        "name": "test_history_workflow",
        "start": "step1",
        "nodes": [
            {
                "id": "step1",
                "type": "python",
                "code": "outputs['result1'] = inputs.get('x', 0) + 10",
                "description": "Add 10 to input x"
            },
            {
                "id": "step2",
                "type": "python",
                "code": "outputs['result2'] = inputs.get('result1', 0) * 2",
                "description": "Multiply result1 by 2"
            },
            {
                "id": "step3",
                "type": "python",
                "code": "outputs['final'] = inputs.get('result2', 0) + 5",
                "description": "Add 5 to result2"
            }
        ],
        "edges": [
            {"from": "step1", "to": "step2"},
            {"from": "step2", "to": "step3"}
        ]
    }
    
    inputs = {"x": 5}
    
    # Execute workflow
    ctx = MockToolContext()
    result = await run_workflow_tool(
        ctx=ctx,
        workflow=workflow,
        inputs=inputs,
        ensure_requirements=False,
        trace=False
    )
    
    # Verify result structure
    assert result.success is True
    assert result.output is not None
    
    # Final tool metadata should not retain full per-step history or outputs in memory.
    assert "history" not in result.metadata
    assert "outputs" not in result.metadata
    
    # Verify final outputs remain available in the agent-facing output text.
    assert result.metadata["has_output"] is True
    assert result.metadata["output_keys"] == ["final"]
    assert '"final": 35' in result.output
    
    # Verify output text no longer expands the full execution history
    assert "Status: SUCCEEDED" in result.output
    assert "Final Outputs:" in result.output
    assert "Execution History" not in result.output
    assert "Inputs:" not in result.output
    assert "Stdout:" not in result.output


@pytest.mark.asyncio
async def test_workflow_history_with_error():
    """Failure metadata remains lightweight even when execution fails."""
    
    workflow = {
        "name": "test_error_workflow",
        "start": "step1",
        "nodes": [
            {
                "id": "step1",
                "type": "python",
                "code": "outputs['value'] = 100",
                "description": "Set initial value"
            },
            {
                "id": "step2",
                "type": "python",
                "code": "raise ValueError('Intentional error')",
                "description": "This step will fail"
            }
        ],
        "edges": [
            {"from": "step1", "to": "step2"}
        ]
    }
    
    ctx = MockToolContext()
    result = await run_workflow_tool(
        ctx=ctx,
        workflow=workflow,
        inputs={},
        ensure_requirements=False,
        trace=False
    )
    
    # Workflow should fail
    assert result.success is False
    assert result.error is not None
    
    # Per-step details are written through execution step rows, not retained
    # in the final ToolResult metadata.
    assert "history" not in result.metadata
    assert "outputs" not in result.metadata
    
    # Output should contain only the top-level failure summary
    assert "Error:" in result.output
    assert "Execution History" not in result.output
    assert "Inputs:" not in result.output
    assert "Stdout:" not in result.output


@pytest.mark.asyncio
async def test_workflow_history_with_stdout():
    """Stdout is not retained in final metadata history or tool output."""
    
    workflow = {
        "name": "test_stdout_workflow",
        "start": "step1",
        "nodes": [
            {
                "id": "step1",
                "type": "python",
                "code": "print('Hello from step1')\noutputs['msg'] = 'done'",
                "description": "Print and set output"
            }
        ],
        "edges": []
    }
    
    ctx = MockToolContext()
    result = await run_workflow_tool(
        ctx=ctx,
        workflow=workflow,
        inputs={},
        ensure_requirements=False,
        trace=False
    )
    
    assert result.success is True
    
    assert "history" not in result.metadata
    assert "outputs" not in result.metadata
    
    # Output should stay concise and omit per-step stdout details
    assert "Stdout:" not in result.output
    assert "Hello from step1" not in result.output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

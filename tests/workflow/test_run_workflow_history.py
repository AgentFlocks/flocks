"""Test run_workflow tool keeps execution history in metadata."""

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
    """History stays in metadata while the default output stays concise."""
    
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
    
    # Verify metadata contains history
    assert "history" in result.metadata
    history = result.metadata["history"]
    
    # Should have 3 steps
    assert len(history) == 3
    
    # Verify step 1
    step1 = history[0]
    assert step1["node_id"] == "step1"
    assert "inputs" in step1
    assert step1["inputs"]["x"] == 5
    assert "outputs" in step1
    assert step1["outputs"]["result1"] == 15
    assert step1.get("error") is None
    
    # Verify step 2
    step2 = history[1]
    assert step2["node_id"] == "step2"
    assert step2["inputs"]["result1"] == 15
    assert step2["outputs"]["result2"] == 30
    assert step2.get("error") is None
    
    # Verify step 3
    step3 = history[2]
    assert step3["node_id"] == "step3"
    assert step3["inputs"]["result2"] == 30
    assert step3["outputs"]["final"] == 35
    assert step3.get("error") is None
    
    # Verify final outputs in metadata
    assert "outputs" in result.metadata
    assert result.metadata["outputs"]["final"] == 35
    
    # Verify output text no longer expands the full execution history
    assert "Status: SUCCEEDED" in result.output
    assert "Final Outputs:" in result.output
    assert "Execution History" not in result.output
    assert "Inputs:" not in result.output
    assert "Stdout:" not in result.output


@pytest.mark.asyncio
async def test_workflow_history_with_error():
    """History is preserved in metadata even when execution fails."""
    
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
    
    # But history should still be available
    assert "history" in result.metadata
    history = result.metadata["history"]
    
    # Should have 2 steps (both executed, second one failed)
    assert len(history) == 2
    
    # First step should succeed
    step1 = history[0]
    assert step1["node_id"] == "step1"
    assert step1["outputs"]["value"] == 100
    assert step1.get("error") is None
    
    # Second step should have error
    step2 = history[1]
    assert step2["node_id"] == "step2"
    assert step2.get("error") is not None
    assert "Intentional error" in step2["error"]
    assert "traceback" in step2
    
    # Output should contain only the top-level failure summary
    assert "Error:" in result.output
    assert "Execution History" not in result.output
    assert "Inputs:" not in result.output
    assert "Stdout:" not in result.output


@pytest.mark.asyncio
async def test_workflow_history_with_stdout():
    """Stdout remains in metadata history even if hidden from tool output."""
    
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
    
    # Check history contains stdout
    history = result.metadata["history"]
    assert len(history) == 1
    
    step1 = history[0]
    assert "stdout" in step1
    assert "Hello from step1" in step1["stdout"]
    
    # Output should stay concise and omit per-step stdout details
    assert "Stdout:" not in result.output
    assert "Hello from step1" not in result.output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

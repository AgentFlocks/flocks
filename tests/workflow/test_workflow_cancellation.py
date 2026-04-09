"""Tests for cooperative workflow cancellation."""

from __future__ import annotations

import threading

from flocks.workflow.runner import run_workflow


def test_run_workflow_stops_after_cancel_signal() -> None:
    """Cancellation should stop the workflow before the next node runs."""
    workflow = {
        "name": "cancel-test-workflow",
        "start": "step1",
        "nodes": [
            {
                "id": "step1",
                "type": "python",
                "code": "outputs['value'] = 1",
            },
            {
                "id": "step2",
                "type": "python",
                "code": "outputs['value'] = inputs['value'] + 1",
            },
        ],
        "edges": [
            {"from": "step1", "to": "step2"},
        ],
    }
    cancel_event = threading.Event()

    def on_step_complete(_step_result) -> None:
        cancel_event.set()

    result = run_workflow(
        workflow=workflow,
        inputs={},
        ensure_requirements=False,
        trace=False,
        on_step_complete=on_step_complete,
        cancel=cancel_event.is_set,
    )

    assert result.status == "CANCELLED"
    assert len(result.history) == 1
    assert result.history[0]["node_id"] == "step1"
    assert result.outputs == {"value": 1}
    assert result.error is not None

"""High-level workflow execution lifecycle manager."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from flocks.workflow.execution_store import (
    compact_history_for_storage,
    compact_outputs_for_storage,
    create_execution_record,
    record_execution_result,
    resolve_execution_outcome,
)
from flocks.workflow.process_executor import (
    StepCompleteHook,
    StepStartHook,
    WorkerEventHook,
    run_workflow_process,
)
from flocks.workflow.runner import RunWorkflowResult


@dataclass
class WorkflowExecutionManager:
    """Run a workflow through the process worker and optionally persist summary."""

    async def run(
        self,
        *,
        workflow_id: str,
        workflow: Any,
        inputs: Optional[Dict[str, Any]] = None,
        exec_id: Optional[str] = None,
        timeout_s: Optional[float] = None,
        trace: bool = False,
        use_llm: Optional[bool] = None,
        ensure_requirements: bool = True,
        history_mode: str = "summary",
        retain_history: bool = False,
        tool_context: Optional[Any] = None,
        cancel: Optional[Callable[[], bool]] = None,
        on_step_start: Optional[StepStartHook] = None,
        on_step_complete: Optional[StepCompleteHook] = None,
        on_event: Optional[WorkerEventHook] = None,
        persist: bool = True,
    ) -> RunWorkflowResult:
        started = time.time()
        exec_data = None
        if persist:
            exec_data = await create_execution_record(
                workflow_id,
                input_params=inputs or {},
                exec_id=exec_id,
            )
        result = await run_workflow_process(
            workflow=workflow,
            inputs=inputs or {},
            timeout_s=timeout_s,
            trace=trace,
            use_llm=use_llm,
            ensure_requirements=ensure_requirements,
            history_mode=history_mode,
            retain_history=retain_history,
            tool_context=tool_context,
            cancel=cancel,
            on_step_start=on_step_start,
            on_step_complete=on_step_complete,
            on_event=on_event,
        )
        if persist and exec_data is not None:
            status_value, error_message = resolve_execution_outcome(result)
            exec_data.update(
                {
                    "status": status_value,
                    "outputResults": compact_outputs_for_storage(result.outputs),
                    "finishedAt": int(time.time() * 1000),
                    "duration": time.time() - started,
                    "executionLog": compact_history_for_storage(result.history),
                    "errorMessage": error_message,
                    "stepCount": result.steps,
                    "currentNodeId": result.last_node_id,
                    "currentPhase": status_value,
                    "currentStepIndex": result.steps,
                    "updatedAt": int(time.time() * 1000),
                }
            )
            await record_execution_result(workflow_id, str(exec_data["id"]), exec_data)
        return result


async def run_workflow_managed(
    *,
    workflow: Any,
    inputs: Optional[Dict[str, Any]] = None,
    workflow_id: Optional[str] = None,
    timeout_s: Optional[float] = None,
    trace: bool = False,
    use_llm: Optional[bool] = None,
    ensure_requirements: bool = True,
    history_mode: str = "summary",
    retain_history: bool = False,
    tool_context: Optional[Any] = None,
    cancel: Optional[Callable[[], bool]] = None,
    on_step_start: Optional[StepStartHook] = None,
    on_step_complete: Optional[StepCompleteHook] = None,
    on_event: Optional[WorkerEventHook] = None,
    persist: bool = False,
    **_: Any,
) -> RunWorkflowResult:
    workflow_id_value = workflow_id
    if workflow_id_value is None and isinstance(workflow, dict):
        workflow_id_value = str(workflow.get("id") or workflow.get("name") or "workflow")
    manager = WorkflowExecutionManager()
    return await manager.run(
        workflow_id=workflow_id_value or "workflow",
        workflow=workflow,
        inputs=inputs,
        timeout_s=timeout_s,
        trace=trace,
        use_llm=use_llm,
        ensure_requirements=ensure_requirements,
        history_mode=history_mode,
        retain_history=retain_history,
        tool_context=tool_context,
        cancel=cancel,
        on_step_start=on_step_start,
        on_step_complete=on_step_complete,
        on_event=on_event,
        persist=persist,
    )

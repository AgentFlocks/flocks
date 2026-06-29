"""High-level workflow execution lifecycle manager."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from flocks.utils.log import Log
from flocks.workflow.execution_store import (
    compact_execution_summary,
    compact_history_for_storage,
    compact_outputs_for_storage,
    compact_step_for_storage,
    create_execution_record,
    derive_loop_progress,
    record_execution_step,
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
from flocks.workflow.store import WorkflowStore

log = Log.create(service="workflow.execution_manager")


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
        loop = asyncio.get_running_loop()
        started = time.time()
        exec_data = None
        step_count = 0
        pending_step_index: Optional[int] = None
        pending_step: Optional[Dict[str, Any]] = None
        if persist:
            exec_data = await create_execution_record(
                workflow_id,
                input_params=inputs or {},
                exec_id=exec_id,
            )

        def _write_progress(update_fields: Dict[str, Any]) -> None:
            if exec_data is None:
                return
            try:
                exec_data.update(update_fields)
                asyncio.run_coroutine_threadsafe(
                    WorkflowStore.upsert_execution(compact_execution_summary(exec_data)),
                    loop,
                ).result(timeout=5)
            except Exception as exc:
                log.warning(
                    "workflow.execution_manager.progress_write_failed",
                    {
                        "workflow_id": workflow_id,
                        "exec_id": exec_data.get("id") if exec_data else None,
                        "error": str(exc),
                    },
                )

        def _project_step_start(run_id: Optional[str], step_index: int, node: Any, step_inputs: Dict[str, Any]) -> Any:
            nonlocal pending_step_index, pending_step
            node_id = getattr(node, "id", None)
            node_type = getattr(node, "type", None)
            loop_progress = derive_loop_progress(
                node_id=node_id,
                global_step_index=step_index,
                inputs=step_inputs,
                outputs=None,
            )
            pending_step_index = step_index
            pending_step = {
                "node_id": node_id,
                "node_type": node_type,
                "inputs": step_inputs if isinstance(step_inputs, dict) else {},
                "outputs": {},
                "error": "Run cancelled before node completed",
            }
            _write_progress(
                {
                    "currentNodeId": node_id,
                    "currentNodeType": node_type,
                    "currentPhase": "running",
                    "currentStepIndex": step_index,
                    "loopProgress": loop_progress,
                    "runId": run_id,
                    "updatedAt": int(time.time() * 1000),
                }
            )
            if on_step_start is not None:
                return on_step_start(run_id, step_index, node, step_inputs)
            return step_index

        def _project_step_complete(step_result: Any) -> None:
            nonlocal step_count, pending_step_index, pending_step
            raw_step = step_result.model_dump(mode="json") if hasattr(step_result, "model_dump") else step_result
            step_dict = compact_step_for_storage(raw_step)
            if not isinstance(step_dict, dict):
                if on_step_complete is not None:
                    on_step_complete(step_result)
                return
            step_count += 1
            pending_step_index = None
            pending_step = None
            loop_progress = derive_loop_progress(
                node_id=step_dict.get("node_id"),
                global_step_index=step_count,
                inputs=step_dict.get("inputs"),
                outputs=step_dict.get("outputs"),
            )
            _write_progress(
                {
                    "stepCount": step_count,
                    "currentNodeId": step_dict.get("node_id"),
                    "currentNodeType": step_dict.get("node_type") or step_dict.get("type"),
                    "currentPhase": "running",
                    "currentStepIndex": step_count,
                    "loopProgress": loop_progress,
                    "updatedAt": int(time.time() * 1000),
                }
            )
            if exec_data is not None:
                try:
                    asyncio.run_coroutine_threadsafe(
                        record_execution_step(str(exec_data["id"]), step_count, step_dict),
                        loop,
                    ).result(timeout=5)
                except Exception as exc:
                    log.warning(
                        "workflow.execution_manager.step_write_failed",
                        {
                            "workflow_id": workflow_id,
                            "exec_id": exec_data.get("id"),
                            "step_index": step_count,
                            "error": str(exc),
                        },
                    )
            if on_step_complete is not None:
                on_step_complete(step_result)

        effective_on_step_start = _project_step_start if persist and exec_data is not None else on_step_start
        effective_on_step_complete = _project_step_complete if persist and exec_data is not None else on_step_complete

        result = await run_workflow_process(
            workflow=workflow,
            inputs=inputs or {},
            workflow_id=workflow_id,
            timeout_s=timeout_s,
            trace=trace,
            use_llm=use_llm,
            ensure_requirements=ensure_requirements,
            history_mode=history_mode,
            retain_history=retain_history,
            tool_context=tool_context,
            cancel=cancel,
            on_step_start=effective_on_step_start,
            on_step_complete=effective_on_step_complete,
            on_event=on_event,
        )
        if persist and exec_data is not None:
            status_value, error_message = resolve_execution_outcome(result)
            final_history = compact_history_for_storage(result.history)
            if pending_step_index is not None and pending_step is not None and not final_history:
                await record_execution_step(str(exec_data["id"]), pending_step_index, pending_step)
            final_steps = max(result.steps, step_count)
            if pending_step_index is not None:
                final_steps = max(final_steps, pending_step_index)
            exec_data.update(
                {
                    "status": status_value,
                    "outputResults": compact_outputs_for_storage(result.outputs),
                    "finishedAt": int(time.time() * 1000),
                    "duration": time.time() - started,
                    "executionLog": final_history,
                    "errorMessage": error_message,
                    "stepCount": final_steps,
                    "currentNodeId": result.last_node_id,
                    "currentPhase": status_value,
                    "currentStepIndex": final_steps,
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

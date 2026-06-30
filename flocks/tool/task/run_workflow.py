"""
Run Workflow Tool - Execute workflows using flocks-workflow runtime

Executes workflow definitions in-process, supporting automatic dependency
installation and structured result reporting.
"""

import asyncio
import json
import inspect
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Optional, Dict, Any, Union

from flocks.tool.registry import ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
from flocks.utils.log import Log
from flocks.session.recorder import Recorder
from flocks.workflow.execution_store import (
    compact_history_for_storage,
    compact_execution_summary,
    compact_outputs_for_storage,
    compact_step_for_storage,
    create_execution_record,
    derive_loop_progress,
    normalize_execution_status,
    record_execution_step,
    record_execution_result,
    resolve_execution_outcome,
)
from flocks.workflow.fs_store import read_workflow_from_fs, resolve_workflow_id_from_source
from flocks.workflow.store import WorkflowStore


log = Log.create(service="tool.run_workflow")

_PROGRESS_FLUSH_EVERY_STEPS = 5

# Lazy import to avoid circular import (flocks.tool <-> flocks.workflow)
_WORKFLOW_AVAILABLE: Optional[bool] = None
RequirementsInstaller = None
RunWorkflowResult = None
_run_workflow = None


def _get_workflow_runtime():
    """Import workflow runtime on first use (avoids circular import at tool registration)."""
    global _WORKFLOW_AVAILABLE, RequirementsInstaller, _run_workflow, RunWorkflowResult
    if _WORKFLOW_AVAILABLE is False:
        return None, None, None
    try:
        # Prefer in-repo integration (flocks.workflow). Fallback to external package if installed.
        from flocks.workflow import (
            RequirementsInstaller as _ReqInstaller,
            run_workflow as _run,
        )
        from flocks.workflow.runner import RunWorkflowResult as _Result

        RequirementsInstaller = _ReqInstaller
        _run_workflow = _run
        RunWorkflowResult = _Result
        _WORKFLOW_AVAILABLE = True
        return RequirementsInstaller, _run_workflow, RunWorkflowResult
    except ImportError as e:
        try:
            from flocks_workflow import (  # pyright: ignore[reportMissingImports]
                RequirementsInstaller as _ReqInstaller,
                run_workflow as _run,
                RunWorkflowResult as _Result,
            )

            RequirementsInstaller = _ReqInstaller
            _run_workflow = _run
            RunWorkflowResult = _Result
            _WORKFLOW_AVAILABLE = True
            return RequirementsInstaller, _run_workflow, RunWorkflowResult
        except ImportError as e2:
            _WORKFLOW_AVAILABLE = False
            log.warn("run_workflow.import_failed", {"message": str(e), "fallback_message": str(e2)})
            return None, None, None


async def _call_workflow_runtime(runtime_fn, call_kwargs: Dict[str, Any]):
    """Call either the async process executor or a legacy sync workflow runtime."""
    if inspect.iscoroutinefunction(runtime_fn):
        return await runtime_fn(**call_kwargs)
    result = await asyncio.to_thread(runtime_fn, **call_kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


_BASE_DESCRIPTION = """Execute a workflow definition using the flocks-workflow runtime.

When to use:
- You need to execute a workflow.
- You have an existing JSON/dict structure or a workflow JSON file and user request to execute it.
- Execute workflow when workflow has been generated.

How to use:
- Provide the workflow definition (dictionary, JSON string, or file path).
- The workflow file path should be an absolute path. IMPORTANT: In JSON, file paths must be quoted strings (e.g. "workflow": "/path/to/workflow.json"). Unquoted paths will cause parse errors.
- Parameters: Provide input parameters, timeout settings. If inputs are not provided, ask user to provide them.

Note:
- This tool depends on an existing workflow file.
- If no workflow file exists, ask user to specify the workflow file path or use the `workflow-builder` skill to create."""

DESCRIPTION = _BASE_DESCRIPTION

# TTL cache for _build_description — avoid repeated file-system scans on every tool call.
_DESCRIPTION_CACHE: Optional[str] = None
_DESCRIPTION_CACHE_AT: float = 0.0
_DESCRIPTION_CACHE_TTL: float = 60.0  # seconds


def _resolve_workflow_display_name_and_total_nodes(
    workflow_source: Union[Dict[str, Any], Path],
) -> tuple[str, Optional[int]]:
    """Return a friendly workflow name and node count when cheaply available."""
    if isinstance(workflow_source, dict):
        workflow_name = str(workflow_source.get("name") or "").strip() or "unnamed workflow"
        nodes = workflow_source.get("nodes")
        total_nodes = len(nodes) if isinstance(nodes, list) else None
        return workflow_name, total_nodes

    workflow_name = workflow_source.stem or workflow_source.name
    try:
        raw = json.loads(workflow_source.read_text(encoding="utf-8"))
    except Exception:
        return workflow_name, None

    if isinstance(raw, dict):
        loaded_name = str(raw.get("name") or "").strip()
        if loaded_name:
            workflow_name = loaded_name
        nodes = raw.get("nodes")
        if isinstance(nodes, list):
            return workflow_name, len(nodes)
    return workflow_name, None


def _create_nested_tool_context(ctx: ToolContext) -> ToolContext:
    """Create an isolated child ToolContext for workflow node tools.

    Workflow execution itself should surface workflow-level progress via
    ``run_workflow`` metadata. Reusing the outer tool context inside workflow
    nodes lets nested tools (notably ``task``) overwrite that metadata with
    child-session progress, which makes the UI misclassify the outer workflow
    card as a delegate/subagent card.

    We therefore clone the context but intentionally omit the metadata
    callback, while preserving permissions, event publishing, abort signal, and
    sandbox/session identity.
    """
    return ToolContext(
        session_id=ctx.session_id,
        message_id=ctx.message_id,
        agent=ctx.agent,
        call_id=ctx.call_id,
        extra=dict(ctx.extra),
        abort_event=ctx.abort,
        permission_callback=ctx._permission_callback,
        metadata_callback=None,
        event_publish_callback=ctx.event_publish_callback,
    )


async def _build_description() -> str:
    """Build dynamic description with available workflows list (TTL-cached, 60 s)."""
    global _DESCRIPTION_CACHE, _DESCRIPTION_CACHE_AT
    now = time.monotonic()
    if _DESCRIPTION_CACHE is not None and now - _DESCRIPTION_CACHE_AT < _DESCRIPTION_CACHE_TTL:
        return _DESCRIPTION_CACHE

    try:
        from flocks.workflow.center import scan_skill_workflows

        entries = await scan_skill_workflows()
        if not entries:
            result = _BASE_DESCRIPTION
        else:
            parts = [_BASE_DESCRIPTION, "", "<available_workflows>"]
            for entry in entries:
                name = entry.get("name") or "(unnamed)"
                desc = entry.get("description") or ""
                path = entry.get("workflowPath") or ""
                source = entry.get("sourceType") or "project"
                parts.append("  <workflow>")
                parts.append(f"    <name>{name}</name>")
                if desc:
                    parts.append(f"    <description>{desc}</description>")
                parts.append(f"    <path>{path}</path>")
                parts.append(f"    <scope>{source}</scope>")
                parts.append("  </workflow>")
            parts.append("</available_workflows>")
            result = "\n".join(parts)
    except Exception:
        result = _BASE_DESCRIPTION

    _DESCRIPTION_CACHE = result
    _DESCRIPTION_CACHE_AT = now
    return result


def _format_workflow_result(result: Any, *, include_history: bool = False) -> str:
    """Format RunWorkflowResult or dict as readable output.

    By default the returned text omits step-by-step execution history so
    session tool output stays concise. The structured history remains
    available in metadata and persisted execution records.
    """
    if hasattr(result, "__dict__"):
        # RunWorkflowResult object
        data = result.__dict__
    elif isinstance(result, dict):
        data = result
    else:
        return str(result)

    output_lines = []
    output_lines.append(f"Status: {data.get('status', 'UNKNOWN')}")

    if data.get("run_id"):
        output_lines.append(f"Run ID: {data.get('run_id')}")

    if data.get("steps"):
        output_lines.append(f"Steps executed: {data.get('steps')}")

    if data.get("last_node_id"):
        output_lines.append(f"Last node: {data.get('last_node_id')}")

    if data.get("error"):
        output_lines.append(f"\nError: {data.get('error')}")

    if data.get("outputs"):
        output_lines.append("\nFinal Outputs:")
        try:
            outputs_str = json.dumps(data.get("outputs"), indent=2, ensure_ascii=False)
            output_lines.append(outputs_str)
        except Exception:
            output_lines.append(str(data.get("outputs")))

    if include_history and data.get("history"):
        history = data.get("history", [])
        if history:
            output_lines.append(f"\n{'=' * 80}")
            output_lines.append(f"Execution History ({len(history)} steps):")
            output_lines.append("=" * 80)

            for i, step in enumerate(history, 1):
                node_id = step.get("node_id", "unknown")
                duration_ms = step.get("duration_ms")
                error = step.get("error")

                output_lines.append(f"\n[Step {i}] Node: {node_id}")
                if duration_ms is not None:
                    output_lines.append(f"  Duration: {duration_ms:.2f}ms")

                # Show inputs
                inputs = step.get("inputs", {})
                if inputs:
                    output_lines.append("  Inputs:")
                    try:
                        inputs_str = json.dumps(inputs, indent=4, ensure_ascii=False)
                        for line in inputs_str.split("\n"):
                            output_lines.append(f"    {line}")
                    except Exception:
                        output_lines.append(f"    {str(inputs)}")

                # Show outputs
                outputs = step.get("outputs", {})
                if outputs:
                    output_lines.append("  Outputs:")
                    try:
                        outputs_str = json.dumps(outputs, indent=4, ensure_ascii=False)
                        for line in outputs_str.split("\n"):
                            output_lines.append(f"    {line}")
                    except Exception:
                        output_lines.append(f"    {str(outputs)}")

                # Show stdout if present
                stdout = step.get("stdout", "")
                if stdout:
                    output_lines.append("  Stdout:")
                    for line in stdout.split("\n"):
                        output_lines.append(f"    {line}")

                # Show error if present
                if error:
                    output_lines.append(f"  Error: {error}")
                    traceback_info = step.get("traceback", "")
                    if traceback_info:
                        output_lines.append("  Traceback:")
                        for line in traceback_info.split("\n"):
                            output_lines.append(f"    {line}")

            output_lines.append(f"\n{'=' * 80}")

    return "\n".join(output_lines)


async def _record_workflow_tool_result(workflow_id: str, result: Any) -> None:
    """Record workflow tool execution to JSONL (best-effort)."""
    try:
        if hasattr(result, "__dict__"):
            data = result.__dict__
        elif isinstance(result, dict):
            data = result
        else:
            data = {"status": "unknown", "outputs": str(result)}
        exec_id = str(data.get("run_id") or data.get("runId") or "unknown").strip() or "unknown"
        await Recorder.record_workflow_execution(exec_id=exec_id, workflow_id=workflow_id, run_result=data)
    except Exception:
        return


@ToolRegistry.register_function(
    name="run_workflow",
    description=DESCRIPTION,
    category=ToolCategory.SYSTEM,
    requires_confirmation=True,
    parameters=[
        ToolParameter(
            name="workflow",
            type=ParameterType.OBJECT,
            description="Workflow definition (dict). If passing a string, provide a JSON string or a workflow JSON file path.",
            required=True,
            json_schema={
                "anyOf": [
                    {
                        "type": "object",
                        "description": "Workflow definition as an object (dict)",
                    },
                    {
                        "type": "string",
                        "description": "Workflow JSON string or a workflow JSON file path",
                    },
                ],
            },
        ),
        ToolParameter(
            name="inputs",
            type=ParameterType.OBJECT,
            description="Input parameters for the workflow execution",
            required=False,
            default={},
            json_schema={
                "type": "object",
                "additionalProperties": True,
            },
        ),
        ToolParameter(
            name="use_llm",
            type=ParameterType.BOOLEAN,
            description=(
                'Enable LLM-backed code generation for `type="logic"` nodes (when code is missing). '
                "Recommended to keep enabled for logic-node workflows."
            ),
            required=False,
            default=True,
        ),
        ToolParameter(
            name="ensure_requirements",
            type=ParameterType.BOOLEAN,
            description="Whether to automatically install requirements declared in workflow metadata",
            required=False,
            default=True,
        ),
        ToolParameter(
            name="timeout_s",
            type=ParameterType.NUMBER,
            description="Execution timeout in seconds (optional)",
            required=False,
        ),
        ToolParameter(
            name="trace",
            type=ParameterType.BOOLEAN,
            description="Enable execution tracing for debugging",
            required=False,
            default=False,
        ),
    ],
)
async def run_workflow_tool(
    ctx: ToolContext,
    workflow: Union[Dict[str, Any], str],
    inputs: Optional[Dict[str, Any]] = None,
    use_llm: bool = True,
    ensure_requirements: bool = True,
    timeout_s: Optional[float] = None,
    trace: bool = False,
) -> ToolResult:
    """
    Execute a workflow using flocks-workflow runtime

    Args:
        ctx: Tool context
        workflow: Workflow definition (dict), JSON string, or a workflow JSON file path
        inputs: Input parameters for workflow execution
        use_llm: Enable LLM-backed code generation for logic nodes
        ensure_requirements: Whether to install requirements automatically
        timeout_s: Execution timeout in seconds
        trace: Enable execution tracing

    Returns:
        ToolResult with workflow execution results
    """
    # Update tool description with available workflows on each call (like the skill_load tool)
    tool = ToolRegistry.get("run_workflow")
    if tool:
        tool.info.description = await _build_description()

    req_installer, _run_workflow_fn, RunWorkflowResultCls = _get_workflow_runtime()
    if _run_workflow_fn is None or RunWorkflowResultCls is None:
        return ToolResult(success=False, error="flocks-workflow package is not available. Please check code")

    # Validate workflow parameter
    if not workflow:
        return ToolResult(success=False, error="workflow parameter is required")

    # Accept workflow as dict, JSON string, or file path.
    workflow_source: Union[Dict[str, Any], Path]
    registered_workflow_id: Optional[str] = None
    if isinstance(workflow, str):
        raw = workflow.strip()
        # Try to parse as JSON first (handles JSON-encoded dicts or strings).
        parsed = None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            pass

        if isinstance(parsed, dict):
            # Valid workflow JSON object.
            workflow_source = parsed
        elif isinstance(parsed, str):
            # json.loads decoded a JSON-encoded string, e.g. the AI double-encoded the
            # path: workflow='"/path/to/workflow.json"' → parsed='/path/to/workflow.json'.
            # Use the decoded string (no surrounding quotes) as the file path.
            p = Path(parsed).expanduser()
            if p.exists() and p.is_file():
                workflow_source = p
            else:
                return ToolResult(
                    success=False,
                    error=(
                        f"Workflow file not found: {parsed!r}. "
                        "Provide a valid workflow JSON file path or a workflow dict."
                    ),
                )
        elif parsed is None:
            # json.loads raised JSONDecodeError — raw is not JSON.
            # First try to resolve as a registered workflow ID, then fall back to file path.
            existing_workflow = read_workflow_from_fs(raw)
            if existing_workflow is not None:
                workflow_source = dict(existing_workflow["workflowJson"])
                registered_workflow_id = str(existing_workflow["id"])
                workflow_source["id"] = registered_workflow_id
                raw = registered_workflow_id
            else:
                p = Path(raw).expanduser()
                if p.exists() and p.is_file():
                    workflow_source = p
                else:
                    return ToolResult(
                        success=False,
                        error=(
                            "Unsupported workflow string. Provide a workflow ID, workflow JSON string, "
                            "or a valid workflow JSON file path."
                        ),
                    )
        else:
            # json.loads returned list / int / bool — not a valid workflow parameter.
            return ToolResult(
                success=False,
                error=(
                    f"Invalid workflow parameter: expected a workflow dict or a file path string, "
                    f"got JSON-decoded {type(parsed).__name__} ({parsed!r})."
                ),
            )
    elif isinstance(workflow, dict):
        workflow_source = workflow
    else:
        return ToolResult(
            success=False, error=f"workflow must be a dictionary or string, got {type(workflow).__name__}"
        )

    # Sanity-check dict workflows: must have at least a `start` field so we
    # surface a clear error instead of a confusing Pydantic validation message.
    if isinstance(workflow_source, dict) and "start" not in workflow_source:
        return ToolResult(
            success=False,
            error=(
                "Invalid workflow definition: the `start` field is required. "
                "Make sure you pass the workflow JSON (with `start`, `nodes`, `edges`) "
                "as the `workflow` parameter, not the execution inputs."
            ),
        )

    # Request permission (workflow execution can run arbitrary code)
    workflow_name, workflow_total_nodes = _resolve_workflow_display_name_and_total_nodes(workflow_source)

    if isinstance(workflow_source, dict):
        # Use id if available, otherwise use name or generate a fallback
        workflow_id = workflow_source.get("id") or workflow_source.get("name") or "unknown"
    else:
        workflow_id = str(workflow_source)

    workflow_inputs = inputs or {}
    canonical_workflow_id = registered_workflow_id or resolve_workflow_id_from_source(workflow_source)
    display_workflow_id = canonical_workflow_id or workflow_id
    tracked_execution: Optional[Dict[str, Any]] = None
    tracked_step_count = 0
    pending_step_index: Optional[int] = None
    pending_step: Optional[Dict[str, Any]] = None
    loop = asyncio.get_running_loop()

    def _emit_metadata(metadata: Dict[str, Any]) -> None:
        loop.call_soon_threadsafe(ctx.metadata, metadata)

    def _update_execution_progress(update_fields: Dict[str, Any]) -> None:
        try:
            if tracked_execution is None:
                return
            tracked_execution.update(update_fields)
            asyncio.run_coroutine_threadsafe(
                WorkflowStore.upsert_execution(compact_execution_summary(tracked_execution)),
                loop,
            ).result(timeout=5)
        except Exception as exc:
            log.warning(
                "run_workflow.execution_progress.write_failed",
                {
                    "workflow_id": display_workflow_id,
                    "exec_id": tracked_execution["id"] if tracked_execution else None,
                    "error": str(exc),
                },
            )

    def _on_step_start(
        run_id: Optional[str],
        step_index: int,
        node: Any,
        _inputs: Dict[str, Any],
    ) -> int:
        nonlocal pending_step_index, pending_step
        current_node_id = getattr(node, "id", None)
        current_node_type = getattr(node, "type", None)
        loop_progress = derive_loop_progress(
            node_id=current_node_id,
            global_step_index=step_index,
            inputs=_inputs,
            outputs=None,
        )
        pending_step_index = step_index
        pending_step = {
            "node_id": current_node_id,
            "node_type": current_node_type,
            "inputs": _inputs if isinstance(_inputs, dict) else {},
            "outputs": {},
            "error": "Run cancelled before node completed",
        }
        if tracked_execution is not None:
            _update_execution_progress(
                {
                    "currentNodeId": current_node_id,
                    "currentNodeType": current_node_type,
                    "currentPhase": "running",
                    "currentStepIndex": step_index,
                    "loopProgress": loop_progress,
                    "updatedAt": int(time.time() * 1000),
                }
            )
        _emit_metadata(
            {
                "title": f"Running workflow: {workflow_name}",
                "metadata": {
                    "workflow_id": display_workflow_id,
                    "workflow_name": workflow_name,
                    "total_nodes": workflow_total_nodes,
                    "workflow_execution_id": tracked_execution["id"] if tracked_execution else None,
                    "run_id": run_id,
                    "status": "running",
                    "phase": "running",
                    "current_node_id": current_node_id,
                    "current_node_type": current_node_type,
                    "step_index": step_index,
                    "loop_progress": loop_progress,
                },
            }
        )
        return step_index

    def _on_step_complete(step_result: Any) -> None:
        nonlocal tracked_step_count, pending_step_index, pending_step
        if hasattr(step_result, "model_dump"):
            step_dict = step_result.model_dump(mode="json")
        elif isinstance(step_result, dict):
            step_dict = dict(step_result)
        else:
            step_dict = {"node_id": None, "outputs": {}, "error": str(step_result)}
        step_index = tracked_step_count + 1
        compacted_step = compact_step_for_storage(step_dict)
        pending_step_index = None
        pending_step = None
        loop_progress = derive_loop_progress(
            node_id=step_dict.get("node_id"),
            global_step_index=step_index,
            inputs=step_dict.get("inputs"),
            outputs=step_dict.get("outputs"),
        )
        tracked_step_count = step_index
        if tracked_execution is not None:
            tracked_execution.update(
                {
                    "stepCount": tracked_step_count,
                    "currentNodeId": step_dict.get("node_id"),
                    "currentNodeType": step_dict.get("node_type") or step_dict.get("type"),
                    "currentPhase": "running",
                    "currentStepIndex": tracked_step_count,
                    "loopProgress": loop_progress,
                    "updatedAt": int(time.time() * 1000),
                }
            )
        if tracked_execution is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    record_execution_step(tracked_execution["id"], step_index, compacted_step),
                    loop,
                ).result(timeout=5)
            except Exception as exc:
                log.warning(
                    "run_workflow.execution_step.write_failed",
                    {
                        "workflow_id": display_workflow_id,
                        "exec_id": tracked_execution["id"],
                        "step_index": step_index,
                        "error": str(exc),
                    },
                )
        if tracked_step_count % _PROGRESS_FLUSH_EVERY_STEPS == 0:
            _update_execution_progress(
                {
                    "stepCount": tracked_step_count,
                    "currentNodeId": step_dict.get("node_id"),
                    "currentNodeType": step_dict.get("node_type") or step_dict.get("type"),
                    "currentPhase": "running",
                    "currentStepIndex": tracked_step_count,
                    "loopProgress": loop_progress,
                    "updatedAt": int(time.time() * 1000),
                }
            )
        _emit_metadata(
            {
                "title": f"Running workflow: {workflow_name}",
                "metadata": {
                    "workflow_id": display_workflow_id,
                    "workflow_name": workflow_name,
                    "total_nodes": workflow_total_nodes,
                    "workflow_execution_id": tracked_execution["id"] if tracked_execution else None,
                    "status": "running",
                    "phase": "running",
                    "current_node_id": step_dict.get("node_id"),
                    "current_node_type": step_dict.get("node_type") or step_dict.get("type"),
                    "step_index": tracked_step_count,
                    "step_count": tracked_step_count,
                    "loop_progress": loop_progress,
                },
            }
        )
        return

    async def _flush_pending_step() -> None:
        if tracked_execution is None or pending_step_index is None or pending_step is None:
            return
        try:
            await record_execution_step(
                tracked_execution["id"],
                pending_step_index,
                pending_step,
            )
        except Exception as exc:
            log.warning(
                "run_workflow.pending_step.write_failed",
                {
                    "workflow_id": display_workflow_id,
                    "exec_id": tracked_execution["id"],
                    "step_index": pending_step_index,
                    "error": str(exc),
                },
            )

    await ctx.ask(
        permission="run_workflow",
        patterns=[workflow_id, workflow_name],
        always=["*"],
        metadata={
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "ensure_requirements": ensure_requirements,
            "use_llm": use_llm,
        },
    )

    if canonical_workflow_id:
        tracked_execution = await create_execution_record(
            canonical_workflow_id,
            input_params=workflow_inputs,
        )

    # Update metadata to show workflow is running
    _emit_metadata(
        {
            "title": f"Running workflow: {workflow_name}",
            "metadata": {
                "workflow_id": display_workflow_id,
                "workflow_name": workflow_name,
                "total_nodes": workflow_total_nodes,
                "workflow_execution_id": tracked_execution["id"] if tracked_execution else None,
                "status": "running",
                "phase": "queued",
                "step_index": 0,
            },
        }
    )

    try:
        # Execute workflow
        log.info(
            "run_workflow.execute.start",
            {
                "workflow_id": workflow_id,
                "workflow_name": workflow_name,
                "ensure_requirements": ensure_requirements,
            },
        )
        execution_started_at = time.time()

        nested_tool_ctx = _create_nested_tool_context(ctx)
        call_kwargs: Dict[str, Any] = {
            "workflow": workflow_source,
            "inputs": workflow_inputs,
            "ensure_requirements": ensure_requirements,
            "requirements_installer": (
                req_installer(installer="auto") if ensure_requirements and req_installer else None
            ),
            "timeout_s": timeout_s,
            "trace": trace,
            "tool_context": nested_tool_ctx,
        }

        # Backward-compatibility: older runtimes may not accept `use_llm`.
        supports_workflow_id = False
        supports_use_llm = False
        supports_step_start = False
        supports_cancel = False
        try:
            sig = inspect.signature(_run_workflow_fn)
            supports_workflow_id = "workflow_id" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
            supports_use_llm = "use_llm" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
            supports_step_start = "on_step_start" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
            supports_cancel = "cancel" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
        except Exception:
            # Best-effort: assume supported.
            supports_workflow_id = True
            supports_use_llm = True
            supports_step_start = True
            supports_cancel = True

        if supports_workflow_id:
            call_kwargs["workflow_id"] = display_workflow_id
        if supports_use_llm:
            call_kwargs["use_llm"] = use_llm
        if supports_step_start:
            call_kwargs["on_step_start"] = _on_step_start
        call_kwargs["on_step_complete"] = _on_step_complete
        if supports_cancel:
            call_kwargs["cancel"] = ctx.abort.is_set

        try:
            result = await _call_workflow_runtime(_run_workflow_fn, call_kwargs)
        except TypeError as te:
            # Fallback if the runtime rejects `use_llm` (unexpected keyword).
            if supports_workflow_id and "workflow_id" in str(te):
                call_kwargs.pop("workflow_id", None)
                result = await _call_workflow_runtime(_run_workflow_fn, call_kwargs)
            elif supports_use_llm and "use_llm" in str(te):
                call_kwargs.pop("use_llm", None)
                result = await _call_workflow_runtime(_run_workflow_fn, call_kwargs)
            elif supports_step_start and "on_step_start" in str(te):
                call_kwargs.pop("on_step_start", None)
                result = await _call_workflow_runtime(_run_workflow_fn, call_kwargs)
            elif supports_cancel and "cancel" in str(te):
                call_kwargs.pop("cancel", None)
                result = await _call_workflow_runtime(_run_workflow_fn, call_kwargs)
            else:
                raise

        # Format result
        if RunWorkflowResultCls and isinstance(result, RunWorkflowResultCls):
            result_dict = result.__dict__
        elif isinstance(result, dict):
            result_dict = result
        else:
            result_dict = {"status": "UNKNOWN", "output": str(result)}

        status = result_dict.get("status", "UNKNOWN")
        success = status == "SUCCEEDED"
        error = result_dict.get("error")

        output = _format_workflow_result(result_dict)

        log.info(
            "run_workflow.execute.complete",
            {
                "workflow_id": workflow_id,
                "status": status,
                "success": success,
                "steps": result_dict.get("steps", 0),
            },
        )

        # Append-only recording for audit/replay
        await _record_workflow_tool_result(display_workflow_id, result_dict)

        status_value = normalize_execution_status(status)
        compacted_history = compact_history_for_storage(result_dict.get("history"))
        history_count = len(compacted_history)
        if status_value == "cancelled" and not compacted_history:
            await _flush_pending_step()
        final_step_count = result_dict.get("steps")
        if not isinstance(final_step_count, int):
            final_step_count = tracked_step_count
        if pending_step_index is not None:
            final_step_count = max(final_step_count, pending_step_index)
        if tracked_execution and canonical_workflow_id:
            current_data = dict(tracked_execution)
            outcome_result = result
            if not hasattr(outcome_result, "status"):
                outcome_result = SimpleNamespace(
                    status=result_dict.get("status"),
                    outputs=result_dict.get("outputs", {}),
                    error=result_dict.get("error"),
                )
            status_value, error_message = resolve_execution_outcome(outcome_result)  # type: ignore[arg-type]
            current_data.update(
                {
                    "outputResults": compact_outputs_for_storage(result_dict.get("outputs")),
                    "status": status_value,
                    "finishedAt": int(time.time() * 1000),
                    "duration": time.time() - execution_started_at,
                    "executionLog": compacted_history,
                    "stepCount": final_step_count,
                    "errorMessage": error_message,
                    "currentNodeId": result_dict.get("last_node_id"),
                    "currentPhase": status_value,
                    "currentStepIndex": final_step_count,
                    "updatedAt": int(time.time() * 1000),
                }
            )
            await record_execution_result(
                canonical_workflow_id,
                tracked_execution["id"],
                current_data,
            )
            _emit_metadata(
                {
                    "title": f"Workflow: {workflow_name}",
                    "metadata": {
                        "workflow_id": canonical_workflow_id,
                        "workflow_name": workflow_name,
                        "total_nodes": workflow_total_nodes,
                        "workflow_execution_id": tracked_execution["id"],
                        "run_id": result_dict.get("run_id"),
                        "status": status_value,
                        "phase": status_value,
                        "current_node_id": result_dict.get("last_node_id"),
                        "step_index": final_step_count,
                        "step_count": final_step_count,
                        "loop_progress": current_data.get("loopProgress"),
                    },
                }
            )

        compacted_outputs = compact_outputs_for_storage(result_dict.get("outputs"))

        # If workflow failed, include error in ToolResult
        if not success and error:
            return ToolResult(
                success=False,
                error=error,
                output=output,  # Also include formatted output for context
                title=f"Workflow: {workflow_name}",
                metadata={
                    "workflow_id": display_workflow_id,
                    "workflow_name": workflow_name,
                    "total_nodes": workflow_total_nodes,
                    "workflow_execution_id": tracked_execution["id"] if tracked_execution else None,
                    "status": status_value,
                    "steps": result_dict.get("steps", 0),
                    "run_id": result_dict.get("run_id"),
                    "last_node_id": result_dict.get("last_node_id"),
                    "outputs": compacted_outputs,
                    "history": [],
                    "history_count": history_count,
                },
            )

        return ToolResult(
            success=success,
            output=output,
            title=f"Workflow: {workflow_name}",
            metadata={
                "workflow_id": display_workflow_id,
                "workflow_name": workflow_name,
                "total_nodes": workflow_total_nodes,
                "workflow_execution_id": tracked_execution["id"] if tracked_execution else None,
                "status": status_value,
                "steps": result_dict.get("steps", 0),
                "run_id": result_dict.get("run_id"),
                "last_node_id": result_dict.get("last_node_id"),
                "outputs": compacted_outputs,
                "history": [],
                "history_count": history_count,
            },
        )

    except Exception as e:
        error_msg = str(e)
        log.error(
            "run_workflow.execute.error",
            {
                "workflow_id": workflow_id,
                "error": error_msg,
            },
        )
        if tracked_execution and canonical_workflow_id:
            current_data = dict(tracked_execution)
            current_data.update(
                {
                    "status": "error",
                    "finishedAt": int(time.time() * 1000),
                    "errorMessage": error_msg,
                    "executionLog": [],
                    "stepCount": tracked_step_count,
                    "currentPhase": "error",
                    "currentStepIndex": tracked_step_count,
                    "updatedAt": int(time.time() * 1000),
                }
            )
            await record_execution_result(
                canonical_workflow_id,
                tracked_execution["id"],
                current_data,
            )
            _emit_metadata(
                {
                    "title": f"Workflow: {workflow_name}",
                    "metadata": {
                        "workflow_id": canonical_workflow_id,
                        "workflow_name": workflow_name,
                        "total_nodes": workflow_total_nodes,
                        "workflow_execution_id": tracked_execution["id"],
                        "status": "error",
                        "phase": "error",
                        "step_index": tracked_step_count,
                    },
                }
            )

        return ToolResult(
            success=False,
            error=f"Workflow execution failed: {error_msg}",
            title=f"Workflow: {workflow_name}",
            metadata={
                "workflow_id": display_workflow_id,
                "workflow_name": workflow_name,
                "total_nodes": workflow_total_nodes,
                "workflow_execution_id": tracked_execution["id"] if tracked_execution else None,
                "status": "FAILED",
            },
        )

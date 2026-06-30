"""High-level workflow runner for host integration."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Literal, Optional, Union

from flocks.config.config import Config
from flocks.sandbox.context import resolve_sandbox_context
from .errors import FlocksWorkflowError, RunCancelledError, RunTimeoutError, WorkflowValidationError
from .io import dump_workflow, load_workflow
from .compiler import default_exec_path, compile_workflow, workflow_has_logic_nodes
from .models import Workflow
from .engine import WorkflowEngine
from .repl_runtime import PythonExecRuntime, SandboxPythonExecRuntime
from .tools import get_tool_registry
from .requirements import (
    RequirementsInstaller,
    SandboxRequirementsInstaller,
    requirements_from_workflow_metadata,
)
from .workflow_lint import is_schema_lint_error, lint_workflow
from .logging_config import setup_workflow_logging


_logger = logging.getLogger("flocks.workflow.runner")


_SANDBOX_MODE_ON_VALUES = {"on", "all", "non-main"}

# ---------------------------------------------------------------------------
# Config cache – avoids spawning a new event-loop (and its self-pipe FDs) on
# every workflow execution when run_workflow() is called from a thread pool
# worker (e.g. via asyncio.to_thread()).  Each asyncio.run() call inside a
# thread creates a _UnixSelectorEventLoop whose socketpair FDs are normally
# closed, but under high concurrency (8+ syslog workers) the repeated
# create/destroy cycle can exhaust the process fd limit before GC runs,
# triggering [Errno 24] Too many open files and the cascade of
# AttributeError: '_UnixSelectorEventLoop' has no attribute '_ssock'.
# A 30-second TTL is a reasonable balance: config changes are uncommon,
# and the short window means a reload never lags by more than 30 s.
# ---------------------------------------------------------------------------
_config_cache: Dict[str, Any] = {}
_config_cache_ts: float = 0.0
_config_cache_ttl: float = 30.0
_config_cache_lock = threading.Lock()


def _run_coro_sync(coro):
    """Run async coroutine from sync context safely."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # We are already inside an event loop, so run coroutine in a worker thread.
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, coro)
        return future.result()


def _load_config_data() -> Dict[str, Any]:
    """Load merged config data with a short-lived TTL cache.

    Calling ``asyncio.run()`` (via ``_run_coro_sync``) inside a thread-pool
    worker creates a temporary event loop each time.  Under high-frequency
    syslog loads the repeated creation / teardown of those loops can exhaust
    the process file-descriptor limit.  The cache amortises that cost: the
    coroutine is only executed once per ``_config_cache_ttl`` seconds.
    """
    global _config_cache, _config_cache_ts

    now = time.monotonic()
    with _config_cache_lock:
        if now - _config_cache_ts < _config_cache_ttl and _config_cache:
            return dict(_config_cache)

    try:
        cfg = _run_coro_sync(Config.get())
    except Exception as exc:
        _logger.debug("workflow runtime: failed to load config: %s", exc)
        with _config_cache_lock:
            return dict(_config_cache)

    result: Dict[str, Any] = {}
    if hasattr(cfg, "model_dump"):
        try:
            dumped = cfg.model_dump(by_alias=True, exclude_none=True, mode="json")
            if isinstance(dumped, dict):
                result = dumped
        except Exception as exc:
            _logger.debug("workflow runtime: failed to dump config: %s", exc)

    with _config_cache_lock:
        _config_cache = result
        _config_cache_ts = time.monotonic()
        # Return a shallow copy on this cold path too, mirroring the hit
        # path above.  Without it the very first caller would receive the
        # exact ``_config_cache`` dict and a top-level mutation on their
        # "snapshot" would leak into every subsequent cached read.
        return dict(_config_cache)


def _resolve_workflow_runtime_preference(tool_context: Optional[Any]) -> Literal["sandbox", "host"]:
    """Resolve workflow runtime preference only from sandbox.mode."""
    _ = tool_context
    config_data = _load_config_data()
    sandbox_cfg = config_data.get("sandbox") or {}
    if isinstance(sandbox_cfg, dict):
        mode = sandbox_cfg.get("mode")
        if isinstance(mode, str):
            normalized_mode = mode.strip().lower()
            if normalized_mode in _SANDBOX_MODE_ON_VALUES:
                return "sandbox"  # type: ignore[return-value]
            if normalized_mode == "off":
                return "host"  # type: ignore[return-value]
    # sandbox.mode omitted: default to host for explicitness and consistency.
    return "host"  # type: ignore[return-value]


def _resolve_workflow_node_timeout(
    requested_timeout_s: Optional[float],
    workflow_metadata: Optional[Dict[str, Any]],
) -> Optional[float]:
    """Resolve effective per-node timeout with workflow metadata fallback."""
    if requested_timeout_s is None:
        return None
    if requested_timeout_s not in (300, 300.0):
        return requested_timeout_s
    if not isinstance(workflow_metadata, dict):
        return requested_timeout_s

    candidates = [
        workflow_metadata.get("node_timeout_s"),
        workflow_metadata.get("nodeTimeoutS"),
    ]
    runtime_defaults = workflow_metadata.get("runtime_defaults")
    if isinstance(runtime_defaults, dict):
        candidates.extend(
            [
                runtime_defaults.get("node_timeout_s"),
                runtime_defaults.get("nodeTimeoutS"),
            ]
        )

    for value in candidates:
        if value is None:
            continue
        try:
            resolved = float(value)
        except (TypeError, ValueError):
            continue
        if resolved > 0:
            return resolved
    return requested_timeout_s


def _resolve_workflow_dataflow_mode(workflow_metadata: Optional[Dict[str, Any]]) -> Literal["legacy", "vertex_cache"]:
    """Resolve workflow dataflow mode from metadata.

    Missing metadata intentionally stays legacy so historical workflow files run
    with their original full-payload edge semantics.
    """
    if not isinstance(workflow_metadata, dict):
        return "legacy"

    candidates: list[Any] = [
        workflow_metadata.get("dataflow_mode"),
        workflow_metadata.get("dataflowMode"),
    ]
    for section_key in ("runtime", "runtime_defaults", "runtimeDefaults"):
        section = workflow_metadata.get(section_key)
        if isinstance(section, dict):
            candidates.extend(
                [
                    section.get("dataflow_mode"),
                    section.get("dataflowMode"),
                ]
            )

    for value in candidates:
        normalized = str(value or "").strip().lower().replace("-", "_")
        if normalized in {"vertex_cache", "vertex", "cache"}:
            return "vertex_cache"
        if normalized in {"legacy", "classic", "default"}:
            return "legacy"
    return "legacy"


def _resolve_sandbox_payload_from_config(tool_context: Optional[Any]) -> Optional[Dict[str, Any]]:
    """Resolve sandbox payload directly from config for workflow sandbox default."""
    config_data = _load_config_data()
    if not config_data:
        return None

    sandbox_cfg = config_data.get("sandbox")
    if not isinstance(sandbox_cfg, dict):
        sandbox_cfg = {}
        config_data["sandbox"] = sandbox_cfg

    # strategy-B force mode: workflow defaults to sandbox even without request-context injection.
    sandbox_cfg["mode"] = "on"

    session_key = getattr(tool_context, "session_id", None) or "workflow-default-session"
    agent_id = getattr(tool_context, "agent", None) or "default"
    extra = getattr(tool_context, "extra", None)
    main_session_key = "main"
    workspace_dir = os.getcwd()
    if isinstance(extra, dict):
        main_session_key = str(extra.get("main_session_key") or main_session_key)
        workspace_dir = str(extra.get("workspace_dir") or workspace_dir)

    try:
        sandbox_ctx = _run_coro_sync(
            resolve_sandbox_context(
                config_data=config_data,
                session_key=session_key,
                agent_id=agent_id,
                main_session_key=main_session_key,
                workspace_dir=workspace_dir,
            )
        )
    except Exception as exc:
        _logger.warning("workflow runtime: failed to resolve sandbox context: %s", exc)
        return None

    if sandbox_ctx is None:
        return None

    payload = {
        "container_name": sandbox_ctx.container_name,
        "workspace_dir": sandbox_ctx.workspace_dir,
        "container_workdir": sandbox_ctx.container_workdir,
        "workspace_access": sandbox_ctx.workspace_access,
        "agent_workspace_dir": sandbox_ctx.agent_workspace_dir,
    }
    env = getattr(sandbox_ctx, "env", None)
    if isinstance(env, dict):
        payload["env"] = env
    return payload


def _extract_sandbox_runtime_payload(tool_context: Optional[Any]) -> Optional[Dict[str, Any]]:
    """Extract sandbox config payload from ToolContext.extra if available."""
    if tool_context is None:
        return None
    extra = getattr(tool_context, "extra", None)
    if not isinstance(extra, dict):
        return None
    sandbox = extra.get("sandbox")
    if isinstance(sandbox, dict):
        return sandbox
    if hasattr(sandbox, "model_dump"):
        try:
            dumped = sandbox.model_dump(exclude_none=True)
            return dumped if isinstance(dumped, dict) else None
        except Exception:
            return None
    return None


def _ensure_logging_configured() -> None:
    """确保日志已配置，如果没有则使用默认配置。"""
    workflow_logger = logging.getLogger("flocks.workflow")
    # 检查是否已经配置了 handler
    if not workflow_logger.handlers and workflow_logger.level == logging.NOTSET:
        setup_workflow_logging()


WorkflowSource = Union[Dict[str, Any], str, Path, Workflow]


@dataclass
class RunWorkflowResult:
    status: str
    run_id: Optional[str] = None
    steps: int = 0
    last_node_id: Optional[str] = None
    outputs: Dict[str, Any] = None  # type: ignore[assignment]
    history: list[Dict[str, Any]] = None  # type: ignore[assignment]
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if self.outputs is None:
            self.outputs = {}
        if self.history is None:
            self.history = []


def _build_initial_inputs(
    inputs: Optional[Dict[str, Any]],
    workflow_path: Optional[str],
) -> Dict[str, Any]:
    """Build initial inputs and inject workflow file context when available."""
    initial_inputs: Dict[str, Any] = dict(inputs or {})
    if not workflow_path:
        return initial_inputs

    resolved_workflow_path = str(Path(workflow_path).expanduser().resolve())
    initial_inputs.setdefault("_workflow_path", resolved_workflow_path)
    initial_inputs.setdefault(
        "_workflow_dir",
        str(Path(resolved_workflow_path).parent),
    )
    return initial_inputs


def run_workflow(
    *,
    workflow: WorkflowSource,
    inputs: Optional[Dict[str, Any]] = None,
    timeout_s: Optional[float] = None,
    node_timeout_s: Optional[float] = 300.0,
    trace: bool = False,
    use_llm: Optional[bool] = None,
    tool_registry: Optional[Any] = None,
    tool_context: Optional[Any] = None,
    ensure_requirements: bool = True,
    requirements_installer: Optional[RequirementsInstaller] = None,
    sandbox_requirements_installer: Optional[SandboxRequirementsInstaller] = None,
    run_id: Optional[str] = None,
    on_step_start: Optional[Any] = None,
    on_step_complete: Optional[Any] = None,
    max_parallel_workers: int = 4,
    history_mode: Literal["full", "summary"] = "summary",
    cancel: Optional[Callable[[], bool]] = None,
    retain_history: bool = False,
) -> RunWorkflowResult:
    # 确保日志已配置
    _ensure_logging_configured()

    _logger.debug("=== 开始执行 workflow ===")

    workflow_path_for_engine: Optional[str] = None
    effective_use_llm: Optional[bool] = use_llm
    if isinstance(workflow, Workflow):
        _logger.debug("workflow 来源: Workflow 对象")
        wf = workflow
    elif isinstance(workflow, (str, Path)):
        workflow_path = Path(workflow).expanduser()
        _logger.debug("workflow 来源: 文件路径 %s", workflow_path)
        workflow_path_for_engine = str(workflow_path.resolve()) if workflow_path.exists() else None

        # Prefer compiled exec workflow if it exists (and is up-to-date). Otherwise compile first.
        exec_path = default_exec_path(workflow_path)
        _logger.debug("检查编译缓存: %s", exec_path)
        wf = load_workflow(workflow_path)
        if effective_use_llm is None:
            # Auto-enable LLM codegen when the workflow contains logic nodes.
            # This keeps pure-python workflows offline-friendly while ensuring
            # logic nodes can be materialized into runnable Python when needed.
            effective_use_llm = workflow_has_logic_nodes(wf)
            _logger.debug("自动检测 use_llm=%s (基于是否包含 logic 节点)", effective_use_llm)
        if workflow_path.exists():
            exec_is_fresh = False
            if exec_path.exists():
                try:
                    exec_is_fresh = exec_path.stat().st_mtime >= workflow_path.stat().st_mtime
                    _logger.debug("编译缓存状态: %s", "最新" if exec_is_fresh else "过期")
                except Exception:
                    exec_is_fresh = False
                    _logger.warning("无法检查编译缓存状态")

            if exec_is_fresh:
                try:
                    _logger.debug("使用编译缓存")
                    wf = load_workflow(exec_path)
                except Exception:
                    # If exec is unreadable, fall back to source then recompile below if needed.
                    _logger.warning("编译缓存不可读，回退到源文件")
                    wf = load_workflow(workflow_path)

            # Only compile when the source contains logic nodes. Pure-python workflows don't need exec.
            # If exec exists but is stale/unreadable, recompile and overwrite it.
            if workflow_has_logic_nodes(wf) and (not exec_is_fresh):
                _logger.debug("开始编译 workflow (包含 logic 节点)")
                compiled = compile_workflow(
                    wf,
                    use_llm=bool(effective_use_llm),
                    convert_logic_to_python=True,
                    preserve_description=True,
                    workflow_path=str(workflow_path),
                )
                _logger.debug("编译完成，保存到 %s", exec_path)
                dump_workflow(compiled, exec_path, indent=2)
                wf = compiled
    else:
        _logger.debug("workflow 来源: 字典")
        wf = Workflow.from_dict(workflow)

    if effective_use_llm is None:
        effective_use_llm = workflow_has_logic_nodes(wf)

    _logger.debug("workflow 信息: nodes=%s, edges=%s, start=%s", len(wf.nodes), len(wf.edges), wf.start)

    try:
        lint_results = lint_workflow(wf)
        lint_errors = [r for r in lint_results if r.get("severity") == "error"]
        lint_warnings = [r for r in lint_results if r.get("severity") != "error"]
        if lint_errors:
            _logger.error(f"workflow lint 检查发现 {len(lint_errors)} 个错误: {lint_errors[:5]}")
            strict_mapping_errors = [item for item in lint_errors if item.get("kind") == "implicit_full_payload_edge"]
            if strict_mapping_errors:
                raise WorkflowValidationError(f"Workflow strict edge mapping failed: {strict_mapping_errors[:5]}")
            schema_errors = [item for item in lint_errors if is_schema_lint_error(item)]
            if schema_errors:
                raise WorkflowValidationError(f"Workflow schema lint failed: {schema_errors[:5]}")
        if lint_warnings:
            _logger.warning(f"workflow lint 检查发现 {len(lint_warnings)} 个警告: {lint_warnings[:5]}")
    except WorkflowValidationError:
        raise
    except Exception:
        pass

    effective_node_timeout_s = _resolve_workflow_node_timeout(
        node_timeout_s,
        wf.metadata,
    )
    dataflow_mode = _resolve_workflow_dataflow_mode(wf.metadata)

    reqs = requirements_from_workflow_metadata(wf.metadata)
    if ensure_requirements:
        _logger.debug("检查依赖包...")
        if reqs:
            _logger.debug("需要安装的依赖: %s", reqs)

    _logger.debug("初始化工具注册表和运行时环境...")
    registry = tool_registry or get_tool_registry(tool_context=tool_context)
    sandbox_payload = _extract_sandbox_runtime_payload(tool_context)
    runtime_preference = _resolve_workflow_runtime_preference(tool_context)

    if runtime_preference == "host":
        sandbox_payload = None
    elif runtime_preference == "sandbox" and not sandbox_payload:
        sandbox_payload = _resolve_sandbox_payload_from_config(tool_context)
        if not sandbox_payload:
            _logger.warning(
                "workflow runtime: default=sandbox but sandbox context unavailable, fallback to host runtime"
            )

    if sandbox_payload:
        _logger.debug("workflow runtime: sandbox python execution enabled")
        if ensure_requirements and reqs:
            (sandbox_requirements_installer or SandboxRequirementsInstaller(installer="auto")).ensure_installed(
                reqs,
                sandbox=sandbox_payload,
            )
        rt = SandboxPythonExecRuntime(sandbox=sandbox_payload, tool_registry=registry)
    else:
        if runtime_preference == "host":
            _logger.debug("workflow runtime: host forced by sandbox.mode=off or runtime override")
        if ensure_requirements and reqs:
            (requirements_installer or RequirementsInstaller(installer="auto")).ensure_installed(reqs)
        rt = PythonExecRuntime(
            tool_registry=registry,
            cleanup_globals_after_execute=(history_mode == "summary"),
        )

    _logger.debug(
        "创建执行引擎 (use_llm=%s, trace=%s, node_timeout=%ss, parallel_workers=%s, history_mode=%s, dataflow_mode=%s)",
        effective_use_llm,
        trace,
        effective_node_timeout_s,
        max_parallel_workers,
        history_mode,
        dataflow_mode,
    )
    engine = WorkflowEngine(
        wf,
        runtime=rt,
        use_llm=bool(effective_use_llm),
        trace=trace,
        workflow_path=workflow_path_for_engine,
        node_timeout_s=effective_node_timeout_s,
        max_parallel_workers=max_parallel_workers,
        history_mode=history_mode,
        dataflow_mode=dataflow_mode,
    )

    initial_inputs = _build_initial_inputs(inputs, workflow_path_for_engine)
    _logger.debug(
        "开始执行 workflow (timeout=%ss, inputs=%s)",
        timeout_s,
        list(initial_inputs.keys()),
    )

    _on_step_start = None
    _on_step_end = None
    if on_step_start is not None:

        def _on_step_start(_rid, _step, _node, _inp):
            return on_step_start(_rid, _step, _node, _inp)
    elif on_step_complete is not None:

        def _on_step_start(_rid, _step, _node, _inp):
            return True

    if on_step_complete is not None:

        def _on_step_end(_token, step_result):
            return on_step_complete(step_result)

    try:
        result = engine.run(
            initial_inputs=initial_inputs,
            run_id=run_id,
            timeout_s=timeout_s,
            cancel=cancel,
            on_step_start=_on_step_start,
            on_step_end=_on_step_end,
            retain_history=retain_history,
        )
    except FlocksWorkflowError as e:
        # Extract execution context from error if available
        exec_ctx = getattr(e, "execution_context", {})
        history_from_error = exec_ctx.get("history", [])

        # Convert StepResult objects to dicts if needed
        if history_from_error and hasattr(history_from_error[0], "model_dump"):
            history_from_error = [s.model_dump(mode="json") for s in history_from_error]

        last_outputs = exec_ctx.get("outputs") or (
            history_from_error[-1].get("outputs", {}) if history_from_error else {}
        )

        status = "FAILED"
        if isinstance(e, RunCancelledError):
            status = "CANCELLED"
        elif isinstance(e, RunTimeoutError):
            status = "TIMED_OUT"

        return RunWorkflowResult(
            status=status,
            error=f"{type(e).__name__}: {e}",
            run_id=exec_ctx.get("run_id"),
            steps=exec_ctx.get("steps", 0),
            last_node_id=exec_ctx.get("last_node_id"),
            outputs=last_outputs,
            history=history_from_error,
        )

    history = [s.model_dump(mode="json") for s in result.history] if result.history else []
    last_outputs = result.outputs if result.outputs else {}

    if cancel is not None and cancel():
        return RunWorkflowResult(
            status="CANCELLED",
            run_id=result.run_id,
            steps=result.steps,
            last_node_id=result.last_node_id,
            outputs=last_outputs,
            history=history,
            error=f"RunCancelledError: Run cancelled: run_id={result.run_id}",
        )

    _logger.info(
        f"=== workflow 执行成功 === run_id={result.run_id}, steps={result.steps}, last_node={result.last_node_id}"
    )

    return RunWorkflowResult(
        status="SUCCEEDED",
        run_id=result.run_id,
        steps=result.steps,
        last_node_id=result.last_node_id,
        outputs=last_outputs,
        history=history,
    )

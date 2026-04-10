"""Workflow execution engine."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from dataclasses import dataclass, field
import hashlib
import logging
import time
import traceback
import uuid
import json
from typing import Any, Callable, Deque, Dict, List, NamedTuple, Optional, Set, Tuple, TypeVar

from pydantic import BaseModel, Field

from .code_gen import CodeGen, SimpleCodeGen, LLMCodeGen
from .errors import MaxStepsExceededError, NodeExecutionError, RunCancelledError, RunTimeoutError
from .models import Edge, Workflow, Node
from .repl_runtime import PythonExecRuntime, Runtime


_logger = logging.getLogger("flocks.workflow.engine")

_T = TypeVar("_T")

StepStartHook = Callable[[str, int, Node, Dict[str, Any]], _T]
StepEndHook = Callable[[_T, "StepResult"], None]


class _ExecOutcome(NamedTuple):
    """Result of executing a single node within a batch."""
    idx: int
    outputs: Dict[str, Any]
    stdout: str
    error: Optional[str]
    traceback: Optional[str]
    duration_ms: float
    is_timeout: bool = False


def _outputs_for_log(outputs: Dict[str, Any], *, max_chars: int = 4000) -> str:
    """Serialize outputs for logs with bounded size."""
    try:
        text = json.dumps(outputs, ensure_ascii=False, default=str)
    except Exception:
        text = repr(outputs)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"...[truncated:{len(text) - max_chars}]"


class StepResult(BaseModel):
    node_id: str
    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    stdout: str = ""
    error: Optional[str] = None
    traceback: Optional[str] = None
    duration_ms: Optional[float] = None


class ExecutionResult(BaseModel):
    steps: int
    history: list[StepResult] = Field(default_factory=list)
    last_node_id: Optional[str] = None
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)


def _default_workflow_loader(workflow_id: str) -> "Workflow":
    """Default loader: resolves workflow from Storage by ID (sync wrapper)."""
    import asyncio

    async def _load():
        from flocks.storage.storage import Storage
        data = await Storage.read(f"workflow/{workflow_id}")
        if data is None:
            raise NodeExecutionError(
                node_id="<subworkflow>",
                message=f"Workflow not found: {workflow_id!r}",
            )
        wf_json = data.get("workflowJson") or data
        return Workflow.from_dict(wf_json)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None:
        return asyncio.run(_load())

    executor = getattr(_default_workflow_loader, "_executor", None)
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wf-subwf")
        setattr(_default_workflow_loader, "_executor", executor)
    fut = executor.submit(lambda: asyncio.run(_load()))
    return fut.result()


@dataclass
class WorkflowEngine:
    workflow: Workflow
    runtime: Optional[Runtime] = None
    code_gen: Optional[CodeGen] = None
    max_steps: int = 10_000
    stop_on_error: bool = True
    use_llm: bool = False
    trace: bool = False
    mutate_workflow: bool = False
    workflow_path: Optional[str] = None
    node_timeout_s: Optional[float] = 300.0
    _depth: int = 0
    max_parallel_workers: int = 4
    workflow_loader: Optional[Callable[[str], "Workflow"]] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.max_steps <= 0:
            raise ValueError("max_steps must be > 0")
        if self.max_parallel_workers < 1:
            raise ValueError("max_parallel_workers must be >= 1")
        if self.runtime is None:
            self.runtime = PythonExecRuntime()
        if self.code_gen is None:
            if self.use_llm:
                self.code_gen = LLMCodeGen(workflow_path=self.workflow_path)
            else:
                self.code_gen = SimpleCodeGen()
        if self.workflow_loader is None:
            self.workflow_loader = _default_workflow_loader

    def _get_isolated_runtime(self) -> "Runtime":
        """Create a thread-isolated runtime copy for parallel node execution.

        PythonExecRuntime uses a shared ``globals`` dict that is NOT thread-safe.
        For parallel execution each worker thread receives a shallow copy of the
        globals dict so that ``inputs`` / ``outputs`` bindings don't collide.
        SandboxPythonExecRuntime spawns a new subprocess per call and is already
        safe for concurrent use.
        """
        assert self.runtime is not None
        if isinstance(self.runtime, PythonExecRuntime):
            return PythonExecRuntime(
                globals=dict(self.runtime.globals),
                tool_registry=self.runtime.tool_registry,
            )
        return self.runtime

    def run(
        self,
        initial_inputs: Optional[Dict[str, Any]] = None,
        *,
        run_id: Optional[str] = None,
        timeout_s: Optional[float] = None,
        cancel: Optional[Callable[[], bool]] = None,
        on_step_start: Optional[StepStartHook[Any]] = None,
        on_step_end: Optional[StepEndHook[Any]] = None,
    ) -> ExecutionResult:
        assert self.runtime is not None
        nodes = self.workflow.nodes_by_id()
        adj = self.workflow.adjacency()
        incoming_from: Dict[str, List[str]] = {n.id: [] for n in self.workflow.nodes}
        for e in self.workflow.edges:
            incoming_from.setdefault(e.to, []).append(e.from_)
        for k in incoming_from:
            incoming_from[k].sort()
        q: Deque[Tuple[str, Dict[str, Any], Optional[str]]] = deque(
            [(self.workflow.start, initial_inputs or {}, None)]
        )
        history: list[StepResult] = []
        step_count = 0
        last_node_id: Optional[str] = None
        rid = (run_id or uuid.uuid4().hex).strip() or uuid.uuid4().hex
        run_t0 = time.perf_counter()
        join_inputs: Dict[str, Dict[str, Dict[str, Any]]] = {}
        join_seen_sources: Dict[str, Set[str]] = {}
        # Dedup: track (node_id -> last_input_hash) to skip identical re-executions.
        _dedup_hashes: Dict[str, str] = {}
        step_timeout_s = self.node_timeout_s if (self.node_timeout_s is not None and self.node_timeout_s > 0) else None
        timeout_executor: Optional[ThreadPoolExecutor] = None
        if step_timeout_s is not None:
            timeout_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wf-node")
        try:
            def _build_execution_context() -> Dict[str, Any]:
                return {
                    "run_id": rid,
                    "steps": step_count,
                    "last_node_id": last_node_id,
                    "history": history,
                }

            while q:
                if cancel is not None and cancel():
                    err = RunCancelledError(rid)
                    err.execution_context = _build_execution_context()
                    raise err
                if timeout_s is not None and timeout_s > 0:
                    if (time.perf_counter() - run_t0) > float(timeout_s):
                        err = RunTimeoutError(rid, float(timeout_s))
                        err.execution_context = _build_execution_context()
                        raise err
                if step_count >= self.max_steps:
                    err = MaxStepsExceededError(self.max_steps)
                    err.execution_context = _build_execution_context()
                    raise err

                # ── Phase 1: drain queue, apply join / dedup ──────────────
                ready: List[Tuple[str, Node, Dict[str, Any], Optional[str]]] = []
                while q:
                    node_id, inputs, src_node_id = q.popleft()
                    last_node_id = node_id
                    node = nodes[node_id]

                    # Join handling
                    if getattr(node, "join", False) and incoming_from.get(node_id):
                        expected = incoming_from[node_id]
                        by_src = join_inputs.setdefault(node_id, {})
                        src_key = src_node_id or "__start__"
                        buf = by_src.setdefault(src_key, {})
                        buf.update(inputs)
                        seen = join_seen_sources.setdefault(node_id, set())
                        if src_node_id is not None:
                            if src_node_id in expected:
                                seen.add(src_node_id)
                        else:
                            seen.add("__start__")
                        if len(seen.intersection(set(expected))) < len(expected):
                            continue
                        by_src = join_inputs.pop(node_id, by_src)
                        merged: Dict[str, Any] = {}
                        origin: Dict[str, str] = {}
                        conflict_mode = getattr(node, "join_conflict", "overwrite")
                        join_mode = getattr(node, "join_mode", "flat")
                        namespace_key = getattr(node, "join_namespace_key", "__by_source__") or "__by_source__"

                        def merge_payload(src: str, payload: Dict[str, Any]) -> None:
                            nonlocal merged, origin
                            for k, v in payload.items():
                                if k in merged and conflict_mode == "error" and merged.get(k) != v:
                                    raise NodeExecutionError(
                                        node_id=node_id,
                                        message=(
                                            "Join conflict on key "
                                            f"{k!r}: sources {origin.get(k)!r} and {src!r} provided different values"
                                        ),
                                    )
                                merged[k] = v
                                origin[k] = src

                        for src in expected:
                            merge_payload(src, by_src.get(src, {}))
                        for src, payload in sorted(by_src.items(), key=lambda x: x[0]):
                            if src in set(expected):
                                continue
                            merge_payload(src, payload)
                        if join_mode == "namespace":
                            merged.setdefault(namespace_key, dict(by_src))
                        inputs = merged
                        join_seen_sources.pop(node_id, None)

                    # Dedup: skip if same node already ran with identical inputs
                    try:
                        _hash_raw = json.dumps(
                            {"n": node_id, "i": inputs},
                            sort_keys=True, ensure_ascii=False, default=str,
                        )
                        _input_hash = hashlib.sha256(_hash_raw.encode()).hexdigest()[:16]
                    except Exception:
                        _input_hash = ""
                    if _input_hash and node_id in _dedup_hashes and _dedup_hashes[node_id] == _input_hash:
                        _logger.info(
                            "wf.step.dedup_skip node=%s (identical input hash %s)",
                            node_id, _input_hash,
                            extra={"run_id": rid, "node_id": node_id, "input_hash": _input_hash},
                        )
                        continue
                    if _input_hash:
                        _dedup_hashes[node_id] = _input_hash

                    ready.append((node_id, node, inputs, src_node_id))

                if not ready:
                    continue

                # ── Phase 2: execute ready items ──────────────────────────
                use_parallel = self.max_parallel_workers > 1 and len(ready) > 1
                exec_results: List[_ExecOutcome] = []

                # Call on_step_start hooks (always from main thread)
                step_tokens: Dict[int, Any] = {}
                for _idx, (_nid, _nd, _inp, _) in enumerate(ready):
                    if self.trace:
                        _desc = (_nd.description or "").strip().splitlines()[0:1]
                        _desc_text = _desc[0] if _desc else ""
                        _par_tag = " [parallel]" if use_parallel else ""
                        print(f"\n[WF] step={step_count+_idx+1} node={_nid} type={_nd.type} {_desc_text}{_par_tag}".rstrip())
                    _logger.info(
                        "wf.step.start step=%s node=%s type=%s%s",
                        step_count + _idx + 1, _nid, _nd.type,
                        " (parallel)" if use_parallel else "",
                        extra={"run_id": rid, "step": step_count + _idx + 1, "node_id": _nid, "node_type": _nd.type},
                    )
                    if on_step_start is not None:
                        try:
                            step_tokens[_idx] = on_step_start(rid, step_count + _idx + 1, _nd, _inp)
                        except Exception:
                            _logger.exception(
                                "wf.step_start.hook_error",
                                extra={"run_id": rid, "step": step_count + _idx + 1, "node_id": _nid},
                            )

                if use_parallel:
                    # ── Parallel execution ────────────────────────────────
                    _pw = min(len(ready), self.max_parallel_workers)

                    def _par_exec(
                        _args: Tuple[int, str, Node, Dict[str, Any]],
                    ) -> _ExecOutcome:
                        _pi, _pnid, _pnd, _pinp = _args
                        _t0 = time.perf_counter()
                        try:
                            _prt = self._get_isolated_runtime()
                            _pouts, _pso = self._execute_node(_pnd, _pinp, _runtime=_prt)
                            return _ExecOutcome(_pi, _pouts, _pso, None, None, (time.perf_counter() - _t0) * 1000.0)
                        except Exception as _pe:
                            _perr = str(_pe)
                            _ptb: Optional[str] = traceback.format_exc()
                            _pso_err = ""
                            if isinstance(_pe, NodeExecutionError):
                                if getattr(_pe, "stdout", None):
                                    _pso_err = _pe.stdout or ""
                                if getattr(_pe, "traceback", None):
                                    _ptb = _pe.traceback
                            return _ExecOutcome(_pi, {}, _pso_err, _perr, _ptb, (time.perf_counter() - _t0) * 1000.0)

                    _exec_args = [(i, nid, nd, inp) for i, (nid, nd, inp, _) in enumerate(ready)]
                    _completed_idx: Set[int] = set()
                    _pool = ThreadPoolExecutor(max_workers=_pw, thread_name_prefix="wf-par")
                    try:
                        _fut_map = {_pool.submit(_par_exec, a): a[0] for a in _exec_args}
                        try:
                            for _fut in as_completed(_fut_map.keys(), timeout=step_timeout_s):
                                _ci = _fut_map[_fut]
                                _completed_idx.add(_ci)
                                exec_results.append(_fut.result())
                        except FuturesTimeoutError:
                            for _f2, _ci2 in _fut_map.items():
                                if _ci2 not in _completed_idx:
                                    exec_results.append(_ExecOutcome(
                                        idx=_ci2, outputs={}, stdout="",
                                        error=f"节点执行超时 ({self.node_timeout_s}s)",
                                        traceback=None,
                                        duration_ms=(step_timeout_s or 0) * 1000.0,
                                        is_timeout=True,
                                    ))
                    finally:
                        try:
                            _pool.shutdown(wait=False, cancel_futures=True)
                        except TypeError:
                            _pool.shutdown(wait=False)
                    exec_results.sort(key=lambda x: x.idx)
                else:
                    # ── Serial execution (preserves original behaviour) ───
                    for _idx, (_nid, _nd, _inp, _src) in enumerate(ready):
                        _t0 = time.perf_counter()
                        try:
                            if step_timeout_s is not None and timeout_executor is not None:
                                _sfut = timeout_executor.submit(self._execute_node, _nd, _inp)
                                _outs, _so = _sfut.result(timeout=step_timeout_s)
                            else:
                                _outs, _so = self._execute_node(_nd, _inp)
                            exec_results.append(_ExecOutcome(_idx, _outs, _so, None, None, (time.perf_counter() - _t0) * 1000.0))
                        except FuturesTimeoutError as _fte:
                            _fte_msg = str(_fte).strip()
                            _err = _fte_msg if _fte_msg else f"节点执行超时 ({self.node_timeout_s}s)"
                            exec_results.append(_ExecOutcome(_idx, {}, "", _err, None, (time.perf_counter() - _t0) * 1000.0, is_timeout=True))
                            if timeout_executor is not None:
                                try:
                                    timeout_executor.shutdown(wait=False, cancel_futures=True)
                                except TypeError:
                                    timeout_executor.shutdown(wait=False)
                                timeout_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wf-node")
                        except Exception as _e:
                            _err = str(_e)
                            _tb: Optional[str] = traceback.format_exc()
                            _so = ""
                            if isinstance(_e, NodeExecutionError) and getattr(_e, "stdout", None):
                                _so = _e.stdout or ""
                            if isinstance(_e, NodeExecutionError) and getattr(_e, "traceback", None):
                                _tb = _e.traceback
                            exec_results.append(_ExecOutcome(_idx, {}, _so, _err, _tb, (time.perf_counter() - _t0) * 1000.0))

                # ── Phase 3: record results, hooks, enqueue downstream ────
                _stop_exc: Optional[NodeExecutionError] = None
                for _eo in exec_results:
                    _nid, _nd, _inp, _src = ready[_eo.idx]
                    _sn = step_count + _eo.idx + 1
                    last_node_id = _nid

                    if _eo.error is not None:
                        # ── error / timeout result ────────────────────────
                        if self.trace:
                            if _eo.stdout:
                                print("[WF] stdout=" + _eo.stdout.rstrip())
                            print(f"[WF] error={_eo.error}")
                            if _eo.traceback:
                                print("[WF] traceback:")
                                print(_eo.traceback.rstrip())
                        step_res = StepResult(
                            node_id=_nid, inputs=_inp, outputs=_eo.outputs,
                            stdout=_eo.stdout, error=_eo.error, traceback=_eo.traceback,
                            duration_ms=_eo.duration_ms,
                        )
                        history.append(step_res)
                        _status = "timeout" if _eo.is_timeout else "error"
                        (_logger.warning if _eo.is_timeout else _logger.error)(
                            f"wf.step.{_status}",
                            extra={
                                "run_id": rid, "step": _sn, "node_id": _nid,
                                "node_type": _nd.type, "error": _eo.error,
                                **({"timeout_s": self.node_timeout_s} if _eo.is_timeout else {"traceback": (_eo.traceback or "")[:500]}),
                            },
                        )
                        _logger.info(
                            "wf.step.end step=%s node=%s type=%s status=%s duration_ms=%.3f outputs=%s error=%s",
                            _sn, _nid, _nd.type, _status, _eo.duration_ms,
                            _outputs_for_log(_eo.outputs), _eo.error,
                            extra={
                                "run_id": rid, "step": _sn, "node_id": _nid,
                                "node_type": _nd.type, "status": _status,
                                "duration_ms": _eo.duration_ms, "outputs_keys": list(_eo.outputs.keys()),
                                "outputs": _outputs_for_log(_eo.outputs), "error": _eo.error,
                            },
                        )
                        if on_step_end is not None and _eo.idx in step_tokens:
                            try:
                                on_step_end(step_tokens[_eo.idx], step_res)
                            except Exception:
                                _logger.exception(
                                    "wf.step_end.hook_error",
                                    extra={"run_id": rid, "step": _sn, "node_id": _nid},
                                )
                        if self.stop_on_error and _stop_exc is None and not _eo.is_timeout:
                            _stop_exc = NodeExecutionError(
                                node_id=_nid, message=_eo.error,
                                stdout=_eo.stdout, traceback=_eo.traceback,
                                execution_context={
                                    "run_id": rid,
                                    "steps": step_count + len(exec_results),
                                    "last_node_id": _nid,
                                    "history": history,
                                },
                            )
                            continue
                    else:
                        # ── success result ────────────────────────────────
                        if self.trace:
                            if _eo.stdout:
                                print("[WF] stdout=" + _eo.stdout.rstrip())
                            try:
                                print("[WF] outputs=" + json.dumps(_eo.outputs, ensure_ascii=False, default=str))
                            except Exception:
                                print(f"[WF] outputs=<unserializable> {_eo.outputs!r}")
                        step_res = StepResult(
                            node_id=_nid, inputs=_inp, outputs=_eo.outputs,
                            stdout=_eo.stdout, error=None, duration_ms=_eo.duration_ms,
                        )
                        history.append(step_res)
                        _logger.info(
                            "wf.step.end step=%s node=%s type=%s status=%s duration_ms=%.3f outputs=%s",
                            _sn, _nid, _nd.type, "ok", _eo.duration_ms, _outputs_for_log(_eo.outputs),
                            extra={
                                "run_id": rid, "step": _sn, "node_id": _nid,
                                "node_type": _nd.type, "status": "ok",
                                "duration_ms": _eo.duration_ms, "outputs_keys": list(_eo.outputs.keys()),
                                "outputs": _outputs_for_log(_eo.outputs), "error": None,
                            },
                        )
                        if on_step_end is not None and _eo.idx in step_tokens:
                            try:
                                on_step_end(step_tokens[_eo.idx], step_res)
                            except Exception:
                                _logger.exception(
                                    "wf.step_end.hook_error",
                                    extra={"run_id": rid, "step": _sn, "node_id": _nid},
                                )

                    # Enqueue downstream (skip for failed node when stop_on_error)
                    if _stop_exc is None:
                        upstream = dict(_inp)
                        upstream.update(_eo.outputs)
                        selected = self._select_edges(_nd, upstream, adj.get(_nid, []))
                        for edge in selected:
                            q.append((edge.to, self._build_downstream_inputs(upstream, edge), _nid))

                step_count += len(exec_results)
                if _stop_exc is not None:
                    raise _stop_exc

            pending_joins = []
            for nid, buf in join_inputs.items():
                n = nodes.get(nid)
                if n is not None and getattr(n, "join", False):
                    expected = incoming_from.get(nid, [])
                    seen = join_seen_sources.get(nid, set())
                    pending_joins.append((nid, expected, sorted(seen)))
            if pending_joins:
                msg = "Join node(s) did not receive all incoming inputs: " + "; ".join(
                    f"{nid} expected={expected} seen={seen}" for nid, expected, seen in pending_joins
                )
                raise NodeExecutionError(node_id=pending_joins[0][0], message=msg)
            return ExecutionResult(steps=step_count, history=history, last_node_id=last_node_id, run_id=rid)
        finally:
            if timeout_executor is not None:
                try:
                    timeout_executor.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    timeout_executor.shutdown(wait=False)

    def run_node(self, node_id: str, inputs: Dict[str, Any]) -> "StepResult":
        """Public API: execute a single node by id and return a StepResult.

        Unlike the full ``run()`` loop, this method executes exactly one node
        in isolation without traversing edges or updating shared state.  It is
        intended for external callers (e.g. the /run-node API endpoint) that
        want to drive execution step-by-step.
        """
        nodes = self.workflow.nodes_by_id()
        if node_id not in nodes:
            raise KeyError(f"Node {node_id!r} not found in workflow")
        node = nodes[node_id]
        t0 = time.perf_counter()
        try:
            outputs, stdout = self._execute_node(node, inputs)
            return StepResult(
                node_id=node_id,
                inputs=inputs,
                outputs=outputs,
                stdout=stdout,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )
        except Exception as e:
            tb = traceback.format_exc()
            return StepResult(
                node_id=node_id,
                inputs=inputs,
                outputs={},
                error=str(e),
                traceback=tb,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )

    def _execute_node(
        self,
        node: Node,
        inputs: Dict[str, Any],
        *,
        _runtime: Optional["Runtime"] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """Execute a single node; returns (outputs, stdout) or raises.

        When *_runtime* is supplied it is used instead of ``self.runtime`` for
        python / logic nodes.  This allows the parallel execution path to pass
        in a thread-isolated runtime copy.
        """
        _rt = _runtime or self.runtime
        assert _rt is not None
        node_id = node.id
        if node.type == "python":
            assert node.code is not None
            return _rt.execute(node.code, inputs)
        if node.type == "logic":
            assert self.code_gen is not None
            code = node.code
            if code is None or not str(code).strip():
                try:
                    code = self.code_gen.generate(node)
                except Exception as gen_error:
                    raise NodeExecutionError(
                        node_id=node_id,
                        message=f"Code generation failed: {type(gen_error).__name__}: {gen_error}",
                    ) from gen_error
                if self.mutate_workflow:
                    node.code = code
            return _rt.execute(code, inputs)
        if node.type in {"branch", "loop"}:
            return {}, ""
        if node.type == "tool":
            return self._execute_tool_node(node, inputs)
        if node.type == "llm":
            return self._execute_llm_node(node, inputs)
        if node.type == "http_request":
            return self._execute_http_request_node(node, inputs)
        if node.type == "subworkflow":
            return self._execute_subworkflow_node(node, inputs, _runtime=_runtime)
        raise NodeExecutionError(
            node_id=node_id, message=f"Unsupported node.type={node.type!r}"
        )

    def _execute_tool_node(self, node: Node, inputs: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        """Execute a tool node by calling the named tool from the tool registry."""
        assert node.tool_name, "tool node requires tool_name"
        from .tools import ToolFacade, get_tool_registry
        reg = get_tool_registry()
        facade = ToolFacade(reg)
        merged_args = {**(node.tool_args or {}), **inputs}
        try:
            result = facade.run(node.tool_name, **merged_args)
        except Exception as e:
            raise NodeExecutionError(
                node_id=node.id,
                message=f"Tool '{node.tool_name}' failed: {type(e).__name__}: {e}",
            ) from e
        output_k = node.output_key or "result"
        return {output_k: result}, ""

    def _execute_llm_node(self, node: Node, inputs: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        """Execute an LLM node: render Jinja2 prompt template, call LLM."""
        assert node.prompt, "llm node requires prompt"
        try:
            from jinja2 import Template, TemplateError
            rendered = Template(node.prompt).render(**inputs)
        except Exception as e:
            raise NodeExecutionError(
                node_id=node.id,
                message=f"Prompt template render failed: {type(e).__name__}: {e}",
            ) from e
        from .llm import get_llm_client
        try:
            client = get_llm_client(model=node.model)
            text = client.ask(rendered)
        except Exception as e:
            raise NodeExecutionError(
                node_id=node.id,
                message=f"LLM call failed: {type(e).__name__}: {e}",
            ) from e
        output_k = node.output_key or "result"
        return {output_k: text}, ""

    def _execute_http_request_node(self, node: Node, inputs: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        """Execute an HTTP request node using httpx (synchronous)."""
        assert node.url, "http_request node requires url"
        assert node.method, "http_request node requires method"
        try:
            from jinja2 import Template
            url = Template(node.url).render(**inputs)
            method = node.method.upper()
            headers = node.headers or {}
            body = node.body
            if isinstance(body, str):
                body = Template(body).render(**inputs)
        except Exception as e:
            raise NodeExecutionError(
                node_id=node.id,
                message=f"HTTP request template render failed: {type(e).__name__}: {e}",
            ) from e
        try:
            import httpx
            with httpx.Client(timeout=30.0) as client:
                if method in {"GET", "DELETE", "HEAD"}:
                    resp = client.request(method, url, headers=headers)
                elif isinstance(body, dict):
                    resp = client.request(method, url, headers=headers, json=body)
                else:
                    resp = client.request(method, url, headers=headers, content=body)
            try:
                resp_data: Any = resp.json()
            except Exception:
                resp_data = resp.text
        except Exception as e:
            raise NodeExecutionError(
                node_id=node.id,
                message=f"HTTP request failed: {type(e).__name__}: {e}",
            ) from e
        response_k = node.response_key or "response"
        return {
            response_k: resp_data,
            "status_code": resp.status_code,
        }, ""

    def _execute_subworkflow_node(
        self,
        node: Node,
        inputs: Dict[str, Any],
        *,
        _runtime: Optional["Runtime"] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """Execute a subworkflow node by loading and running a child workflow."""
        if self._depth >= 1:
            raise NodeExecutionError(
                node_id=node.id,
                message="子工作流不允许再嵌套子工作流（最大嵌套深度为 1）",
            )
        assert node.workflow_id, "subworkflow node requires workflow_id"
        assert self.workflow_loader is not None
        try:
            sub_wf = self.workflow_loader(node.workflow_id)
        except NodeExecutionError:
            raise
        except Exception as e:
            raise NodeExecutionError(
                node_id=node.id,
                message=f"Failed to load sub-workflow '{node.workflow_id}': {type(e).__name__}: {e}",
            ) from e
        if node.inputs_mapping:
            sub_inputs = {k: self._get_by_path(inputs, v) for k, v in node.inputs_mapping.items()}
        else:
            sub_inputs = dict(inputs)
        if node.inputs_const:
            sub_inputs.update(node.inputs_const)
        sub_engine = WorkflowEngine(
            workflow=sub_wf,
            runtime=_runtime or self.runtime,
            code_gen=self.code_gen,
            max_steps=self.max_steps,
            stop_on_error=self.stop_on_error,
            use_llm=self.use_llm,
            trace=self.trace,
            node_timeout_s=self.node_timeout_s,
            _depth=self._depth + 1,
            workflow_loader=self.workflow_loader,
        )
        result = sub_engine.run(initial_inputs=sub_inputs)
        last_outputs = result.history[-1].outputs if result.history else {}
        output_k = node.output_key or "output"
        return {output_k: last_outputs}, ""

    def _select_edges(self, node: Any, payload: Dict[str, Any], edges: List[Edge]) -> List[Edge]:
        if not edges:
            return []
        if node.type in {"python", "tool", "llm", "http_request", "subworkflow"}:
            return list(edges)
        key = node.select_key or "result"
        value = self._get_by_path(payload, key)
        selected_label: Optional[str]
        if value is None:
            selected_label = None
        elif isinstance(value, bool):
            selected_label = "true" if value else "false"
        elif isinstance(value, str):
            selected_label = value
        else:
            selected_label = str(value)
        matched = [e for e in edges if e.label == selected_label] if selected_label is not None else []
        if matched:
            return matched
        defaults = [e for e in edges if e.label is None]
        return defaults[:1] if defaults else []

    def _build_downstream_inputs(self, upstream: Dict[str, Any], edge: Edge) -> Dict[str, Any]:
        if edge.mapping:
            out: Dict[str, Any] = {}
            for dst, src in edge.mapping.items():
                found, value = self._try_get_by_path(upstream, src)
                if found:
                    out[dst] = value
                elif self.trace:
                    available_keys = list(upstream.keys())[:10]
                    _logger.warning(
                        "wf.edge.mapping.none_value",
                        extra={"edge_from": edge.from_, "edge_to": edge.to, "dst_key": dst, "src_path": src, "available_keys": available_keys},
                    )
        else:
            out = dict(upstream)
        if edge.const:
            out.update(edge.const)
        return out

    def _try_get_by_path(self, data: Any, path: str) -> tuple[bool, Any]:
        if path is None:
            return False, None
        path = str(path).strip()
        if not path:
            return False, None
        if path == "$":
            return True, data
        if path.startswith("$."):
            path = path[2:]
        cur: Any = data
        for part in path.split("."):
            if isinstance(cur, dict):
                if part in cur:
                    cur = cur[part]
                else:
                    return False, None
            elif isinstance(cur, list):
                try:
                    idx = int(part)
                except Exception:
                    return False, None
                if 0 <= idx < len(cur):
                    cur = cur[idx]
                else:
                    return False, None
            else:
                return False, None
        return True, cur

    def _get_by_path(self, data: Any, path: str) -> Any:
        found, value = self._try_get_by_path(data, path)
        return value if found else None

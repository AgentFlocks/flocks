"""Regression tests for isolated and compatibility node timeouts."""

import os
import threading
import time

import pytest

import flocks.workflow.repl_runtime as repl_runtime_module
from flocks.workflow import NodeExecutionError, NodeTimeoutError, Workflow, WorkflowEngine, run_workflow
from flocks.workflow.repl_runtime import HostProcessPythonExecRuntime, PythonExecRuntime


def test_node_timeout_aborts_run_without_orphan_thread_or_downstream():
    """A timed-out subprocess is killed before the run fails."""
    workflow = Workflow.from_dict({
        "name": "timeout_test",
        "start": "slow",
        "nodes": [
            {
                "id": "slow",
                "type": "python",
                "code": "import time; time.sleep(5); outputs['x'] = 1",
                "description": "Never finishes within the timeout",
                "processIsolated": True,
                "timeoutFatal": True,
            },
            {
                "id": "fast",
                "type": "python",
                "code": "outputs['y'] = inputs.get('x', 0) + 10",
                "description": "Uses x or 0",
            },
        ],
        "edges": [{"from": "slow", "to": "fast"}],
    })
    rt = PythonExecRuntime()
    engine = WorkflowEngine(
        workflow,
        runtime=rt,
        node_timeout_s=0.05,
        stop_on_error=False,
    )
    baseline_threads = {thread.ident for thread in threading.enumerate() if thread.name.startswith("wf-node")}
    started = time.perf_counter()
    with pytest.raises(NodeTimeoutError) as caught:
        engine.run(initial_inputs={}, retain_history=True)
    elapsed = time.perf_counter() - started

    history = caught.value.execution_context["history"]
    assert elapsed < 1.0
    assert caught.value.execution_context["steps"] == 1
    assert len(history) == 1

    step_slow = history[0]
    assert step_slow.node_id == "slow"
    assert step_slow.error is not None
    assert "节点执行超时" in step_slow.error
    assert "0.05" in step_slow.error
    assert step_slow.outputs == {}
    assert {thread.ident for thread in threading.enumerate() if thread.name.startswith("wf-node")} == baseline_threads


def test_process_isolated_timeout_can_remain_nonfatal_for_compatibility():
    """Fatal timeout semantics remain opt-in at the node level."""
    workflow = Workflow.from_dict({
        "name": "cooperative_timeout",
        "start": "slow",
        "nodes": [
            {
                "id": "slow",
                "type": "python",
                "code": "import time; time.sleep(5)",
                "processIsolated": True,
            }
        ],
        "edges": [],
    })
    engine = WorkflowEngine(
        workflow,
        runtime=PythonExecRuntime(),
        node_timeout_s=0.05,
    )

    started = time.perf_counter()
    result = engine.run(retain_history=True)

    assert time.perf_counter() - started < 0.5
    assert result.steps == 1
    assert "节点执行超时" in (result.history[0].error or "")
    assert not any(thread.name.startswith("wf-node") for thread in threading.enumerate())


def test_process_timeout_closes_inherited_parent_fd(tmp_path):
    lease_fd = os.open(tmp_path / "lease.lock", os.O_RDWR | os.O_CREAT, 0o600)
    workflow = Workflow.from_dict({
        "start": "slow",
        "nodes": [
            {
                "id": "slow",
                "type": "python",
                "code": "import os, time\nos.fstat(inputs['lease_fd'])\ntime.sleep(5)",
                "processIsolated": True,
                "processInheritFdKeys": ["lease_fd"],
                "timeoutFatal": True,
            }
        ],
        "edges": [],
    })

    with pytest.raises(NodeTimeoutError):
        WorkflowEngine(
            workflow,
            runtime=PythonExecRuntime(),
            node_timeout_s=0.05,
        ).run({"lease_fd": lease_fd})

    with pytest.raises(OSError):
        os.fstat(lease_fd)


def test_process_rpc_bridge_matches_concurrent_responses_by_request_id():
    class Registry:
        cancel_checker = None

        def __init__(self):
            self.active = 0
            self.peak = 0
            self.lock = threading.Lock()

        def run(self, _name, *, value):
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                time.sleep((8 - value) * 0.005)
                return value
            finally:
                with self.lock:
                    self.active -= 1

    registry = Registry()
    workflow = Workflow.from_dict({
        "start": "parallel_rpc",
        "nodes": [
            {
                "id": "parallel_rpc",
                "type": "python",
                "processIsolated": True,
                "code": (
                    "from concurrent.futures import ThreadPoolExecutor\n"
                    "def call(value):\n"
                    "    return tool.run('echo', value=value)\n"
                    "with ThreadPoolExecutor(max_workers=8) as pool:\n"
                    "    outputs['values'] = list(pool.map(call, range(8)))"
                ),
            }
        ],
        "edges": [],
    })

    result = WorkflowEngine(
        workflow,
        runtime=PythonExecRuntime(tool_registry=registry),
        node_timeout_s=3,
        history_mode="full",
    ).run()

    assert result.outputs["values"] == list(range(8))
    assert registry.peak > 1


def test_process_rpc_bridge_defaults_to_32_workers():
    assert PythonExecRuntime().isolated_rpc_max_workers == 32
    assert HostProcessPythonExecRuntime().rpc_max_workers == 32


@pytest.mark.parametrize("rpc_max_workers", [3, 8])
def test_process_rpc_bridge_bounds_concurrent_llm_requests(monkeypatch, rpc_max_workers):
    real_executor = repl_runtime_module._ThreadPoolExecutor
    pending = 0
    pending_peak = 0
    pending_lock = threading.Lock()

    class TrackingExecutor(real_executor):
        def submit(self, fn, *args, **kwargs):
            nonlocal pending, pending_peak
            with pending_lock:
                pending += 1
                pending_peak = max(pending_peak, pending)

            def tracked():
                nonlocal pending
                try:
                    return fn(*args, **kwargs)
                finally:
                    with pending_lock:
                        pending -= 1

            try:
                return super().submit(tracked)
            except Exception:
                with pending_lock:
                    pending -= 1
                raise

    class LLM:
        def __init__(self):
            self.active = 0
            self.peak = 0
            self.lock = threading.Lock()

        def ask(self, prompt, **_kwargs):
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                time.sleep(0.05)
                return prompt
            finally:
                with self.lock:
                    self.active -= 1

    llm = LLM()
    monkeypatch.setattr(repl_runtime_module, "_ThreadPoolExecutor", TrackingExecutor)
    monkeypatch.setattr(repl_runtime_module, "get_lazy_llm", lambda **_kwargs: llm)

    outputs, _stdout = HostProcessPythonExecRuntime(rpc_max_workers=rpc_max_workers).execute(
        (
            "from concurrent.futures import ThreadPoolExecutor\n"
            "def call(value):\n"
            "    return llm.ask(str(value))\n"
            "with ThreadPoolExecutor(max_workers=24) as pool:\n"
            "    outputs['values'] = list(pool.map(call, range(24)))"
        ),
        {},
    )

    assert outputs["values"] == [str(value) for value in range(24)]
    assert llm.peak <= rpc_max_workers
    assert pending_peak <= rpc_max_workers


def test_process_rpc_tool_cancel_checker_is_scoped_per_request():
    class Registry:
        cancel_checker = None

        def __init__(self):
            self.barrier = threading.Barrier(2)

        def run(self, _name, **_kwargs):
            self.barrier.wait(timeout=1)
            return bool(self.cancel_checker and self.cancel_checker())

    registry = Registry()
    runtime = HostProcessPythonExecRuntime(tool_registry=registry)
    responses = {}

    def invoke(label, checker):
        responses[label] = runtime._handle_rpc_request(
            msg={
                "type": "rpc",
                "token": "token",
                "id": label,
                "rpc": {"kind": "tool", "name": "check"},
            },
            token="token",
            cancel_checker=checker,
        )

    first = threading.Thread(target=invoke, args=("first", lambda: False))
    second = threading.Thread(target=invoke, args=("second", lambda: True))
    first.start()
    second.start()
    first.join(timeout=1)
    second.join(timeout=1)

    assert responses["first"]["output"] is False
    assert responses["second"]["output"] is True
    assert callable(registry.cancel_checker)
    assert registry.cancel_checker() is False


def test_process_rpc_bridge_rejects_oversized_child_frame():
    with pytest.raises(NodeExecutionError, match="exceeds configured limit"):
        HostProcessPythonExecRuntime(rpc_max_bytes=1024).execute(
            "outputs['value'] = 'x' * 2048",
            {},
        )


def test_process_rpc_bridge_rejects_oversized_parent_response(monkeypatch):
    class LLM:
        def ask(self, _prompt, **_kwargs):
            return "x" * 8192

    monkeypatch.setattr(repl_runtime_module, "get_lazy_llm", lambda **_kwargs: LLM())

    with pytest.raises(NodeExecutionError, match="Bridge payload too large"):
        HostProcessPythonExecRuntime(rpc_max_bytes=4096).execute(
            "outputs['value'] = llm.ask('small')",
            {},
        )


def test_process_rpc_bridge_allows_explicitly_unlimited_frames():
    outputs, _stdout = HostProcessPythonExecRuntime(rpc_max_bytes=None).execute(
        "outputs['value'] = 'x' * 2048",
        {},
    )

    assert outputs == {"value": "x" * 2048}


def test_process_isolated_node_inherits_runtime_rpc_limit():
    workflow = Workflow.from_dict({
        "start": "large_output",
        "nodes": [
            {
                "id": "large_output",
                "type": "python",
                "processIsolated": True,
                "code": "outputs['value'] = 'x' * 2048",
            }
        ],
        "edges": [],
    })

    with pytest.raises(NodeExecutionError, match="exceeds configured limit"):
        WorkflowEngine(
            workflow,
            runtime=PythonExecRuntime(isolated_rpc_max_bytes=1024),
            node_timeout_s=3,
        ).run()


def test_process_rpc_bridge_cancels_legacy_tool_registry_when_child_exits():
    class Registry:
        cancel_checker = None

        def __init__(self):
            self.started = threading.Event()
            self.stopped = threading.Event()
            self.timed_out = threading.Event()

        def run(self, _name, **_kwargs):
            self.started.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if self.cancel_checker and self.cancel_checker():
                    self.stopped.set()
                    raise RuntimeError("bridge closed")
                time.sleep(0.01)
            self.timed_out.set()
            return "late"

    registry = Registry()
    with pytest.raises(NodeExecutionError, match="exited with code 17"):
        HostProcessPythonExecRuntime(tool_registry=registry).execute(
            (
                "import os, threading, time\n"
                "threading.Thread(target=lambda: tool.run('blocked'), daemon=True).start()\n"
                "time.sleep(0.3)\n"
                "os._exit(17)"
            ),
            {},
        )

    assert registry.started.wait(0.5)
    assert registry.stopped.wait(0.5)
    assert not registry.timed_out.is_set()
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline and any(
        thread.name.startswith("wf-process-rpc") for thread in threading.enumerate()
    ):
        time.sleep(0.01)
    assert not any(thread.name.startswith("wf-process-rpc") for thread in threading.enumerate())


def test_process_rpc_bridge_cancels_llm_when_child_exits(monkeypatch):
    started = threading.Event()
    stopped = threading.Event()
    timed_out = threading.Event()
    real_bounded_semaphore = threading.BoundedSemaphore
    tracked_semaphores = []

    class TrackingBoundedSemaphore:
        def __init__(self, value):
            self._semaphore = real_bounded_semaphore(value)
            self._lock = threading.Lock()
            self.active = 0
            tracked_semaphores.append(self)

        def acquire(self, *args, **kwargs):
            acquired = self._semaphore.acquire(*args, **kwargs)
            if acquired:
                with self._lock:
                    self.active += 1
            return acquired

        def release(self):
            with self._lock:
                self.active -= 1
            self._semaphore.release()

    monkeypatch.setattr(
        repl_runtime_module.threading,
        "BoundedSemaphore",
        TrackingBoundedSemaphore,
    )

    class LLM:
        def __init__(self, cancel_checker):
            self.cancel_checker = cancel_checker

        def ask(self, _prompt, **_kwargs):
            started.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if self.cancel_checker and self.cancel_checker():
                    stopped.set()
                    raise RuntimeError("bridge closed")
                time.sleep(0.01)
            timed_out.set()
            return "late"

    monkeypatch.setattr(
        repl_runtime_module,
        "get_lazy_llm",
        lambda *, cancel_checker=None: LLM(cancel_checker),
    )

    with pytest.raises(NodeExecutionError, match="exited with code 17"):
        HostProcessPythonExecRuntime().execute(
            (
                "import os, threading, time\n"
                "for value in range(24):\n"
                "    threading.Thread(\n"
                "        target=lambda item=value: llm.ask(f'blocked-{item}'),\n"
                "        daemon=True,\n"
                "    ).start()\n"
                "time.sleep(0.3)\n"
                "os._exit(17)"
            ),
            {},
        )

    assert started.wait(0.5)
    assert stopped.wait(0.5)
    assert not timed_out.is_set()
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline and any(
        thread.name.startswith("wf-process-rpc") for thread in threading.enumerate()
    ):
        time.sleep(0.01)
    assert not any(thread.name.startswith("wf-process-rpc") for thread in threading.enumerate())
    assert len(tracked_semaphores) == 1
    assert tracked_semaphores[0].active == 0


def test_process_isolated_runtime_exposes_cooperative_cancel_hooks():
    workflow = Workflow.from_dict({
        "start": "check_cancel",
        "nodes": [
            {
                "id": "check_cancel",
                "type": "python",
                "processIsolated": True,
                "code": (
                    "outputs['cancelled'] = cancelled()\n"
                    "outputs['is_cancelled'] = is_cancelled()"
                ),
            }
        ],
        "edges": [],
    })

    result = WorkflowEngine(
        workflow,
        runtime=PythonExecRuntime(),
        node_timeout_s=3,
    ).run()

    assert result.outputs == {"cancelled": False, "is_cancelled": False}


def test_process_isolated_system_exit_preserves_outputs():
    workflow = Workflow.from_dict({
        "start": "early_return",
        "nodes": [
            {
                "id": "early_return",
                "type": "python",
                "processIsolated": True,
                "code": "outputs['x'] = 1\nraise SystemExit(0)",
            }
        ],
        "edges": [],
    })

    result = WorkflowEngine(
        workflow,
        runtime=PythonExecRuntime(),
        node_timeout_s=3,
    ).run()

    assert result.outputs == {"x": 1}


def test_process_retained_fd_stays_in_parent_and_crosses_node_boundary(tmp_path):
    lease_fd = os.open(tmp_path / "lease.lock", os.O_RDWR | os.O_CREAT, 0o600)
    workflow = Workflow.from_dict({
        "start": "passthrough",
        "nodes": [
            {
                "id": "passthrough",
                "type": "python",
                "processIsolated": True,
                "processRetainFdKeys": ["lease_fd"],
                "code": (
                    "outputs['child_fd'] = inputs.get('lease_fd')\n"
                    "outputs['lease_fd'] = inputs.get('lease_fd')"
                ),
            }
        ],
        "edges": [],
    })

    try:
        result = WorkflowEngine(
            workflow,
            runtime=PythonExecRuntime(),
            node_timeout_s=3,
        ).run({"lease_fd": lease_fd})

        # The child sees a harmless placeholder descriptor, while the parent
        # restores and continues owning the actual lease descriptor.
        assert isinstance(result.outputs["child_fd"], int)
        assert result.outputs["lease_fd"] == lease_fd
        os.fstat(lease_fd)
    finally:
        try:
            os.close(lease_fd)
        except OSError:
            pass


def test_process_retained_fd_is_closed_when_child_fails(tmp_path):
    lease_fd = os.open(tmp_path / "lease.lock", os.O_RDWR | os.O_CREAT, 0o600)
    workflow = Workflow.from_dict({
        "start": "fail",
        "nodes": [
            {
                "id": "fail",
                "type": "python",
                "processIsolated": True,
                "processRetainFdKeys": ["lease_fd"],
                "code": "raise RuntimeError('boom')",
            }
        ],
        "edges": [],
    })

    with pytest.raises(NodeExecutionError, match="boom"):
        WorkflowEngine(
            workflow,
            runtime=PythonExecRuntime(),
            node_timeout_s=3,
        ).run({"lease_fd": lease_fd})

    with pytest.raises(OSError):
        os.fstat(lease_fd)


def test_host_process_windows_launch_does_not_require_posix_shell(monkeypatch):
    real_popen = repl_runtime_module.subprocess.Popen
    script_paths = []

    def checking_popen(args, *popen_args, **popen_kwargs):
        assert args[0] != "sh"
        assert popen_kwargs["encoding"] == "utf-8"
        script_paths.append(args[-1])
        return real_popen(args, *popen_args, **popen_kwargs)

    monkeypatch.setattr(repl_runtime_module.sys, "platform", "win32")
    monkeypatch.setattr(repl_runtime_module.subprocess, "Popen", checking_popen)

    outputs, _stdout = HostProcessPythonExecRuntime().execute(
        "outputs['value'] = inputs['value']",
        {"value": "中文告警"},
    )

    assert outputs == {"value": "中文告警"}
    assert script_paths
    assert all(not os.path.exists(path) for path in script_paths)


def test_node_timeout_none_disabled():
    """When node_timeout_s is None, no per-node timeout is applied."""
    workflow = Workflow.from_dict({
        "name": "no_timeout",
        "start": "a",
        "nodes": [
            {"id": "a", "type": "python", "code": "outputs['x'] = 1", "description": "Quick"},
        ],
        "edges": [],
    })
    engine = WorkflowEngine(
        workflow,
        runtime=PythonExecRuntime(),
        node_timeout_s=None,
    )
    result = engine.run(initial_inputs={}, retain_history=True)
    assert result.steps == 1
    assert result.history[0].outputs["x"] == 1


def test_run_workflow_node_timeout_param():
    """run_workflow accepts node_timeout_s and passes it to engine."""
    workflow = {
        "name": "runner_timeout",
        "start": "s",
        "nodes": [
            {
                "id": "s",
                "type": "python",
                "code": "import time; time.sleep(0.5); outputs['ok'] = 1",
                "description": "Slow",
                "processIsolated": True,
                "timeoutFatal": True,
            },
        ],
        "edges": [],
    }
    result = run_workflow(
        workflow=workflow,
        inputs={},
        node_timeout_s=0.2,
        ensure_requirements=False,
        retain_history=True,
    )
    assert result.status == "FAILED"
    assert "NodeTimeoutError" in (result.error or "")
    assert len(result.history) == 1
    assert result.history[0].get("error") is not None
    assert "节点执行超时" in result.history[0]["error"]


def test_run_workflow_uses_metadata_node_timeout_default():
    """Workflow metadata can override the historical 300s default."""
    workflow = {
        "name": "metadata_timeout",
        "start": "s",
        "nodes": [
            {
                "id": "s",
                "type": "python",
                "code": "import time; time.sleep(0.2); outputs['ok'] = 1",
                "description": "Slow-ish",
                "processIsolated": True,
                "timeoutFatal": True,
            },
        ],
        "edges": [],
        "metadata": {"node_timeout_s": 0.05},
    }
    result = run_workflow(
        workflow=workflow,
        inputs={},
        ensure_requirements=False,
        retain_history=True,
    )
    assert result.status == "FAILED"
    assert "NodeTimeoutError" in (result.error or "")
    assert len(result.history) == 1
    assert "节点执行超时" in (result.history[0].get("error") or "")


def test_run_workflow_explicit_node_timeout_overrides_metadata():
    """Explicit caller timeout should win over workflow metadata."""
    workflow = {
        "name": "metadata_timeout_override",
        "start": "s",
        "nodes": [
            {
                "id": "s",
                "type": "python",
                "code": "import time; time.sleep(0.2); outputs['ok'] = 1",
                "description": "Slow-ish",
                "processIsolated": True,
                "timeoutFatal": True,
            },
        ],
        "edges": [],
        "metadata": {"node_timeout_s": 0.05},
    }
    result = run_workflow(
        workflow=workflow,
        inputs={},
        node_timeout_s=1.0,
        ensure_requirements=False,
        retain_history=True,
    )
    assert result.status == "SUCCEEDED"
    assert len(result.history) == 1
    assert result.history[0].get("error") is None
    assert result.history[0]["outputs"]["ok"] == 1

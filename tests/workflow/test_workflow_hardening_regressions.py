"""Regression tests for workflow engine / center / trigger defects found during the 2026-09 test pass.

Each test pins one fixed behaviour:

* ``_summarize_for_observability`` marks truncated dicts instead of returning a
  50-key dict that reads like the whole payload,
* step results record the node type,
* the run-level ``timeout_s`` interrupts a cooperative node instead of only
  being checked between steps,
* ``http_request`` nodes pass through the ``tool.execute.before`` gate,
* ``llm`` nodes run ``llm.before`` / ``llm.after`` hooks (prompt redaction
  call point) and restore placeholders in the reply,
* trigger dispatches present the legacy-compat carrier to ingress hooks,
* stopping a custom adapter does not leak the adapter task's CancelledError,
* the poller refuses to schedule a definition that fails lint,
* local publishes keep release history, run under a per-workflow lock, and
  stopping an unpublished workflow raises ``WorkflowNotPublishedError``,
* the workflow center takes the display name from ``meta.json``.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.workflow import center, fs_store, poller_manager
from flocks.workflow import llm as llm_module
from flocks.workflow.engine import _summarize_for_observability
from flocks.workflow.runner import run_workflow
from flocks.workflow.triggers import runtime as trigger_runtime_module
from flocks.workflow.triggers.models import TriggerDefinition


def _py(node_id: str, code: str, **extra: Any) -> dict[str, Any]:
    return {"id": node_id, "type": "python", "code": code, **extra}


def _run(workflow: dict[str, Any], inputs: dict[str, Any] | None = None, **kwargs: Any):
    kwargs.setdefault("ensure_requirements", False)
    kwargs.setdefault("retain_history", True)
    kwargs.setdefault("history_mode", "full")
    return run_workflow(workflow=workflow, inputs=inputs or {}, **kwargs)


@pytest.fixture
def hook_pipeline(monkeypatch: pytest.MonkeyPatch):
    """An empty hook pipeline that never loads plugin hooks (Pro or project ones)."""
    HookPipeline.reset()
    monkeypatch.setattr(HookPipeline, "ensure_initialized", AsyncMock(side_effect=lambda *args, **kwargs: tuple(HookPipeline._hooks)))
    yield HookPipeline
    HookPipeline.reset()


@pytest.fixture
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> list[list[dict[str, Any]]]:
    """Replace the workflow LLM provider; records the messages it received."""
    calls: list[list[dict[str, Any]]] = []

    class _Response:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Provider:
        async def chat(self, *, model_id: str, messages: list, temperature: float = 0.2, **_: Any) -> _Response:
            calls.append([m.model_dump() for m in messages])
            return _Response(f"reply:{messages[-1].content}")

    provider = _Provider()
    monkeypatch.setattr(
        llm_module.LLMClient,
        "_build_candidate_targets",
        lambda self: [llm_module._ResolvedTarget(provider_id="fake", model_id="fake-model", source="default")],
    )
    monkeypatch.setattr(llm_module.LLMClient, "_validate_target", lambda self, target: None)
    monkeypatch.setattr(llm_module.LLMClient, "_prepare_provider", lambda self, provider_id: provider)
    return calls


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------
def test_observability_summary_marks_truncated_dicts() -> None:
    small = {f"k{i}": i for i in range(50)}
    assert _summarize_for_observability(small) == small

    big = {f"k{i}": i for i in range(51)}
    summary = _summarize_for_observability(big)
    assert summary["_type"] == "dict" and summary["key_count"] == 51
    assert summary["keys"] == [f"k{i}" for i in range(50)]
    assert summary["preview"] == {"k0": 0, "k1": 1, "k2": 2}


@pytest.mark.parametrize("history_mode", ["full", "summary"])
def test_step_results_carry_node_type(history_mode: str) -> None:
    workflow = {
        "start": "a",
        "nodes": [
            _py("a", "outputs['result'] = True"),
            {"id": "b", "type": "branch"},
            _py("c", "outputs['done'] = 1"),
        ],
        "edges": [
            {"from": "a", "to": "b", "mapping": {"result": "$.result"}},
            {"from": "b", "to": "c", "label": "true", "mapping": {"v": "$.result"}},
        ],
    }
    result = _run(workflow, history_mode=history_mode)
    assert result.status == "SUCCEEDED", result.error
    assert [(step["node_id"], step["node_type"]) for step in result.history] == [("a", "python"), ("b", "branch"), ("c", "python")]


def test_run_timeout_interrupts_cooperative_node() -> None:
    workflow = {
        "start": "slow",
        "nodes": [_py("slow", "import time\nfor i in range(600):\n    if cancelled():\n        break\n    time.sleep(0.05)\noutputs['i'] = i")],
        "edges": [],
    }
    started = time.perf_counter()
    result = _run(workflow, timeout_s=0.5, node_timeout_s=None)
    elapsed = time.perf_counter() - started
    assert result.status == "TIMED_OUT", (result.status, result.error)
    assert "Run timed out" in (result.error or "")
    assert elapsed < 5, f"deadline was only checked between steps ({elapsed:.1f}s)"


def test_http_request_node_is_gated_by_tool_before_hook(hook_pipeline) -> None:
    seen: list[dict[str, Any]] = []

    class _Deny(HookBase):
        async def tool_before(self, ctx):
            seen.append(json.loads(json.dumps(ctx.input, default=str)))
            ctx.output["decision"] = {"action": "deny", "reason": "network target denied"}

    hook_pipeline.register("test-deny-network", _Deny(), order=1)
    workflow = {
        "start": "h",
        "nodes": [{"id": "h", "type": "http_request", "method": "GET", "url": "http://169.254.169.254/latest/meta-data/"}],
        "edges": [],
    }
    result = _run(workflow)
    assert result.status == "FAILED"
    assert "HTTP request blocked by policy: network target denied" in (result.error or "")
    assert len(seen) == 1
    payload = json.dumps(seen[0])
    assert "http_request" in payload and "169.254.169.254" in payload


def test_http_request_node_proceeds_when_hook_allows(hook_pipeline, monkeypatch) -> None:
    import httpx

    def failed_request(client, method, url, **kwargs):
        assert method == "GET" and url == "http://127.0.0.1:9/"
        raise httpx.ConnectError("synthetic connection failure")

    monkeypatch.setattr(httpx.Client, "request", failed_request)
    class _Allow(HookBase):
        async def tool_before(self, ctx):
            ctx.output["decision"] = {"action": "allow"}

    hook_pipeline.register("test-allow-network", _Allow(), order=1)
    workflow = {
        "start": "h",
        "nodes": [{"id": "h", "type": "http_request", "method": "GET", "url": "http://127.0.0.1:9/"}],
        "edges": [],
    }
    result = _run(workflow)
    # A deterministic transport failure proves the policy let the request through.
    assert result.status == "FAILED"
    assert "blocked by policy" not in (result.error or "")
    assert "synthetic connection failure" in (result.error or "")


def test_llm_node_runs_before_and_after_hooks_and_restores_placeholders(hook_pipeline, fake_provider) -> None:
    seen: dict[str, Any] = {}

    class _Redact(HookBase):
        async def llm_before(self, ctx):
            seen["before"] = json.loads(json.dumps(ctx.input))
            request = dict(ctx.input["request"])
            messages = [dict(m) for m in request["messages"]]
            messages[-1]["content"] = messages[-1]["content"].replace("13800138000", "[[PHONE_1]]")
            request["messages"] = messages
            ctx.output["request"] = request
            ctx.output["redaction"] = {"streamTextReplacements": [{"placeholder": "[[PHONE_1]]", "value": "13800138000"}]}

        async def llm_after(self, ctx):
            seen["after"] = json.loads(json.dumps(ctx.output))

    hook_pipeline.register("test-redact", _Redact(), order=1)
    workflow = {"start": "l", "nodes": [{"id": "l", "type": "llm", "prompt": "重复 {{ text }}", "output_key": "out"}], "edges": []}
    result = _run(workflow, {"text": "手机号 13800138000"})

    assert result.status == "SUCCEEDED", result.error
    sent = fake_provider[0][-1]["content"]
    assert "13800138000" not in sent and "[[PHONE_1]]" in sent
    assert "13800138000" in result.outputs["out"]
    assert seen["before"]["request"]["messageCount"] == 1
    assert seen["before"]["model"] == {"providerID": "fake", "modelID": "fake-model"}
    assert seen["after"]["response"]["content"] == result.outputs["out"]


@pytest.mark.asyncio
async def test_llm_node_hooks_run_on_the_server_loop(hook_pipeline, fake_provider) -> None:
    """A hook holding a loop-bound resource (Pro keeps a grpc.aio channel on the
    server loop) must be awaited on that loop, not on the workflow's private
    background loop, otherwise it fails with "attached to a different loop"."""
    from flocks.workflow import _async_runtime

    server_loop = asyncio.get_running_loop()
    _async_runtime.register_primary_loop(server_loop)
    bound_future: asyncio.Future = server_loop.create_future()
    server_loop.call_later(0.05, bound_future.set_result, "channel-ready")
    seen: dict[str, Any] = {}

    class _LoopBound(HookBase):
        async def llm_before(self, ctx):
            seen["loop"] = asyncio.get_running_loop()
            seen["resource"] = await bound_future  # raises RuntimeError on any other loop

    hook_pipeline.register("test-loop-bound", _LoopBound(), order=1)
    workflow = {"start": "l", "nodes": [{"id": "l", "type": "llm", "prompt": "hi {{ text }}", "output_key": "out"}], "edges": []}
    try:
        # The server runs workflows in a worker thread exactly like this.
        result = await asyncio.to_thread(_run, workflow, {"text": "x"})
    finally:
        _async_runtime._clear_primary_loop_for_testing()

    assert result.status == "SUCCEEDED", result.error
    assert seen["loop"] is server_loop and seen["resource"] == "channel-ready"
    assert fake_provider and fake_provider[0][-1]["content"].startswith("hi x")


def test_llm_hook_context_comes_from_tool_registry() -> None:
    registry = SimpleNamespace(_ctx=SimpleNamespace(session_id="sess-1", agent="rex"))
    context = llm_module.llm_hook_context_from_registry(registry)
    assert context["sessionID"] == "sess-1" and context["agent"] == "rex"
    default = llm_module.llm_hook_context_from_registry(None)
    assert default["sessionID"] == llm_module._DEFAULT_LLM_HOOK_CONTEXT["sessionID"]


# ---------------------------------------------------------------------------
# triggers
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_trigger_execution_presents_legacy_compat_carrier(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_execute_with_hooks(payload, effect, **_kwargs):
        captured.update(payload)
        return await effect()

    monkeypatch.setattr(trigger_runtime_module, "execute_with_hooks", _fake_execute_with_hooks)
    runtime = trigger_runtime_module.TriggerRuntime()
    monkeypatch.setattr(runtime, "_execute_workflow_effect", AsyncMock(return_value={"status": "success"}))
    trigger = TriggerDefinition.model_validate({"id": "tw1", "type": "webhook", "auth": {"type": "api_key", "apiKey": "k"}})

    result = await runtime._execute_workflow(workflow_id="wf", workflow_json={}, trigger=trigger, mapped_inputs={"x": 1})

    assert result == {"status": "success"}
    assert captured["operation"] == "workflow.trigger.execute"
    assert captured["legacy_compat"] is True  # top-level: that is where ingress policies read it
    assert captured["evidence"] == {"auth_scheme": "api_key", "trigger_id": "tw1", "trigger_type": "webhook"}
    assert captured["metadata"]["legacy_compat"] is True


@pytest.mark.asyncio
async def test_stop_custom_adapter_swallows_adapter_task_cancellation() -> None:
    runtime = trigger_runtime_module.TriggerRuntime()
    started = asyncio.Event()

    async def _forever() -> None:
        started.set()
        await asyncio.Event().wait()

    key = ("wf", "adapter-1")
    runtime._custom_adapter_tasks[key] = asyncio.create_task(_forever())
    await started.wait()

    await runtime._stop_custom_adapter("wf", "adapter-1")  # must not raise CancelledError

    assert key not in runtime._custom_adapter_tasks
    assert runtime._custom_status[key]["state"] == "stopped"


@pytest.mark.asyncio
async def test_poller_restart_rejects_definition_failing_lint(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = poller_manager.WorkflowPollerManager()

    async def _config(_workflow_id: str, *, kind: str) -> dict[str, Any]:
        return {"enabled": True, "intervalSeconds": 30}

    unmapped = {
        "start": "a",
        "nodes": [_py("a", "outputs['r'] = 1"), _py("b", "y = 1")],
        "edges": [{"from": "a", "to": "b"}],
        "metadata": {"runtime": {"strict_edge_mapping": True}},
    }
    monkeypatch.setattr(poller_manager.WorkflowStore, "get_config", _config)
    monkeypatch.setattr(poller_manager, "read_workflow_from_fs", lambda _workflow_id: {"workflowJson": unmapped})

    status = await manager.restart_workflow("wf-lint")

    assert status["state"] == "failed"
    assert status["error"].startswith("workflow_plan_failed:")
    assert "strict edge mapping" in status["error"].lower()
    assert "wf-lint" not in manager._tasks


# ---------------------------------------------------------------------------
# workflow center
# ---------------------------------------------------------------------------
def _kv_store(monkeypatch: pytest.MonkeyPatch, initial: dict[str, Any]) -> dict[str, Any]:
    store = dict(initial)

    async def _get(key):
        return store.get(str(key))

    async def _put(key, value, value_type: str = "json"):
        store[str(key)] = value

    async def _remove(key):
        return store.pop(str(key), None) is not None

    async def _list(prefix):
        return [k for k in store if k.startswith(str(prefix))]

    monkeypatch.setattr(center.WorkflowStore, "kv_get", _get)
    monkeypatch.setattr(center.WorkflowStore, "kv_put", _put)
    monkeypatch.setattr(center.WorkflowStore, "kv_remove", _remove)
    monkeypatch.setattr(center.WorkflowStore, "kv_list", _list)
    monkeypatch.setattr(center.WorkflowStore, "kv_list_keys", _list)
    return store


def _write_publishable_workflow(tmp_path: Path, workflow_id: str) -> Path:
    path = tmp_path / workflow_id / "workflow.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"id": workflow_id, "start": "n1", "nodes": [_py("n1", "outputs['ok'] = True")], "edges": []}),
        encoding="utf-8",
    )
    return path


def _stub_local_publish(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, healthy: bool) -> None:
    class _Proc:
        pid = 4242

    async def _spawn(*_args, **_kwargs):
        return _Proc()

    async def _allocate(preferred, **_kwargs):
        return preferred or 19000

    async def _snapshot(_workflow_id, release_id, _workflow_json):
        snapshot = tmp_path / f"{release_id}.json"
        snapshot.write_text("{}", encoding="utf-8")
        return snapshot

    monkeypatch.setattr(center, "_allocate_port", _allocate)
    monkeypatch.setattr(center, "_write_release_snapshot", _snapshot)
    monkeypatch.setattr(center, "_stop_existing_runtime_for_publish", AsyncMock(return_value=False))
    monkeypatch.setattr(center, "_is_port_available", lambda _port: True)
    monkeypatch.setattr(center.asyncio, "create_subprocess_exec", _spawn)
    monkeypatch.setattr(center, "_wait_service_healthy", AsyncMock(return_value=healthy))
    monkeypatch.setattr(center, "_stop_local_service", AsyncMock(return_value=None))
    monkeypatch.setattr(center, "_pid_is_running", lambda _pid: False)


@pytest.mark.asyncio
async def test_local_publish_records_release_history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workflow_id = "wf-local-release"
    path = _write_publishable_workflow(tmp_path, workflow_id)
    store = _kv_store(monkeypatch, {f"workflow_registry/{workflow_id}": {"workflowId": workflow_id, "workflowPath": str(path), "publishStatus": "unpublished"}})
    _stub_local_publish(monkeypatch, tmp_path, healthy=True)

    result = await center.publish_workflow_local(workflow_id, port=19000)

    releases = await center.list_workflow_releases(workflow_id)
    assert len(releases) == 1
    release = releases[0]
    assert release["releaseId"] == result["releaseId"] == store[f"workflow_registry/{workflow_id}"]["activeReleaseId"]
    assert release["driver"] == "local" and release["status"] == "active" and release["hostPort"] == 19000
    assert release["activatedAt"] and release["workflowSnapshotPath"].endswith(f"{release['releaseId']}.json")


@pytest.mark.asyncio
async def test_local_publish_marks_release_failed_on_health_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workflow_id = "wf-local-unhealthy"
    path = _write_publishable_workflow(tmp_path, workflow_id)
    store = _kv_store(monkeypatch, {f"workflow_registry/{workflow_id}": {"workflowId": workflow_id, "workflowPath": str(path), "publishStatus": "unpublished"}})
    _stub_local_publish(monkeypatch, tmp_path, healthy=False)

    with pytest.raises(center.WorkflowCenterError, match="failed health check"):
        await center.publish_workflow_local(workflow_id)

    releases = await center.list_workflow_releases(workflow_id)
    assert [r["status"] for r in releases] == ["failed"]
    assert releases[0]["deactivatedAt"]
    assert store[f"workflow_registry/{workflow_id}"]["publishStatus"] == "failed"
    assert f"workflow_runtime/{workflow_id}" not in store


@pytest.mark.asyncio
async def test_publish_and_stop_are_serialized_per_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    release = asyncio.Event()

    async def _slow_local_publish(workflow_id: str, **_kwargs):
        order.append("publish-start")
        await release.wait()
        order.append("publish-end")
        return {"workflowId": workflow_id}

    async def _stop_runtime(workflow_id: str, runtime: dict[str, Any], *, update_registry: bool):
        order.append("stop")
        return {"workflowId": workflow_id, "status": "stopped"}

    monkeypatch.setattr(center, "publish_workflow_local", _slow_local_publish)
    monkeypatch.setattr(center, "_stop_runtime_record", _stop_runtime)
    monkeypatch.setattr(center.WorkflowStore, "kv_get", AsyncMock(return_value={"workflowId": "wf", "driver": "local"}))
    center._WORKFLOW_LIFECYCLE_LOCKS.pop("wf", None)

    publish_task = asyncio.create_task(center.publish_workflow("wf", driver="local"))
    await asyncio.sleep(0.05)
    stop_task = asyncio.create_task(center.stop_workflow_service("wf"))
    await asyncio.sleep(0.05)
    assert order == ["publish-start"]  # stop waits for the in-flight publish
    release.set()
    await asyncio.gather(publish_task, stop_task)
    assert order == ["publish-start", "publish-end", "stop"]


@pytest.mark.asyncio
async def test_stop_unpublished_workflow_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _kv_store(monkeypatch, {"workflow_registry/wf-idle": {"workflowId": "wf-idle", "publishStatus": "unpublished"}})
    stop_local = AsyncMock()
    monkeypatch.setattr(center, "_stop_local_service", stop_local)

    with pytest.raises(center.WorkflowNotPublishedError):
        await center.stop_workflow_service("wf-idle")
    with pytest.raises(center.WorkflowNotFoundError):
        await center.stop_workflow_service("wf-unknown")
    stop_local.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_uses_meta_json_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "workflows"
    named = root / "3fa1c2d4-0000-4000-8000-000000000000"
    named.mkdir(parents=True)
    named.joinpath("workflow.json").write_text(json.dumps({"start": "a", "nodes": [_py("a", "x = 1")], "edges": []}), encoding="utf-8")
    named.joinpath("meta.json").write_text(json.dumps({"id": named.name, "name": "告警降噪"}), encoding="utf-8")
    bare = root / "bare_dir"
    bare.mkdir()
    bare.joinpath("workflow.json").write_text(json.dumps({"start": "a", "nodes": [_py("a", "x = 1")], "edges": []}), encoding="utf-8")
    _kv_store(monkeypatch, {})
    monkeypatch.setattr(fs_store, "_workspace_root", None)

    by_id: dict[str, dict[str, Any]] = {}
    await center._scan_workflow_dir(root, "global", by_id)

    assert by_id[named.name]["name"] == "告警降噪"
    assert by_id["bare_dir"]["name"] == "bare_dir"

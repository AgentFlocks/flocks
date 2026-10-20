"""Workflow work the engine does itself is held to the tool gate, and every
workflow model call can be paired with its response.

* python and logic nodes pass ``tool.execute.before`` as ``workflow_python``
  and report their outcome to ``tool.execute.after``,
* http_request nodes report their outcome too,
* each workflow model call carries its own ``model_call_id`` and a caller, so a
  registered response transform can undo what ``llm.before`` did.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest

from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.transform import register_response_transform, reset_response_transform
from flocks.workflow import llm as llm_module
from flocks.workflow.engine import WORKFLOW_CODE_TOOL_NAME
from flocks.workflow.runner import run_workflow


def _run(workflow: dict[str, Any], inputs: dict[str, Any] | None = None):
    return run_workflow(
        workflow=workflow,
        inputs=inputs or {},
        ensure_requirements=False,
        retain_history=True,
        history_mode="full",
    )


@pytest.fixture
def hook_pipeline(monkeypatch: pytest.MonkeyPatch):
    """An empty hook pipeline that never loads plugin hooks (Pro or project ones)."""
    HookPipeline.reset()
    monkeypatch.setattr(
        HookPipeline,
        "ensure_initialized",
        AsyncMock(side_effect=lambda *args, **kwargs: tuple(HookPipeline._hooks)),
    )
    yield HookPipeline
    HookPipeline.reset()


@pytest.fixture(autouse=True)
def _clean_transform():
    reset_response_transform()
    yield
    reset_response_transform()


@pytest.fixture
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> list[list[dict[str, Any]]]:
    calls: list[list[dict[str, Any]]] = []

    class _Response:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Provider:
        async def chat(self, *, model_id: str, messages: list, **_: Any) -> _Response:
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


class _Recorder(HookBase):
    """Records gate payloads and outcomes; denies the tools it is told to."""

    def __init__(self, deny: set[str] | None = None) -> None:
        self.deny = deny or set()
        self.before: list[dict[str, Any]] = []
        self.after: list[dict[str, Any]] = []

    @staticmethod
    def _tool_name(payload: Dict[str, Any]) -> str:
        return payload["tool_execution"]["tool"]["name"]

    async def tool_before(self, ctx):
        payload = json.loads(json.dumps(ctx.input, default=str))
        self.before.append(payload)
        if self._tool_name(payload) in self.deny:
            ctx.output["decision"] = {"action": "deny", "reason": "code not allowed here"}

    async def tool_after(self, ctx):
        self.after.append(json.loads(json.dumps(ctx.input, default=str)))


def test_python_node_is_refused_when_the_gate_denies_it(hook_pipeline) -> None:
    recorder = _Recorder(deny={WORKFLOW_CODE_TOOL_NAME})
    hook_pipeline.register("test-recorder", recorder, order=1)
    workflow = {"start": "p", "nodes": [{"id": "p", "type": "python", "code": "outputs['x'] = 1"}], "edges": []}

    result = _run(workflow)

    assert result.status == "FAILED"
    assert "Workflow code blocked by policy: code not allowed here" in (result.error or "")
    assert len(recorder.before) == 1
    tool = recorder.before[0]["tool_execution"]["tool"]
    assert tool["name"] == WORKFLOW_CODE_TOOL_NAME
    assert tool["validated_input"]["code"] == "outputs['x'] = 1"
    assert len(tool["validated_input"]["code_sha256"]) == 64
    assert tool["validated_input"]["node_type"] == "python"
    # The refusal is reported as an outcome, the code never ran.
    assert [entry["outcome"]["status"] for entry in recorder.after] == ["deny"]


def test_python_node_outcome_reaches_the_after_stage(hook_pipeline) -> None:
    recorder = _Recorder()
    hook_pipeline.register("test-recorder", recorder, order=1)
    workflow = {"start": "p", "nodes": [{"id": "p", "type": "python", "code": "outputs['x'] = 41 + 1"}], "edges": []}

    result = _run(workflow)

    assert result.status == "SUCCEEDED", result.error
    assert result.outputs["x"] == 42
    assert [entry["outcome"]["status"] for entry in recorder.after] == ["success"]
    # The gate payload names the caller, like every tool payload.
    assert recorder.before[0]["callerID"]


def test_python_node_failure_is_reported_as_an_error_outcome(hook_pipeline) -> None:
    recorder = _Recorder()
    hook_pipeline.register("test-recorder", recorder, order=1)
    workflow = {"start": "p", "nodes": [{"id": "p", "type": "python", "code": "raise ValueError('boom')"}], "edges": []}

    result = _run(workflow)

    assert result.status == "FAILED"
    assert [entry["outcome"]["status"] for entry in recorder.after] == ["error"]


def test_logic_node_code_is_gated_too(hook_pipeline) -> None:
    recorder = _Recorder(deny={WORKFLOW_CODE_TOOL_NAME})
    hook_pipeline.register("test-recorder", recorder, order=1)
    workflow = {
        "start": "g",
        "nodes": [{"id": "g", "type": "logic", "description": "mark ok", "code": "outputs['ok'] = True"}],
        "edges": [],
    }

    result = _run(workflow)

    assert result.status == "FAILED"
    assert "Workflow code blocked by policy" in (result.error or "")
    assert recorder.before[0]["tool_execution"]["tool"]["validated_input"]["node_type"] == "logic"


def test_python_node_runs_unchanged_without_a_gate(hook_pipeline) -> None:
    workflow = {"start": "p", "nodes": [{"id": "p", "type": "python", "code": "outputs['x'] = 7"}], "edges": []}

    result = _run(workflow)

    assert result.status == "SUCCEEDED", result.error
    assert result.outputs["x"] == 7


def test_http_request_outcome_reaches_the_after_stage(hook_pipeline) -> None:
    recorder = _Recorder()
    hook_pipeline.register("test-recorder", recorder, order=1)
    workflow = {
        "start": "h",
        "nodes": [{"id": "h", "type": "http_request", "method": "GET", "url": "http://127.0.0.1:9/"}],
        "edges": [],
    }

    result = _run(workflow)

    # Nothing listens on port 9: the gate allowed it, the request failed.
    assert result.status == "FAILED"
    assert [entry["outcome"]["status"] for entry in recorder.after] == ["error"]


class _PerCallTransform:
    """Restores what one model call's ``llm.before`` replaced, keyed on its id."""

    def __init__(self) -> None:
        self.mappings: Dict[str, Dict[str, str]] = {}
        self.restored_for: list[str] = []

    @property
    def name(self) -> str:
        return "test-per-call"

    async def open_stream(self, metadata):  # pragma: no cover - not streamed here
        raise AssertionError("workflow calls are not streamed")

    async def transform_text(self, text: str, metadata: Dict[str, Any]) -> str:
        call_id = metadata.get("model_call_id") or ""
        self.restored_for.append(call_id)
        for placeholder, value in self.mappings.get(call_id, {}).items():
            text = text.replace(placeholder, value)
        return text

    async def transform_value(self, value: Any, metadata: Dict[str, Any]) -> Any:
        return value


def test_each_workflow_model_call_is_named_and_restored(hook_pipeline, fake_provider) -> None:
    transform = _PerCallTransform()
    register_response_transform(transform)
    seen: list[dict[str, Any]] = []

    class _Redact(HookBase):
        async def llm_before(self, ctx):
            meta = json.loads(json.dumps(ctx.input))
            seen.append(meta)
            request = dict(ctx.input["request"])
            messages = [dict(m) for m in request["messages"]]
            messages[-1]["content"] = messages[-1]["content"].replace("13800138000", "[[P]]")
            request["messages"] = messages
            ctx.output["request"] = request
            transform.mappings[meta["model_call_id"]] = {"[[P]]": "13800138000"}

    hook_pipeline.register("test-redact", _Redact(), order=1)
    workflow = {
        "start": "a",
        "nodes": [
            {"id": "a", "type": "llm", "prompt": "第一 {{ text }}", "output_key": "first"},
            {"id": "b", "type": "llm", "prompt": "第二 {{ text }}", "output_key": "second"},
        ],
        "edges": [{"from": "a", "to": "b"}],
    }

    result = _run(workflow, {"text": "13800138000"})

    assert result.status == "SUCCEEDED", result.error
    # The model only ever saw the placeholder; the outputs carry the value.
    assert all("13800138000" not in call[-1]["content"] for call in fake_provider)
    by_node = {step["node_id"]: step["outputs"] for step in result.history}
    assert "13800138000" in by_node["a"]["first"] and "13800138000" in by_node["b"]["second"]
    call_ids = [meta["model_call_id"] for meta in seen]
    assert len(set(call_ids)) == 2 and all(call_id.startswith("workflow_llm:") for call_id in call_ids)
    assert transform.restored_for == call_ids
    # No session started this run: the system caller.
    assert {meta["callerID"] for meta in seen} == {"service:flocks"}
    # messageID stays empty: a workflow must never rewrite a chat message.
    assert all(meta["messageID"] == "" for meta in seen)


class _Patcher(HookBase):
    def __init__(self, patch: Dict[str, Any]) -> None:
        self.patch = patch

    async def tool_before(self, ctx):
        ctx.output["decision"] = {"action": "allow", "validated_input_patch": dict(self.patch)}


def test_python_node_runs_the_code_the_gate_authorised(hook_pipeline) -> None:
    patched = "outputs['x'] = 'patched'"
    recorder = _Recorder()
    hook_pipeline.register("patcher", _Patcher({"code": patched}), order=1)
    hook_pipeline.register("test-recorder", recorder, order=2)
    workflow = {"start": "p", "nodes": [{"id": "p", "type": "python", "code": "outputs['x'] = 'original'"}], "edges": []}

    result = _run(workflow)

    assert result.status == "SUCCEEDED", result.error
    assert result.outputs["x"] == "patched"
    # The outcome is reported against what ran; what was asked for stays in raw_input.
    tool = recorder.after[0]["tool_execution"]["tool"]
    assert tool["validated_input"]["code"] == patched
    assert tool["validated_input"]["code_sha256"] == hashlib.sha256(patched.encode("utf-8")).hexdigest()
    assert tool["raw_input"]["code"] == "outputs['x'] = 'original'"


def test_python_node_refuses_a_patch_it_cannot_apply(hook_pipeline) -> None:
    hook_pipeline.register("patcher", _Patcher({"timeout": 5}), order=1)
    workflow = {"start": "p", "nodes": [{"id": "p", "type": "python", "code": "outputs['x'] = 1"}], "edges": []}

    result = _run(workflow)

    assert result.status == "FAILED"
    assert "cannot apply: timeout" in (result.error or "")


def test_http_request_sends_the_patched_target(hook_pipeline, monkeypatch) -> None:
    from flocks.workflow import engine as engine_module

    sent: list[tuple[str, str]] = []

    class _Response:
        status_code = 200
        text = "ok"

        def json(self):
            return {"ok": True}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def request(self, method, url, **kwargs):
            sent.append((method, url))
            return _Response()

    monkeypatch.setattr(engine_module, "sync_client", lambda **kwargs: _Client())
    recorder = _Recorder()
    hook_pipeline.register("patcher", _Patcher({"url": "http://10.0.0.5/api", "method": "post"}), order=1)
    hook_pipeline.register("test-recorder", recorder, order=2)
    workflow = {
        "start": "h",
        "nodes": [{"id": "h", "type": "http_request", "method": "GET", "url": "http://[[HOST_1]]/api"}],
        "edges": [],
    }

    result = _run(workflow)

    assert result.status == "SUCCEEDED", result.error
    assert sent == [("POST", "http://10.0.0.5/api")]
    tool = recorder.after[0]["tool_execution"]["tool"]
    assert tool["validated_input"] == {"url": "http://10.0.0.5/api", "method": "POST"}
    assert tool["raw_input"] == {"url": "http://[[HOST_1]]/api", "method": "GET"}


def test_gate_names_the_project_on_the_probe_and_both_stages(hook_pipeline, monkeypatch) -> None:
    """Project-level hooks are found by the payload's workspace/session; the
    probe and both stage payloads must carry the same ones."""
    probes: list[dict[str, Any]] = []
    real_probe = HookPipeline.has_stage_handlers

    async def _recording_probe(stage, input_data=None):
        probes.append(dict(input_data or {}))
        return await real_probe(stage, input_data)

    monkeypatch.setattr(HookPipeline, "has_stage_handlers", _recording_probe)
    recorder = _Recorder()
    hook_pipeline.register("test-recorder", recorder, order=1)
    workflow = {"start": "p", "nodes": [{"id": "p", "type": "python", "code": "outputs['x'] = 1"}], "edges": []}

    result = _run(workflow)

    assert result.status == "SUCCEEDED", result.error
    gate_probes = [p for p in probes if p.get("tool", {}).get("name") == WORKFLOW_CODE_TOOL_NAME]
    assert gate_probes and all(p.get("workspace") and p.get("sessionID") for p in gate_probes)
    before, after = recorder.before[0], recorder.after[0]
    for key in ("workspace", "sessionID"):
        assert before[key] == after[key] == gate_probes[0][key]


def test_after_only_observer_still_gets_the_outcome(hook_pipeline) -> None:
    class _Observer(HookBase):
        def __init__(self) -> None:
            self.after: list[dict[str, Any]] = []

        async def tool_after(self, ctx):
            self.after.append(json.loads(json.dumps(ctx.input, default=str)))

    observer = _Observer()
    hook_pipeline.register("observer", observer, order=1)
    workflow = {"start": "p", "nodes": [{"id": "p", "type": "python", "code": "outputs['x'] = 2"}], "edges": []}

    result = _run(workflow)

    assert result.status == "SUCCEEDED", result.error
    assert [entry["outcome"]["status"] for entry in observer.after] == ["success"]
    assert observer.after[0]["tool_execution"]["tool"]["name"] == WORKFLOW_CODE_TOOL_NAME


def test_logic_code_generation_names_the_run(hook_pipeline, monkeypatch) -> None:
    """The model call that writes a logic node's code carries the run's hook
    context, through a client of its own, not the shared default."""
    from flocks.workflow import llm as llm_mod

    contexts: list[dict[str, Any]] = []

    class _Client:
        def __init__(self, hook_context):
            self.hook_context = hook_context

        def ask(self, prompt, **kwargs):
            contexts.append(dict(self.hook_context or {}))
            return "```python\noutputs['answer'] = 42\n```"

    monkeypatch.setattr(llm_mod, "get_llm_client", lambda **kwargs: _Client(kwargs.get("hook_context")))
    workflow = {
        "start": "g",
        "nodes": [{"id": "g", "type": "logic", "description": "set answer to 42"}],
        "edges": [],
    }

    result = run_workflow(
        workflow=workflow, inputs={}, ensure_requirements=False, retain_history=True,
        history_mode="full", use_llm=True,
    )

    assert result.status == "SUCCEEDED", result.error
    assert result.outputs["answer"] == 42
    assert contexts and contexts[0].get("sessionID") and contexts[0].get("workspace")


def test_logic_code_generation_binds_the_run_to_an_explicit_client(monkeypatch, tmp_path) -> None:
    """A generator given its own client keeps that client's model, but the
    model call still names the run: through a copy per generation, so the
    shared client is never modified and parallel generations never share one."""
    from types import SimpleNamespace

    from flocks.tool import ToolContext
    from flocks.workflow.code_gen import LLMCodeGen
    from flocks.workflow.engine import WorkflowEngine
    from flocks.workflow.llm import LLMClient
    from flocks.workflow.models import Workflow

    calls: list[tuple[Any, dict[str, Any], str, str]] = []

    def _ask(self, prompt, **kwargs):
        calls.append((self, dict(self.hook_context), self.provider_id, self.model))
        return "```python\noutputs['answer'] = 42\n```"

    monkeypatch.setattr(LLMClient, "ask", _ask)
    explicit = LLMClient.__new__(LLMClient)  # skips the provider and config lookups
    explicit.provider_id, explicit.model = "custom-provider", "custom-model"
    explicit.api_key = explicit.base_url = explicit.cancel_checker = None  # secret-guard: allow (a test user / no key, not a secret)
    explicit.hook_context = {"sessionID": "someone-else", "agent": "other", "trace_label": "kept"}

    workflow = Workflow.from_dict({
        "start": "g",
        "nodes": [{"id": "g", "type": "logic", "description": "set answer to 42"}],
        "edges": [],
    })
    engine = WorkflowEngine(workflow=workflow, code_gen=LLMCodeGen(client=explicit))
    ctx = ToolContext(
        session_id="session-run-1", message_id="m-1", agent="analyst",
        extra={"workspace_dir": str(tmp_path)},
    )
    runtime = SimpleNamespace(tool_registry=SimpleNamespace(_ctx=ctx))
    node = workflow.nodes[0]

    first = engine._generate_logic_code(node, runtime)
    second = engine._generate_logic_code(node, runtime)

    assert "outputs['answer'] = 42" in first and first == second
    (first_client, context, provider, model), (second_client, *_rest) = calls
    assert context["sessionID"] == "session-run-1"
    assert context["workspace"] == str(tmp_path)
    assert context["agent"] == "analyst"
    assert context["trace_label"] == "kept"
    assert (provider, model) == ("custom-provider", "custom-model")
    assert first_client is not explicit and second_client is not first_client
    assert explicit.hook_context["sessionID"] == "someone-else"
    assert engine.code_gen.client is explicit

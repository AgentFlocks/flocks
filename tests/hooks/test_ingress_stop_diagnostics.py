"""Stop provenance, primary-error preservation and bounded ingress audit."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from flocks.audit import get_sink, register_sink
from flocks.hooks import execution
from flocks.hooks.execution import (
    ExecutionStopped,
    execute_with_hooks,
    execution_stop_diagnostics,
)
from flocks.hooks.pipeline import HookBase, HookContext, HookPipeline
from flocks.plugin import PluginLoader


@pytest.fixture(autouse=True)
def isolated_hooks(monkeypatch):
    original_sink = get_sink()
    HookPipeline.reset()
    monkeypatch.setattr(HookPipeline, "_resolve_project_dir", AsyncMock(return_value=None))
    monkeypatch.setattr(HookPipeline, "ensure_initialized", AsyncMock())
    monkeypatch.setattr(PluginLoader, "has_runtime_critical_entrypoint_failure", lambda: False)
    yield
    register_sink(original_sink)
    HookPipeline.reset()


@pytest.fixture
def audits():
    records = []

    class Sink:
        @classmethod
        async def emit(cls, event_type, payload):
            records.append((event_type, payload))

    register_sink(Sink)
    return records


async def run_ingress(effect=None, **payload):
    return await execute_with_hooks(
        {"operation": "workflow.trigger.syslog", "workflow_id": "stream_alert_denoise", **payload},
        effect or AsyncMock(return_value="ok"),
        before=HookPipeline.run_ingress_before,
        after=HookPipeline.run_ingress_after,
    )


def deny(detail=None):
    class Deny(HookBase):
        async def ingress_before(self, ctx):
            return {"execution": {"stop": True, "detail": detail}}

    HookPipeline.register("test.identity", Deny(), order=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("after_failure", [False, True])
async def test_first_stop_keeps_reason_and_source_through_later_hooks(audits, after_failure):
    deny("identity binding missing")

    class Later(HookBase):
        async def ingress_before(self, ctx):
            return {"execution": {"stop": True, "detail": "different reason"}}

        async def ingress_after(self, ctx):
            if after_failure:
                raise RuntimeError("secondary cleanup failure")
            return {"execution": {"stop": True}}

    HookPipeline.register("test.later", Later(), order=20, critical=True)
    effect = AsyncMock()
    with pytest.raises(ExecutionStopped, match="^identity binding missing$") as caught:
        await run_ingress(effect)
    effect.assert_not_awaited()
    assert execution_stop_diagnostics(caught.value) == {
        "stop_source": "test.identity",
        "stop_stage": "ingress.before",
        "stop_reason": "extension_stop",
        "stop_detail_present": True,
    }
    assert len(audits) == 1
    assert "test.identity" in audits[0][1]["reason"]
    assert "different reason" not in audits[0][1]["reason"]


@pytest.mark.asyncio
async def test_anonymous_stop_names_actual_hook_and_stage(audits):
    deny()
    with pytest.raises(ExecutionStopped, match=r"hook=test.identity, stage=ingress.before") as caught:
        await run_ingress()
    assert caught.value.stop_detail is None
    assert audits == [("ingress.stopped", {
        "status": "failed",
        "phase": "ingress.before",
        "entry": "workflow.trigger.syslog",
        "resource": {"type": "workflow", "id": "stream_alert_denoise"},
        "reason": "stop_source=test.identity; stop_stage=ingress.before; stop_reason=extension_stop",
    })]


@pytest.mark.asyncio
async def test_isolated_failure_after_stop_keeps_the_requesting_hook(audits):
    class First(HookBase):
        async def ingress_before(self, ctx):
            ctx.output["execution"] = {"stop": True}
            raise RuntimeError("isolated hook failed after stopping")

    class Later(HookBase):
        async def ingress_before(self, ctx):
            return {"execution": {"stop": True, "detail": "later reason"}}

    HookPipeline.register("test.first", First(), order=10)
    HookPipeline.register("test.later", Later(), order=20)
    with pytest.raises(ExecutionStopped) as caught:
        await run_ingress()
    assert caught.value.stop_source == "test.first"
    assert caught.value.stop_detail is None
    assert "later reason" not in str(caught.value)


@pytest.mark.asyncio
async def test_after_stop_has_after_provenance_and_audits_once(audits):
    class StopAfter(HookBase):
        async def ingress_after(self, ctx):
            return {"execution": {"stop": True}}

    HookPipeline.register("test.after", StopAfter())
    effect = AsyncMock(return_value="done")
    with pytest.raises(ExecutionStopped) as caught:
        await run_ingress(effect)
    effect.assert_awaited_once()
    assert caught.value.stop_source == "test.after"
    assert caught.value.stop_stage == "ingress.after"
    assert len(audits) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("secondary", ["stop", "error"])
async def test_critical_entrypoint_root_cause_survives_after(audits, monkeypatch, secondary):
    monkeypatch.setattr(PluginLoader, "has_runtime_critical_entrypoint_failure", lambda: True)

    class After(HookBase):
        async def ingress_after(self, ctx):
            if secondary == "error":
                raise RuntimeError("after failed")
            return {"execution": {"stop": True}}

    HookPipeline.register("test.after", After(), critical=True)
    with pytest.raises(ExecutionStopped, match="^critical plugin entrypoint failure$") as caught:
        await run_ingress()
    assert caught.value.stop_reason == "critical_entrypoint_failure"
    assert caught.value.stop_source == "plugin.loader"
    assert len(audits) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["before", "effect"])
@pytest.mark.parametrize("secondary", ["stop", "error"])
async def test_original_exception_survives_after(location, secondary):
    primary = RuntimeError("original problem")

    async def before(payload):
        if location == "before":
            raise primary
        return HookContext("noop", payload)

    async def after(payload):
        assert payload["error"] is primary
        if secondary == "error":
            raise ValueError("after problem")
        return HookContext("noop", payload, {"execution": {"stop": True}})

    with pytest.raises(RuntimeError) as caught:
        await execute_with_hooks({}, AsyncMock(side_effect=primary), before=before, after=after)
    assert caught.value is primary


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["before", "effect"])
@pytest.mark.parametrize("secondary", ["stop", "error"])
async def test_original_cancellation_is_never_replaced(location, secondary):
    cancellation = asyncio.CancelledError("cancel original")

    async def before(payload):
        if location == "before":
            raise cancellation
        return HookContext("noop", payload)

    async def after(payload):
        if secondary == "error":
            raise RuntimeError("cleanup failed")
        return HookContext("noop", payload, {"execution": {"stop": True}})

    with pytest.raises(asyncio.CancelledError) as caught:
        await execute_with_hooks({}, AsyncMock(side_effect=cancellation), before=before, after=after)
    assert caught.value is cancellation


@pytest.mark.asyncio
async def test_new_cancellation_during_after_is_propagated(audits):
    deny("blocked")

    class After(HookBase):
        async def ingress_after(self, ctx):
            raise asyncio.CancelledError("cancel cleanup")

    HookPipeline.register("test.after", After())
    with pytest.raises(asyncio.CancelledError):
        await run_ingress()
    assert audits == []


@pytest.mark.asyncio
async def test_success_has_no_audit_io(audits):
    assert await run_ingress() == "ok"
    assert audits == []


@pytest.mark.asyncio
async def test_non_ingress_stop_does_not_emit_ingress_audit(audits):
    async def before(payload):
        return HookContext("tool.execute.before", payload, {"execution": {"stop": True}})

    with pytest.raises(ExecutionStopped):
        await execute_with_hooks({}, AsyncMock(), before=before)
    assert audits == []


@pytest.mark.asyncio
@pytest.mark.parametrize("detail", [
    'denied token="private-token" payload={"body":"private-syslog"}',
    'password "test-secret"',
    'api-key test-key',
    '密码为 test-secret',
    '<134>Sep 30 12:18:36 source-host raw syslog test-secret',
    'ordinary-looking text that still must never be persisted',
])
async def test_ingress_audit_never_copies_payload_or_credential_details(audits, detail):
    deny(detail)
    with pytest.raises(ExecutionStopped) as caught:
        await run_ingress(
            event={"body": "private-syslog"},
            trigger={"secret": "private-trigger"},
            context={"email": "owner@example.test"},
        )
    facts = execution_stop_diagnostics(caught.value)
    assert str(caught.value) == detail
    assert caught.value.stop_detail == detail
    assert facts["stop_detail_present"] is True
    assert "stop_detail" not in facts
    serialized = json.dumps([audits, facts], ensure_ascii=False)
    assert "private-" not in serialized
    assert "owner@example" not in serialized
    assert "test-secret" not in serialized
    assert "test-key" not in serialized
    assert "source-host" not in serialized
    assert detail not in serialized
    assert "detail omitted" in serialized
    assert set(audits[0][1]) == {"status", "phase", "entry", "resource", "reason"}


@pytest.mark.asyncio
async def test_failing_audit_does_not_replace_denial():
    class Sink:
        @classmethod
        async def emit(cls, event_type, payload):
            raise OSError("audit unavailable")

    register_sink(Sink)
    deny("original denial")
    with pytest.raises(ExecutionStopped, match="^original denial$"):
        await run_ingress()


@pytest.mark.asyncio
async def test_cancel_resistant_sink_is_bounded_and_caps_pending_work(monkeypatch):
    monkeypatch.setattr(execution, "_STOP_AUDIT_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(execution, "_MAX_PENDING_STOP_AUDITS", 1)
    release = asyncio.Event()
    calls = []

    class Sink:
        @classmethod
        async def emit(cls, event_type, payload):
            calls.append(payload)
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()

    register_sink(Sink)
    deny()
    try:
        for _ in range(2):
            with pytest.raises(ExecutionStopped):
                await asyncio.wait_for(run_ingress(), timeout=0.3)
        assert len(calls) == 1
        assert len(execution._pending_stop_audits) == 1
    finally:
        release.set()
        await asyncio.gather(*execution._pending_stop_audits)
        await asyncio.sleep(0)
    assert not execution._pending_stop_audits


@pytest.mark.asyncio
async def test_cancellation_during_audit_is_propagated(monkeypatch):
    monkeypatch.setattr(execution, "_STOP_AUDIT_TIMEOUT_SECONDS", 10)
    started = asyncio.Event()

    class Sink:
        @classmethod
        async def emit(cls, event_type, payload):
            started.set()
            await asyncio.Event().wait()

    register_sink(Sink)
    deny()
    task = asyncio.create_task(run_ingress())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert not execution._pending_stop_audits


def test_diagnostics_are_bounded_and_ordinary_errors_are_unchanged():
    assert execution_stop_diagnostics(ValueError("private")) == {}
    error = ExecutionStopped(
        "private", source="bad\nsource", stage="ingress.before",
        reason="private-reason-text", detail="x" * 1000,
    )
    facts = execution_stop_diagnostics(error)
    assert "stop_source" not in facts
    assert facts["stop_detail_present"] is True
    assert "stop_detail" not in facts
    assert facts["stop_reason"] == "extension_stop"

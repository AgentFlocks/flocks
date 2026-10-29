from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest

from flocks.workflow import health_notifications as health
from flocks.workflow import poller_manager as poller
from flocks.workflow import notification_delivery as delivery_module


@pytest.fixture
def delivery(monkeypatch, tmp_path):
    calls = []
    replies = [{"success": True}]
    registries = []
    def registry(context):
        registries.append(context)
        def send(name, **kwargs):
            assert name == "channel_message"
            calls.append(kwargs)
            return replies.pop(0) if replies else {"success": True}
        return SimpleNamespace(run_safe=send)
    monkeypatch.setattr(health, "get_tool_registry", registry)
    monkeypatch.setattr(delivery_module, "_configured_session_bindings", lambda session_id, **kwargs: [
        SimpleNamespace(session_id=session_id, channel_id="feishu", account_id="account", chat_id="chat")])
    clock = [1000.0]
    monkeypatch.setattr(health.time, "time", lambda: clock[0])
    return SimpleNamespace(notifier=health.WorkflowHealthNotifier(root=tmp_path), calls=calls,
                           replies=replies, contexts=registries, clock=clock, root=tmp_path)


CONFIG = {"timeoutSeconds": 120, "inputs": {"notify_session_id": "explicit-session", "notify_channel_type": "feishu"}}


@pytest.mark.asyncio
async def test_failure_and_timeout_notify_explicit_target_without_llm(delivery):
    original_context = SimpleNamespace(agent="rex", extra={"workflow_temp_parent": True})
    report = await delivery.notifier.notify("monitor", CONFIG, status="timeout", error="node timed out", tool_context=original_context)
    assert report["status"] == "sent"
    assert len(delivery.calls) == 1
    assert delivery.calls[0]["session_id"] == "explicit-session"
    assert "channel_type" not in delivery.calls[0]
    assert delivery.calls[0]["chat_id"] == "chat"
    assert delivery.contexts[0].session_id == "explicit-session"
    assert "workflow_temp_parent" not in delivery.contexts[0].extra


@pytest.mark.asyncio
async def test_same_fault_with_different_run_uuids_is_rate_limited(delivery):
    for run in ("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"):
        await delivery.notifier.notify("monitor", CONFIG, status="timeout", error=f"Run timed out: run_id={run} timeout_s=120")
    assert len(delivery.calls) == 1


@pytest.mark.asyncio
async def test_failed_delivery_remains_durable_and_retries_without_new_fault(delivery):
    delivery.replies[:] = [{"success": False, "error": "offline"}, {"success": True}]
    first = await delivery.notifier.notify("monitor", CONFIG, status="error", error="database is locked")
    assert first["status"] == "failed" and first["notify_queued"] == 1
    delivery.clock[0] += 61
    retried = await delivery.notifier.notify("monitor", CONFIG, status="error", error="database is locked")
    assert retried["status"] == "sent" and retried["notify_queued"] == 0
    assert len(delivery.calls) == 2


@pytest.mark.asyncio
async def test_recovered_success_round_drains_old_failure_without_creating_new_fault(delivery):
    delivery.replies[:] = [{"success": False, "error": "offline"}, {"success": True}]
    failed = await delivery.notifier.notify("monitor", CONFIG, status="timeout", error="node timeout")
    assert failed["notify_queued"] == 1
    delivery.clock[0] += 61
    recovered = await delivery.notifier.notify("monitor", CONFIG, status="success", error="")
    assert recovered["notify_sent"] == 1 and recovered["notify_queued"] == 0
    assert len(delivery.calls) == 2
    assert delivery.calls[0]["message"] == delivery.calls[1]["message"]
    await delivery.notifier.notify("monitor", CONFIG, status="success", error="")
    assert len(delivery.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("config, cancelled, status", [
    ({"inputs": {}}, False, "error"),
    ({"inputs": {"notify_session_id": "explicit", "notify_channel_enabled": "false"}}, False, "error"),
    (CONFIG, True, "error"), (CONFIG, False, "cancelled"), (CONFIG, False, "success"),
])
async def test_unconfigured_disabled_cancelled_or_success_never_send(delivery, config, cancelled, status):
    await delivery.notifier.notify("monitor", config, status=status, error="test", cancelled=cancelled)
    assert not delivery.calls and not delivery.contexts
    assert not list(delivery.root.rglob("pending.json"))


@pytest.mark.asyncio
async def test_wait_deadline_retains_one_worker_instead_of_abandoning_threads(delivery, monkeypatch):
    release = threading.Event()
    started = threading.Event()
    calls = []
    def slow_delivery(*args, **kwargs):
        calls.append(1)
        started.set()
        release.wait(timeout=3)
        return {"notify_sent": 0, "notify_failed": 1, "notify_queued": 1}
    monkeypatch.setattr(delivery.notifier, "_deliver", slow_delivery)
    delivery.notifier.wait_seconds = 0.01
    try:
        result = await delivery.notifier.notify("monitor", CONFIG, status="error", error="test")
        assert result["status"] == "in_flight" and started.is_set()
        again = await delivery.notifier.notify("monitor", CONFIG, status="error", error="test")
        assert again["status"] == "in_flight" and len(calls) == 1
    finally:
        release.set()
        await delivery.notifier._workers["monitor"]


@pytest.mark.asyncio
async def test_different_fault_is_enqueued_before_reusing_inflight_worker(delivery, monkeypatch):
    release = threading.Event()
    calls = []
    def slow_delivery(*args):
        calls.append(1)
        release.wait(timeout=3)
        return {"status": "queued", "notify_queued": 2}
    monkeypatch.setattr(delivery.notifier, "_deliver", slow_delivery)
    delivery.notifier.wait_seconds = 0.01
    try:
        await delivery.notifier.notify("monitor", CONFIG, status="error", error="first fault")
        result = await delivery.notifier.notify("monitor", CONFIG, status="error", error="different fault")
        assert result["status"] == "in_flight" and len(calls) == 1
        pending = json.loads(next(delivery.root.rglob("pending.json")).read_text())["pending"]
        assert len(pending) == 2
        assert any("first fault" in item["message"] for item in pending.values())
        assert any("different fault" in item["message"] for item in pending.values())
    finally:
        release.set()
        await delivery.notifier._workers["monitor"]


@pytest.mark.asyncio
async def test_cancelled_callers_do_not_release_queue_lock_before_io_finishes(delivery, monkeypatch):
    release = threading.Event()
    started = threading.Event()
    active, peak = [0], [0]
    mutex = threading.Lock()
    original = delivery.notifier._enqueue
    def blocked_enqueue(*args):
        with mutex:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        started.set()
        try:
            release.wait(timeout=3)
            return original(*args)
        finally:
            with mutex:
                active[0] -= 1
    monkeypatch.setattr(delivery.notifier, "_enqueue", blocked_enqueue)
    callers = []
    try:
        first = asyncio.create_task(delivery.notifier.notify("monitor", CONFIG, status="error", error="first fault"))
        callers.append(first)
        assert await asyncio.to_thread(started.wait, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(delivery.notifier.notify("monitor", CONFIG, status="error", error="second fault"))
        callers.append(second)
        await asyncio.sleep(0.01)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        assert active[0] == 1 and peak[0] == 1
    finally:
        jobs = list(delivery.notifier._queue_jobs)
        release.set()
        await asyncio.gather(*jobs)
    pending = json.loads(next(delivery.root.rglob("pending.json")).read_text())["pending"]
    assert len(pending) == 2 and peak[0] == 1
    assert not delivery.calls


@pytest.mark.asyncio
async def test_tool_deadline_is_cooperative_and_retains_pending_message(delivery, monkeypatch):
    monkeypatch.setattr(health, "TOOL_TIMEOUT_SECONDS", 0.01)
    class Adapter:
        cancel_checker = None
        def run_safe(self, name, **kwargs):
            while not self.cancel_checker():
                time.sleep(0.001)
            return {"success": False, "error": "notification deadline reached"}
    monkeypatch.setattr(health, "get_tool_registry", lambda context: Adapter())
    result = await delivery.notifier.notify("monitor", CONFIG, status="error", error="test")
    assert result["status"] == "failed" and result["notify_queued"] == 1
    assert result["notify_errors"][0]["error"] == "notification deadline reached"


@pytest.mark.asyncio
async def test_outbox_failure_is_visible_and_path_component_cannot_traverse(delivery, monkeypatch):
    def failed_delivery(*args, **kwargs):
        assert ".." not in kwargs["outbox_dir"].relative_to(delivery.root).parts
        raise OSError("disk full")
    monkeypatch.setattr(health, "deliver_workflow_notifications", failed_delivery)
    report = await delivery.notifier.notify("../../outside", CONFIG, status="error", error="original")
    assert report["status"] == "failed" and "disk full" in report["error"]


@pytest.fixture
def poller_deps(monkeypatch):
    notifications, cleaned, saved = [], [], []
    async def create(*args, **kwargs):
        return {"id": "test-execution", "startedAt": 1}
    async def build(**kwargs):
        return SimpleNamespace(extra={}, agent="rex")
    async def cleanup(context):
        cleaned.append(context)
    async def record(*args, **kwargs):
        saved.append((args, kwargs))
    async def notify(*args, **kwargs):
        notifications.append((args, kwargs))
        return {"status": "sent"}
    monkeypatch.setattr(poller, "create_execution_record", create)
    monkeypatch.setattr(poller, "build_workflow_tool_context", build)
    monkeypatch.setattr(poller, "cleanup_workflow_tool_context", cleanup)
    monkeypatch.setattr(poller, "record_execution_result", record)
    monkeypatch.setattr(poller, "run_workflow", lambda **kwargs: poller.RunWorkflowResult(status="FAILED", error="original failure"))
    manager = poller.WorkflowPollerManager()
    manager._health_notifier = SimpleNamespace(notify=notify, get_status=lambda _: None)
    return SimpleNamespace(manager=manager, notifications=notifications, cleaned=cleaned, saved=saved)


@pytest.mark.asyncio
async def test_record_creation_failure_notifies_and_cleans_cancel_event(poller_deps, monkeypatch):
    async def locked(*args, **kwargs):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(poller, "create_execution_record", locked)
    result = await poller_deps.manager._execute_run("monitor", {}, CONFIG)
    assert result["lastError"] == "database is locked"
    assert not poller_deps.saved and poller_deps.cleaned == [None]
    assert "monitor" not in poller_deps.manager._run_cancel_events
    assert poller_deps.notifications[0][1]["stage"] == "execution_record_create"


@pytest.mark.asyncio
async def test_persistence_failure_preserves_original_execution_error(poller_deps, monkeypatch):
    async def locked(*args, **kwargs):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(poller, "record_execution_result", locked)
    result = await poller_deps.manager._execute_run("monitor", {}, CONFIG)
    assert result["lastError"] == "original failure"
    assert result["lastPersistenceError"] == "database is locked"
    notice = poller_deps.notifications[0][1]
    assert notice["status"] == "persistence_error"
    assert "original failure" in notice["error"] and "database is locked" in notice["error"]
    assert len(poller_deps.cleaned) == 1 and not poller_deps.manager._run_cancel_events


@pytest.mark.asyncio
async def test_notification_failure_does_not_replace_execution_error_or_skip_cleanup(poller_deps):
    async def failed(*args, **kwargs):
        raise RuntimeError("notify unavailable")
    poller_deps.manager._health_notifier.notify = failed
    result = await poller_deps.manager._execute_run("monitor", {}, CONFIG)
    assert result["lastError"] == "original failure"
    assert result["healthNotification"]["error"] == "notify unavailable"
    assert len(poller_deps.cleaned) == 1 and not poller_deps.manager._run_cancel_events


@pytest.mark.asyncio
async def test_manual_stop_is_not_reported_as_fault(poller_deps, monkeypatch):
    def stopped(**kwargs):
        for event in poller_deps.manager._run_cancel_events["monitor"]:
            event.set()
        assert kwargs["cancel"]()
        raise RuntimeError("user stopped")
    monkeypatch.setattr(poller, "run_workflow", stopped)
    result = await poller_deps.manager._execute_run("monitor", {}, CONFIG)
    assert result["lastStatus"] == "cancelled"
    assert not poller_deps.notifications and len(poller_deps.cleaned) == 1
    assert not poller_deps.manager._run_cancel_events


@pytest.mark.asyncio
async def test_history_cancel_is_not_reported_as_fault(poller_deps, monkeypatch):
    monkeypatch.setattr(poller, "execution_cancelled", lambda eid: eid == "test-execution")
    monkeypatch.setattr(poller, "run_workflow", lambda **_: poller.RunWorkflowResult(status="SUCCEEDED"))
    result = await poller_deps.manager._execute_run("monitor", {}, CONFIG)
    assert result["lastStatus"] == "cancelled"
    assert not poller_deps.notifications
    assert len(poller_deps.cleaned) == 1 and not poller_deps.manager._run_cancel_events


@pytest.mark.asyncio
async def test_async_cancel_during_record_creation_still_cleans_state(poller_deps, monkeypatch):
    started = asyncio.Event()
    async def blocked(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(poller, "create_execution_record", blocked)
    task = asyncio.create_task(poller_deps.manager._execute_run("monitor", {}, CONFIG))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not poller_deps.notifications and poller_deps.cleaned == [None]
    assert not poller_deps.manager._run_cancel_events

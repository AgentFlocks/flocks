"""Durable notification delivery with synthetic bindings and no external sends."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from flocks.workflow import notification_delivery as delivery


@pytest.fixture(autouse=True)
def configured_test_bindings(monkeypatch):
    monkeypatch.setattr(delivery, "_configured_session_bindings", lambda session_id, **kwargs: [
        SimpleNamespace(session_id=session_id, channel_id="feishu",
                        account_id="test-account", chat_id="test-chat-feishu")])


def test_notifications_count_real_delivery_and_exceptions(tmp_path):
    replies = iter([{"success": True}, {"success": False, "error": "rejected"}, RuntimeError("offline")])
    calls = []
    def run_safe(name, **kwargs):
        calls.append((name, kwargs))
        result = next(replies)
        if isinstance(result, Exception):
            raise result
        return result
    result = delivery.deliver_workflow_notifications(SimpleNamespace(run_safe=run_safe),
        {"notify_channel_type": "feishu", "notify_session_id": "test-session"},
        [{"dedup_key": str(i), "message": "test"} for i in range(3)], outbox_dir=tmp_path)
    assert result["notify_sent"] == 1 and result["notify_failed"] == 2
    assert [e["error"] for e in result["notify_errors"]] == ["rejected", "offline"]
    assert "channel_type" not in calls[0][1]
    assert calls[0][1]["chat_id"] == "test-chat-feishu"
    assert result["notify_queued"] == 2


def test_outbox_retries_without_repeating_business_work_and_deduplicates_success(tmp_path):
    calls = []
    replies = iter([{"success": False, "error": "offline"}, {"success": True}])
    tool = SimpleNamespace(run_safe=lambda name, **kwargs: calls.append(kwargs) or next(replies))
    inputs = {"notify_session_id": "target", "notify_channel_type": "feishu"}
    messages = [{"dedup_key": "alert-a", "message": "action already completed"}]
    failed = delivery.deliver_workflow_notifications(tool, inputs, messages, outbox_dir=tmp_path, now=100)
    assert failed["notify_failed"] == 1 and failed["notify_queued"] == 1
    waiting = delivery.deliver_workflow_notifications(tool, inputs, [], outbox_dir=tmp_path, now=101)
    assert waiting["notify_sent"] == 0 and len(calls) == 1
    retried = delivery.deliver_workflow_notifications(tool, inputs, [], outbox_dir=tmp_path, now=160)
    assert retried["notify_sent"] == 1 and retried["notify_queued"] == 0
    repeated = delivery.deliver_workflow_notifications(tool, inputs, messages, outbox_dir=tmp_path, now=200)
    assert repeated["notify_sent"] == 0 and repeated["notify_skipped"] == 1
    assert len(calls) == 2


def test_outbox_never_redirects_pending_after_channel_configuration_change(tmp_path):
    calls = []
    failed_tool = SimpleNamespace(run_safe=lambda name, **kwargs: {"success": False, "error": "offline"})
    message = [{"dedup_key": "alert-a", "message": "test"}]
    delivery.deliver_workflow_notifications(failed_tool, {"notify_session_id": "original"}, message,
                                          outbox_dir=tmp_path, now=100)
    tool = SimpleNamespace(run_safe=lambda name, **kwargs: calls.append(kwargs) or {"success": True})
    result = delivery.deliver_workflow_notifications(tool, {"notify_session_id": "replacement"}, message,
                                                   outbox_dir=tmp_path, now=200)
    assert result["notify_withheld"] == 1 and not calls
    assert delivery.rebind_pending_notifications(tmp_path, old_session_id="original",
        new_session_id="replacement", new_channel_type="feishu") == 1
    result = delivery.deliver_workflow_notifications(tool, {"notify_session_id": "replacement", "notify_channel_type": "feishu"}, [],
                                                   outbox_dir=tmp_path, now=200)
    assert result["notify_sent"] == 1 and result["notify_queued"] == 0
    assert calls[0]["session_id"] == "replacement"


def test_outbox_requires_target_and_bounds_attempts_per_round(tmp_path):
    calls = []
    tool = SimpleNamespace(run_safe=lambda name, **kwargs: calls.append(kwargs) or {"success": True})
    messages = [{"dedup_key": str(i), "message": "test"} for i in range(3)]
    withheld = delivery.deliver_workflow_notifications(tool, {}, messages, outbox_dir=tmp_path, now=100)
    assert withheld["notify_withheld"] == 3 and not calls
    delivery.rebind_pending_notifications(tmp_path, old_session_id="", new_session_id="target", new_channel_type="feishu")
    result = delivery.deliver_workflow_notifications(tool, {"notify_session_id": "target", "notify_channel_type": "feishu"}, [],
                                                   outbox_dir=tmp_path, max_attempts=1, now=200)
    assert result["notify_sent"] == 1 and result["notify_queued"] == 2 and len(calls) == 1


def test_shared_outbox_only_drains_and_counts_current_workflow(tmp_path):
    calls = []
    tool = SimpleNamespace(run_safe=lambda name, **kwargs: calls.append(kwargs) or {"success": True})
    for workflow in ("dispose", "backlog"):
        queued = delivery.deliver_workflow_notifications(tool,
            {"_workflow_id": workflow, "notify_session_id": "target"},
            [{"dedup_key": "same-key", "message": workflow}], outbox_dir=tmp_path, max_attempts=0)
        assert queued["notify_queued"] == 1
    backlog = delivery.deliver_workflow_notifications(tool,
        {"_workflow_id": "backlog", "notify_session_id": "target"}, [], outbox_dir=tmp_path)
    assert backlog["notify_sent"] == 1 and backlog["notify_queued"] == 0
    assert [call["message"] for call in calls] == ["backlog"]
    disabled = delivery.deliver_workflow_notifications(tool,
        {"_workflow_id": "dispose", "notify_session_id": "target", "notify_channel_enabled": False},
        [], outbox_dir=tmp_path)
    assert disabled["notify_queued"] == 1 and len(calls) == 1
    other_target = delivery.deliver_workflow_notifications(tool,
        {"_workflow_id": "backlog", "notify_session_id": "different"}, [], outbox_dir=tmp_path)
    assert other_target["notify_withheld"] == 0


def test_outbox_persists_before_any_channel_side_effect(tmp_path, monkeypatch):
    calls = []
    tool = SimpleNamespace(run_safe=lambda *args, **kwargs: calls.append(kwargs))
    def failed_write(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(delivery, "_atomic_bytes", failed_write)
    with pytest.raises(OSError, match="disk full"):
        delivery.deliver_workflow_notifications(tool, {"notify_session_id": "target"},
            [{"message": "test"}], outbox_dir=tmp_path)
    assert not calls


@pytest.mark.parametrize("bindings", [[],
    [{"session_id": "somebody-else", "channel_id": "feishu", "account_id": "a", "chat_id": "other"}],
    [{"session_id": "target", "channel_id": "feishu", "account_id": "a", "chat_id": "one"},
     {"session_id": "target", "channel_id": "feishu", "account_id": "a", "chat_id": "two"}],
])
def test_unbound_other_session_or_ambiguous_target_is_retained_without_sending(tmp_path, monkeypatch, bindings):
    calls = []
    monkeypatch.setattr(delivery, "_configured_session_bindings", lambda *args, **kwargs: bindings)
    tool = SimpleNamespace(run_safe=lambda *args, **kwargs: calls.append(kwargs) or {"success": True})
    result = delivery.deliver_workflow_notifications(tool, {"notify_session_id": "target"},
        [{"dedup_key": "alert", "message": "test"}], outbox_dir=tmp_path)
    assert not calls
    assert result["notify_failed"] == 1 and result["notify_queued"] == 1
    assert "unique binding" in result["notify_errors"][0]["error"]
    pending = json.loads((tmp_path / "pending.json").read_text())["pending"]
    assert next(iter(pending.values()))["last_error"]


def test_unique_explicit_binding_pins_account_chat_and_disables_fallback(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(delivery, "_configured_session_bindings", lambda *args, **kwargs: [
        {"session_id": "target", "channel_id": "feishu", "account_id": "account", "chat_id": "exact-chat"}])
    tool = SimpleNamespace(run_safe=lambda *args, **kwargs: calls.append(kwargs) or {"success": True})
    result = delivery.deliver_workflow_notifications(tool, {"notify_session_id": "target"},
        [{"message": "test"}], outbox_dir=tmp_path)
    assert result["notify_sent"] == 1 and result["notify_queued"] == 0
    assert calls == [{"message": "test", "session_id": "target", "account_id": "account", "chat_id": "exact-chat"}]


def test_auto_channel_works_for_any_unique_binding_and_refuses_ambiguity(tmp_path, monkeypatch):
    calls = []
    bindings = [{"session_id": "target", "channel_id": "slack", "account_id": "a", "chat_id": "one"}]
    monkeypatch.setattr(delivery, "_configured_session_bindings", lambda *args, **kwargs: bindings)
    tool = SimpleNamespace(run_safe=lambda *args, **kwargs: calls.append(kwargs) or {"success": True})
    inputs = {"notify_session_id": "target"}
    assert delivery.deliver_workflow_notifications(tool, inputs, [{"message": "first"}], outbox_dir=tmp_path)["notify_sent"] == 1
    bindings.append({"session_id": "target", "channel_id": "email", "account_id": "a", "chat_id": "two"})
    result = delivery.deliver_workflow_notifications(tool, inputs, [{"message": "second"}], outbox_dir=tmp_path)
    assert result["notify_failed"] == 1 and result["notify_queued"] == 1
    assert len(calls) == 1


def test_corrupt_outbox_is_preserved_without_external_effects(tmp_path):
    path = tmp_path / "pending.json"
    path.write_bytes(b'{"version":1,"pending":broken')
    calls = []
    tool = SimpleNamespace(run_safe=lambda *args, **kwargs: calls.append(kwargs))
    with pytest.raises(ValueError):
        delivery.deliver_workflow_notifications(tool, {"notify_session_id": "target"},
            [{"message": "test"}], outbox_dir=tmp_path)
    assert path.read_bytes() == b'{"version":1,"pending":broken'
    assert not calls


def test_concurrent_drains_reserve_a_pending_message_only_once(tmp_path):
    started, release = threading.Event(), threading.Event()
    calls = []
    def send(*args, **kwargs):
        calls.append(kwargs)
        started.set()
        assert release.wait(timeout=5)
        return {"success": True}
    inputs = {"notify_session_id": "target"}
    messages = [{"dedup_key": "once", "message": "test"}]
    tool = SimpleNamespace(run_safe=send)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(delivery.deliver_workflow_notifications, tool, inputs, messages, outbox_dir=tmp_path)
        try:
            assert started.wait(timeout=3)
            second = delivery.deliver_workflow_notifications(tool, inputs, messages, outbox_dir=tmp_path)
            assert second["notify_sent"] == 0 and second["notify_queued"] == 1
            assert len(calls) == 1
        finally:
            release.set()
        assert first.result(timeout=3)["notify_sent"] == 1
    assert not json.loads((tmp_path / "pending.json").read_text())["pending"]


def test_abandoned_delivery_lease_is_retried_after_expiry(tmp_path):
    inputs = {"notify_session_id": "target"}
    delivery.deliver_workflow_notifications(None, inputs, [{"message": "test"}], outbox_dir=tmp_path, max_attempts=0, now=100)
    path = tmp_path / "pending.json"
    state = json.loads(path.read_text())
    next(iter(state["pending"].values())).update(lease_id="interrupted-process", lease_until=200)
    path.write_text(json.dumps(state))
    calls = []
    tool = SimpleNamespace(run_safe=lambda *args, **kwargs: calls.append(kwargs) or {"success": True})
    assert delivery.deliver_workflow_notifications(tool, inputs, [], outbox_dir=tmp_path, now=199)["notify_sent"] == 0
    assert not calls
    assert delivery.deliver_workflow_notifications(tool, inputs, [], outbox_dir=tmp_path, now=201)["notify_sent"] == 1
    assert len(calls) == 1


@pytest.mark.parametrize("disabled", [False, " false ", "OFF", None, ""])
def test_disabled_delivery_does_not_send_or_enqueue_new_messages(tmp_path, disabled):
    calls = []
    tool = SimpleNamespace(run_safe=lambda *args, **kwargs: calls.append(kwargs))
    result = delivery.deliver_workflow_notifications(tool,
        {"notify_session_id": "target", "notify_channel_enabled": disabled}, [{"message": "test"}], outbox_dir=tmp_path)
    assert result["notify_skipped"] == 1 and result["notify_queued"] == 0
    assert not calls


@pytest.mark.parametrize("success", [False, "false", "true", None, 1])
def test_only_boolean_success_can_remove_pending_notification(tmp_path, success):
    tool = SimpleNamespace(run_safe=lambda *args, **kwargs: {"success": success})
    result = delivery.deliver_workflow_notifications(tool, {"notify_session_id": "target"},
        [{"message": "test"}], outbox_dir=tmp_path)
    assert result["notify_failed"] == 1 and result["notify_queued"] == 1

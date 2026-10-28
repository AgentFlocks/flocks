"""Small synthetic GT fixtures: no customer configuration or external files."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from flocks.workflow import repairs


@pytest.fixture(autouse=True)
def configured_test_bindings(monkeypatch):
    def bindings(session_id, *, deadline):
        return [SimpleNamespace(session_id=session_id, channel_id=channel,
                                account_id="test-account", chat_id=f"test-chat-{channel}")
                for channel in ("feishu", "custom")]
    monkeypatch.setattr(repairs, "_configured_session_bindings", bindings)


LEGACY_LOCK = '''import sys
IS_WINDOWS = sys.platform == 'win32'
def _acquire_lock(lock_path):
    fh = open(lock_path, 'w+')
    if IS_WINDOWS:
        fh.write('L'); fh.flush(); fh.seek(0)
        while True:
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1); break
            except OSError:
                continue
    else:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    return fh
def _release_lock(fh):
    try:
        if IS_WINDOWS:
            try:
                fh.seek(0); msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()
'''


def _workflow(name: str, node_id: str, code: str) -> dict:
    return {"name": name, "start": node_id, "nodes": [{"id": node_id, "type": "python", "code": code}],
            "edges": [], "metadata": {"custom": {"keep": True}},
            "triggers": [{"id": "custom-trigger", "inputs": {"user_value": "unchanged"}}]}


def _accept(monkeypatch, name, node_id, code):
    monkeypatch.setitem(repairs._LEGACY_HASHES, (name, node_id), hashlib.sha256(code.encode()).hexdigest())


def test_lock_repair_previews_without_mutating_and_is_idempotent():
    workflow = _workflow("stream_alert_dispose_gt", "mark_processed", LEGACY_LOCK)
    before = copy.deepcopy(workflow)
    plan = repairs.preview_workflow_repairs(workflow)
    assert workflow == before
    assert plan.changed
    code = plan.workflow["nodes"][0]["code"]
    ast.parse(code)
    assert "from filelock import FileLock" in code
    assert "open(lock_path, 'w+')" not in code
    assert "fh.write" not in code
    assert "while True" not in code
    assert not repairs.preview_workflow_repairs(plan.workflow).changed
    assert plan.workflow["triggers"] == before["triggers"]


def test_unknown_workflow_or_custom_backlog_is_never_replaced():
    workflow = _workflow("backlog_monitor_gt", "check_backlog", "outputs['custom'] = 1")
    plan = repairs.preview_workflow_repairs(workflow)
    assert not plan.changed and plan.warnings
    assert plan.workflow == workflow
    workflow["name"] = "customer-monitor"
    assert not repairs.preview_workflow_repairs(workflow).changed


def test_custom_lock_with_additional_logic_is_not_overwritten():
    code = LEGACY_LOCK.replace("    return fh", "    custom_audit(lock_path)\n    return fh")
    workflow = _workflow("stream_alert_dispose_gt", "mark_processed", code)
    assert not repairs.preview_workflow_repairs(workflow).changed


def test_repaired_lock_bounds_contention_and_can_be_reacquired(tmp_path, monkeypatch):
    import filelock
    factory = filelock.FileLock
    held = factory(tmp_path / "progress.lock")
    def short_timeout(path, timeout):
        return factory(path, timeout=0.01)
    monkeypatch.setattr(filelock, "FileLock", short_timeout)
    code = repairs.preview_workflow_repairs(
        _workflow("stream_alert_dispose_gt", "mark_processed", LEGACY_LOCK)
    ).workflow["nodes"][0]["code"]
    namespace = {}
    exec(code, namespace)
    with held:
        with pytest.raises(filelock.Timeout):
            namespace["_acquire_lock"](str(tmp_path / "progress.lock"))
    acquired = namespace["_acquire_lock"](str(tmp_path / "progress.lock"))
    namespace["_release_lock"](acquired)
    assert not acquired.is_locked


def test_atomic_apply_backup_and_stale_preview_rejection(tmp_path):
    path = tmp_path / "workflow.json"
    raw = json.dumps(_workflow("stream_alert_dispose_gt", "mark_processed", LEGACY_LOCK)).encode()
    path.write_bytes(raw)
    preview = repairs.repair_workflow_file(path)
    assert not preview["applied"] and not (tmp_path / "backup").exists()
    assert path.read_bytes() == raw
    report = repairs.repair_workflow_file(path, apply=True, expected_sha256=preview["sha256"],
                                          backup_dir=tmp_path / "backup")
    assert report["applied"]
    assert Path(report["backup_path"]).read_bytes() == raw
    assert repairs.repair_workflow_file(path, apply=True, backup_dir=tmp_path / "backup")["changed"] is False
    with pytest.raises(ValueError, match="changed since preview"):
        repairs.repair_workflow_file(path, apply=True, expected_sha256=preview["sha256"],
                                     backup_dir=tmp_path / "backup")


def test_failed_replace_leaves_original_and_backup(tmp_path, monkeypatch):
    path = tmp_path / "workflow.json"
    raw = json.dumps(_workflow("stream_alert_dispose_gt", "mark_processed", LEGACY_LOCK)).encode()
    path.write_bytes(raw)
    def fail_replace(*args):
        raise PermissionError("file in use")
    monkeypatch.setattr(repairs.os, "replace", fail_replace)
    with pytest.raises(PermissionError):
        repairs.repair_workflow_file(path, apply=True, backup_dir=tmp_path / "backup")
    assert path.read_bytes() == raw
    assert next((tmp_path / "backup").glob("*/workflow.json")).read_bytes() == raw
    assert not list(tmp_path.glob("*.tmp"))


def test_triage_exact_indent_and_checkpoint_guard(monkeypatch):
    code = '''def _triage_single_alert(parsed):
        src_ip_val = parsed.get('src_ip', '')
    dst_ip_val = parsed.get('dst_ip', '')
    return src_ip_val, dst_ip_val
if persist and enriched_with_triage:
    pass
outputs['enriched_alerts_with_triage'] = enriched_with_triage
'''
    monkeypatch.setattr(repairs, "_TRIAGE_HASHES", {hashlib.sha256(code.encode()).hexdigest()})
    workflow = _workflow("stream_alert_triage_gt", "attack_analysis_single_structured", code)
    plan = repairs.preview_workflow_repairs(workflow)
    fixed = plan.workflow["nodes"][0]["code"]
    ast.parse(fixed)
    assert "    src_ip_val" in fixed
    assert "if stats.get('triage_failed', 0)" in fixed
    # Execute only our tiny synthetic fixture to prove failures cannot produce
    # outputs that would allow the downstream checkpoint to advance.
    for stats, paths, alerts in [({"triage_failed": 1}, ["durable"], [{}]),
                                ({"triage_failed": 0}, [], [{}]),
                                ({"triage_failed": 0}, ["durable"], [{"triage_status": "failed"}])]:
        namespace = {"stats": stats, "persist": True, "enriched_with_triage": alerts,
                     "output_paths": paths, "outputs": {}}
        with pytest.raises(RuntimeError, match="offsets were not committed"):
            exec(fixed, namespace)
        assert namespace["outputs"] == {}
    assert not repairs.preview_workflow_repairs(plan.workflow).changed


def test_triage_partial_persistence_failure_cannot_checkpoint(monkeypatch):
    code = '''def _triage_single_alert(parsed):
        src_ip_val = parsed.get('src_ip', '')
    return src_ip_val
if persist and enriched_with_triage:
    try:
        output_paths.append('first-shard-written')
        raise OSError('second shard failed')
    except Exception as e:
        print(f'[triage] WARNING: failed to persist triage_result jsonl: {e}')
outputs['enriched_alerts_with_triage'] = enriched_with_triage
'''
    monkeypatch.setattr(repairs, "_TRIAGE_HASHES", {hashlib.sha256(code.encode()).hexdigest()})
    fixed = repairs.preview_workflow_repairs(_workflow(
        "stream_alert_triage_gt", "attack_analysis_single_structured", code)).workflow["nodes"][0]["code"]
    namespace = {"stats": {"triage_failed": 0}, "persist": True, "enriched_with_triage": [{}],
                 "output_paths": [], "outputs": {}}
    with pytest.raises(RuntimeError, match="output was not persisted"):
        exec(fixed, namespace)
    assert namespace["output_paths"] == ["first-shard-written"]
    assert namespace["outputs"] == {}


@pytest.mark.parametrize("stats, alerts", [({"triage_failed": 1}, [{}]),
                                         ({"triage_failed": 0}, [{"triage_status": "failed"}])])
def test_known_analysis_failure_stops_before_persistence_side_effect(monkeypatch, stats, alerts):
    code = '''def _triage_single_alert(parsed):
        src_ip_val = parsed.get('src_ip', '')
    return src_ip_val
if persist and enriched_with_triage:
    persistence_calls.append('write-jsonl')
    output_paths.append('written')
outputs['enriched_alerts_with_triage'] = enriched_with_triage
'''
    monkeypatch.setattr(repairs, "_TRIAGE_HASHES", {hashlib.sha256(code.encode()).hexdigest()})
    fixed = repairs.preview_workflow_repairs(_workflow(
        "stream_alert_triage_gt", "attack_analysis_single_structured", code)).workflow["nodes"][0]["code"]
    namespace = {"stats": stats, "persist": True, "enriched_with_triage": alerts,
                 "output_paths": [], "outputs": {}, "persistence_calls": []}
    with pytest.raises(RuntimeError, match="triage incomplete"):
        exec(fixed, namespace)
    assert namespace["persistence_calls"] == []
    assert namespace["output_paths"] == []
    assert namespace["outputs"] == {}


def test_dispose_join_awaits_sources_and_selects_authoritative_fields(monkeypatch):
    name, node_id, code = "stream_alert_dispose_gt", "join_evidence", "outputs.update(inputs)\n"
    _accept(monkeypatch, name, node_id, code)
    workflow = _workflow(name, node_id, code)
    workflow["edges"] = [{"from": source, "to": node_id} for source in ("query_threat_intel", "verify_deterministic")]
    plan = repairs.preview_workflow_repairs(workflow)
    node = plan.workflow["nodes"][0]
    assert node["join"] is True and node["join_mode"] == "namespace"
    merged = repairs.merge_dispose_evidence_inputs({
        "ti_results": ["stale"], "evidence_results": ["stale"], "custom_config": "keep",
        "__gt_evidence_sources__": {
            "query_threat_intel": {"ti_results": ["fresh-intel"], "evidence_results": []},
            "verify_deterministic": {"ti_results": [], "evidence_results": ["fresh-evidence"]},
        },
    })
    assert merged == {"ti_results": ["fresh-intel"], "evidence_results": ["fresh-evidence"], "custom_config": "keep"}
    with pytest.raises(ValueError, match="Missing evidence branch"):
        repairs.merge_dispose_evidence_inputs({})


@pytest.mark.parametrize("dataflow_mode", ["legacy", "vertex_cache"])
def test_dispose_join_runs_once_after_both_branches(monkeypatch, dataflow_mode):
    from flocks.workflow.engine import WorkflowEngine
    from flocks.workflow.models import Workflow
    from flocks.workflow.repl_runtime import PythonExecRuntime

    code = "outputs['combined'] = inputs['ti_results'][0] + '+' + inputs['evidence_results'][0]\n"
    _accept(monkeypatch, "stream_alert_dispose_gt", "join_evidence", code)
    workflow = _workflow("stream_alert_dispose_gt", "join_evidence", code)
    workflow["start"] = "seed"
    workflow["triggers"] = []
    workflow["nodes"] += [
        {"id": "seed", "type": "python", "code": "outputs.update(ti_results=[], evidence_results=[])"},
        {"id": "query_threat_intel", "type": "python", "code": "outputs.update(inputs)\noutputs['ti_results'] = ['intel']"},
        {"id": "verify_deterministic", "type": "python", "code": "outputs.update(inputs)\noutputs['evidence_results'] = ['evidence']"},
    ]
    workflow["edges"] = [
        {"from": "seed", "to": "query_threat_intel"}, {"from": "seed", "to": "verify_deterministic"},
        {"from": "query_threat_intel", "to": "join_evidence"},
        {"from": "verify_deterministic", "to": "join_evidence"},
    ]
    plan = repairs.preview_workflow_repairs(workflow)
    result = WorkflowEngine(Workflow.from_dict(plan.workflow), runtime=PythonExecRuntime(tool_registry=SimpleNamespace()),
                            dataflow_mode=dataflow_mode).run(retain_history=True)
    assert result.outputs == {"combined": "intel+evidence"}
    assert sum(step.node_id == "join_evidence" for step in result.history) == 1


def test_notifications_count_real_delivery_and_exceptions(tmp_path):
    replies = iter([{"success": True}, {"success": False, "error": "rejected"}, RuntimeError("offline")])
    calls = []
    def run_safe(name, **kwargs):
        calls.append((name, kwargs))
        result = next(replies)
        if isinstance(result, Exception):
            raise result
        return result
    result = repairs.deliver_dispose_notifications(SimpleNamespace(run_safe=run_safe),
        {"notify_channel_type": "custom", "notify_session_id": "test-session"},
        [{"dedup_key": str(i), "message": "test"} for i in range(3)], outbox_dir=tmp_path)
    assert result["notify_sent"] == 1 and result["notify_failed"] == 2
    assert [e["error"] for e in result["notify_errors"]] == ["rejected", "offline"]
    assert "channel_type" not in calls[0][1]
    assert calls[0][1]["chat_id"] == "test-chat-custom"
    assert result["notify_queued"] == 2


def test_outbox_retries_without_repeating_disposal_and_deduplicates_success(tmp_path):
    calls = []
    replies = iter([{"success": False, "error": "offline"}, {"success": True}])
    tool = SimpleNamespace(run_safe=lambda name, **kwargs: calls.append(kwargs) or next(replies))
    inputs = {"notify_session_id": "target", "notify_channel_type": "feishu"}
    messages = [{"dedup_key": "alert-a", "message": "action already completed"}]
    failed = repairs.deliver_dispose_notifications(tool, inputs, messages, outbox_dir=tmp_path, now=100)
    assert failed["notify_failed"] == 1 and failed["notify_queued"] == 1
    waiting = repairs.deliver_dispose_notifications(tool, inputs, [], outbox_dir=tmp_path, now=101)
    assert waiting["notify_sent"] == 0 and len(calls) == 1
    retried = repairs.deliver_dispose_notifications(tool, inputs, [], outbox_dir=tmp_path, now=160)
    assert retried["notify_sent"] == 1 and retried["notify_queued"] == 0
    repeated = repairs.deliver_dispose_notifications(tool, inputs, messages, outbox_dir=tmp_path, now=200)
    assert repeated["notify_sent"] == 0 and repeated["notify_skipped"] == 1
    assert len(calls) == 2


def test_outbox_never_redirects_pending_after_channel_configuration_change(tmp_path):
    calls = []
    failed_tool = SimpleNamespace(run_safe=lambda name, **kwargs: {"success": False, "error": "offline"})
    message = [{"dedup_key": "alert-a", "message": "test"}]
    repairs.deliver_dispose_notifications(failed_tool, {"notify_session_id": "original"}, message,
                                          outbox_dir=tmp_path, now=100)
    tool = SimpleNamespace(run_safe=lambda name, **kwargs: calls.append(kwargs) or {"success": True})
    result = repairs.deliver_dispose_notifications(tool, {"notify_session_id": "replacement"}, message,
                                                   outbox_dir=tmp_path, now=200)
    assert result["notify_withheld"] == 1 and not calls
    assert repairs.rebind_pending_notifications(tmp_path, old_session_id="original",
        new_session_id="replacement", new_channel_type="feishu") == 1
    result = repairs.deliver_dispose_notifications(tool, {"notify_session_id": "replacement"}, [],
                                                   outbox_dir=tmp_path, now=200)
    assert result["notify_sent"] == 1 and result["notify_queued"] == 0
    assert calls[0]["session_id"] == "replacement"


def test_outbox_requires_target_and_bounds_attempts_per_round(tmp_path):
    calls = []
    tool = SimpleNamespace(run_safe=lambda name, **kwargs: calls.append(kwargs) or {"success": True})
    messages = [{"dedup_key": str(i), "message": "test"} for i in range(3)]
    withheld = repairs.deliver_dispose_notifications(tool, {}, messages, outbox_dir=tmp_path, now=100)
    assert withheld["notify_withheld"] == 3 and not calls
    repairs.rebind_pending_notifications(tmp_path, old_session_id="", new_session_id="target", new_channel_type="feishu")
    result = repairs.deliver_dispose_notifications(tool, {"notify_session_id": "target"}, [],
                                                   outbox_dir=tmp_path, max_attempts=1, now=200)
    assert result["notify_sent"] == 1 and result["notify_queued"] == 2 and len(calls) == 1


def test_shared_outbox_only_drains_and_counts_current_workflow(tmp_path):
    calls = []
    tool = SimpleNamespace(run_safe=lambda name, **kwargs: calls.append(kwargs) or {"success": True})
    for workflow in ("dispose", "backlog"):
        queued = repairs.deliver_workflow_notifications(tool,
            {"_workflow_id": workflow, "notify_session_id": "target"},
            [{"dedup_key": "same-key", "message": workflow}], outbox_dir=tmp_path, max_attempts=0)
        assert queued["notify_queued"] == 1
    backlog = repairs.deliver_workflow_notifications(tool,
        {"_workflow_id": "backlog", "notify_session_id": "target"}, [], outbox_dir=tmp_path)
    assert backlog["notify_sent"] == 1 and backlog["notify_queued"] == 0
    assert [call["message"] for call in calls] == ["backlog"]
    disabled = repairs.deliver_workflow_notifications(tool,
        {"_workflow_id": "dispose", "notify_session_id": "target", "notify_channel_enabled": False},
        [], outbox_dir=tmp_path)
    assert disabled["notify_queued"] == 1 and len(calls) == 1
    other_target = repairs.deliver_workflow_notifications(tool,
        {"_workflow_id": "backlog", "notify_session_id": "different"}, [], outbox_dir=tmp_path)
    assert other_target["notify_withheld"] == 0


def test_outbox_persists_before_any_channel_side_effect(tmp_path, monkeypatch):
    calls = []
    tool = SimpleNamespace(run_safe=lambda *args, **kwargs: calls.append(kwargs))
    def failed_write(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(repairs, "_atomic_bytes", failed_write)
    with pytest.raises(OSError, match="disk full"):
        repairs.deliver_dispose_notifications(tool, {"notify_session_id": "target"},
            [{"message": "test"}], outbox_dir=tmp_path)
    assert not calls


@pytest.mark.parametrize("bindings", [[],
    [{"session_id": "somebody-else", "channel_id": "feishu", "account_id": "a", "chat_id": "other"}],
    [{"session_id": "target", "channel_id": "feishu", "account_id": "a", "chat_id": "one"},
     {"session_id": "target", "channel_id": "feishu", "account_id": "a", "chat_id": "two"}],
])
def test_unbound_other_session_or_ambiguous_target_is_retained_without_sending(tmp_path, monkeypatch, bindings):
    calls = []
    monkeypatch.setattr(repairs, "_configured_session_bindings", lambda *args, **kwargs: bindings)
    tool = SimpleNamespace(run_safe=lambda *args, **kwargs: calls.append(kwargs) or {"success": True})
    result = repairs.deliver_workflow_notifications(tool, {"notify_session_id": "target"},
        [{"dedup_key": "alert", "message": "test"}], outbox_dir=tmp_path)
    assert not calls
    assert result["notify_failed"] == 1 and result["notify_queued"] == 1
    assert "unique binding" in result["notify_errors"][0]["error"]
    pending = json.loads((tmp_path / "pending.json").read_text())["pending"]
    assert next(iter(pending.values()))["last_error"]


def test_unique_explicit_binding_pins_account_chat_and_disables_fallback(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(repairs, "_configured_session_bindings", lambda *args, **kwargs: [
        {"session_id": "target", "channel_id": "feishu", "account_id": "account", "chat_id": "exact-chat"}])
    tool = SimpleNamespace(run_safe=lambda *args, **kwargs: calls.append(kwargs) or {"success": True})
    result = repairs.deliver_workflow_notifications(tool, {"notify_session_id": "target"},
        [{"message": "test"}], outbox_dir=tmp_path)
    assert result["notify_sent"] == 1 and result["notify_queued"] == 0
    assert calls == [{"message": "test", "session_id": "target", "account_id": "account", "chat_id": "exact-chat"}]


def _jsonl(root, workflow, day, content):
    path = root / workflow / day / ("dedup_result.jsonl" if "dedup" in workflow else "triage_result_001.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_bounded_scan_stops_on_today_without_reading_history(tmp_path):
    for workflow in ("stream_alert_dedup_gt", "stream_alert_triage_gt"):
        _jsonl(tmp_path, workflow, "2026-01-02", b'{"time":"2026-01-02 12:00:00"}\n')
        _jsonl(tmp_path, workflow, "2026-01-01", b"x" * 3_000_000)
    result = repairs.check_backlog_progress({}, root=tmp_path, today="2026-01-02", max_bytes=1024)
    assert result["monitor_health"] == "ok" and result["has_backlog"] is False
    assert result["scan_bytes"] < 1024 and result["scan_files"] == 2


def test_missing_corrupt_and_budget_exhaustion_remain_unknown_and_notify(tmp_path):
    _jsonl(tmp_path, "stream_alert_dedup_gt", "2026-01-02", b"invalid\n")
    result = repairs.check_backlog_progress({"notify_session_id": "test"}, root=tmp_path,
                                           today="2026-01-02", max_bytes=4)
    assert result["monitor_health"] == "unknown"
    assert result["dedup_health"] == "unknown" and result["triage_health"] == "unknown"
    assert result["scan_bytes"] <= 4
    calls = []
    tool = SimpleNamespace(run_safe=lambda name, **kwargs: calls.append(kwargs) or {"success": True})
    sent = repairs.notify_backlog_progress(tool, result, outbox_dir=tmp_path / "outbox")
    assert sent["feishu_notify_count"] == 1 and "巡检异常" in calls[0]["message"]


def test_backlog_notification_retries_after_backlog_recovers(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(repairs.time, "time", lambda: clock[0])
    replies = iter([{"success": False, "error": "offline"}, {"success": True}])
    calls = []
    tool = SimpleNamespace(run_safe=lambda *args, **kwargs: calls.append(kwargs) or next(replies))
    inputs = {"notify_session_id": "target", "has_backlog": True, "today": "2026-01-02", "backlog_details": "old progress"}
    failed = repairs.notify_backlog_progress(tool, inputs, outbox_dir=tmp_path)
    assert failed["feishu_notify_status"] == "failed" and failed["feishu_notify_queued"] == 1
    clock[0] += 61
    recovered = repairs.notify_backlog_progress(tool, {**inputs, "has_backlog": False}, outbox_dir=tmp_path)
    assert recovered["feishu_notify_status"] == "sent" and recovered["feishu_notify_queued"] == 0
    assert calls[0]["message"] == calls[1]["message"]


def test_unreadable_progress_is_unknown(tmp_path, monkeypatch):
    denied = _jsonl(tmp_path, "stream_alert_dedup_gt", "2026-01-02", b'{"time":"2026-01-02"}\n')
    _jsonl(tmp_path, "stream_alert_triage_gt", "2026-01-02", b'{"time":"2026-01-02"}\n')
    original_open = Path.open
    def guarded_open(self, *args, **kwargs):
        if self == denied:
            raise PermissionError("access denied")
        return original_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    result = repairs.check_backlog_progress({}, root=tmp_path, today="2026-01-02")
    assert result["dedup_health"] == "unknown" and "PermissionError" in result["dedup_scan_errors"]
    assert result["triage_health"] == "healthy"


def test_oversized_jsonl_record_is_unknown_with_bounded_reads(tmp_path):
    _jsonl(tmp_path, "stream_alert_dedup_gt", "2026-01-02",
           json.dumps({"time": "2026-01-02", "body": "x" * 500_000}).encode() + b"\n")
    result = repairs.check_backlog_progress({}, root=tmp_path, today="2026-01-02", max_bytes=4096)
    assert result["dedup_health"] == "unknown"
    assert result["scan_bytes"] <= 4096


def test_current_alert_in_older_directory_is_positive_progress(tmp_path):
    for workflow in ("stream_alert_dedup_gt", "stream_alert_triage_gt"):
        _jsonl(tmp_path, workflow, "2026-01-02", b'{"time":"2026-01-01"}\n')
        _jsonl(tmp_path, workflow, "2026-01-01", b'{"time":"2026-01-03"}\n')
    result = repairs.check_backlog_progress({}, root=tmp_path, today="2026-01-03")
    assert result["monitor_health"] == "ok" and not result["has_backlog"]
    assert result["dedup_latest_date"] == "2026-01-02"
    assert result["dedup_latest_alert_date"] == "2026-01-03"


def test_backlog_repair_preserves_all_trigger_inputs(monkeypatch):
    code = "outputs['old'] = True\n"
    _accept(monkeypatch, "backlog_monitor_gt", "check_backlog", code)
    workflow = _workflow("backlog_monitor_gt", "check_backlog", code)
    plan = repairs.preview_workflow_repairs(workflow)
    assert plan.workflow["triggers"] == workflow["triggers"]
    assert "check_backlog_progress(inputs)" in plan.workflow["nodes"][0]["code"]


def test_v1_upgrade_accepts_whitelist_but_still_rejects_failed_analysis(monkeypatch):
    code = '''source = inputs['source']
status = 'cached' if source == 'cache' else (
    'ok' if source in ('triaged', 'no_dedup_key_triaged') else 'failed'
)
if status == 'failed':
    raise RuntimeError('GT triage incomplete; input offsets were not committed')
outputs['triage_status'] = status
'''
    monkeypatch.setattr(repairs, "_TRIAGE_V1_ANALYSIS_HASHES", {hashlib.sha256(code.encode()).hexdigest()})
    original = _workflow("stream_alert_triage_gt", "attack_analysis_single_structured", code)
    plan = repairs.preview_workflow_repairs(original)
    assert [c.repair for c in plan.changes] == ["triage-whitelist-status"]
    fixed = plan.workflow['nodes'][0]['code']
    for source in ("whitelist", "triaged", "no_dedup_key_triaged", "cache"):
        namespace = {"inputs": {"source": source}, "outputs": {}}
        exec(fixed, namespace)
        assert namespace["outputs"]["triage_status"] in {"ok", "cached"}
    with pytest.raises(RuntimeError, match="triage incomplete"):
        exec(fixed, {"inputs": {"source": "failed"}, "outputs": {}})
    assert not repairs.preview_workflow_repairs(plan.workflow).changed
    custom = _workflow("stream_alert_triage_gt", "attack_analysis_single_structured", code + "\n# custom")
    assert not repairs.preview_workflow_repairs(custom).changed


def test_fingerprinted_loader_upgrade_is_idempotent_and_preserves_inputs(monkeypatch):
    code = "outputs['legacy'] = True"
    monkeypatch.setattr(repairs, "_TRIAGE_REPLAY_LOAD_HASHES", {hashlib.sha256(code.encode()).hexdigest()})
    original = _workflow("stream_alert_triage_gt", "load_dedup_incremental", code)
    plan = repairs.preview_workflow_repairs(original)
    assert plan.workflow["triggers"] == original["triggers"]
    assert "load_gt_dedup_incremental(inputs)" in plan.workflow["nodes"][0]["code"]
    assert not repairs.preview_workflow_repairs(plan.workflow).changed


def _dedup_fixture(root, day, records, *, suffix=""):
    path = root / "stream_alert_dedup_gt" / day / f"dedup_result{suffix}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path.resolve()


def _save_test_offsets(root, offsets):
    path = root / "stream_alert_triage_gt" / "processed_offset.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"offsets": offsets}), encoding="utf-8")
    return path


def test_checkpoint_upgrade_is_fingerprinted_and_idempotent(monkeypatch):
    code = "outputs.update(inputs)"
    monkeypatch.setattr(repairs, "_TRIAGE_MARK_HASHES", {hashlib.sha256(code.encode()).hexdigest()})
    original = _workflow("stream_alert_triage_gt", "mark_processed_dedup_keys", code)
    plan = repairs.preview_workflow_repairs(original)
    assert plan.changed
    assert "commit_gt_dedup_offsets(inputs)" in plan.workflow["nodes"][0]["code"]
    assert not repairs.preview_workflow_repairs(plan.workflow).changed
    original["nodes"][0]["code"] += "\n# custom"
    assert not repairs.preview_workflow_repairs(original).changed


@pytest.mark.parametrize("invalid", ["{broken", '{}', '{"offsets": {"old": -1}}', '{"offsets": {"old": true}}'])
def test_checkpoint_corruption_is_never_replaced(tmp_path, invalid):
    path = tmp_path / "processed_offset.json"
    path.write_text(invalid, encoding="utf-8")
    with pytest.raises((ValueError, KeyError)):
        repairs.commit_gt_dedup_offsets({"file_offsets": {"new": 30},
            "processed_offset_path": str(path), "processed_lock_path": str(tmp_path / "offset.lock")})
    assert path.read_text(encoding="utf-8") == invalid


@pytest.mark.parametrize("dataflow_mode", ["legacy", "vertex_cache"])
def test_incremental_pipeline_commits_only_after_persistence_and_retries(tmp_path, monkeypatch, dataflow_mode):
    from flocks.workflow import NodeExecutionError, Workflow, WorkflowEngine
    from flocks.workflow.repl_runtime import PythonExecRuntime

    path = _dedup_fixture(tmp_path, "2026-10-27", [{"id": "old"}, {"id": "next"}])
    state = _save_test_offsets(tmp_path, {"unrelated-file": 42})
    initial = {"input_date": "2026-10-28", "_trigger": "poller", "incremental_max_records": 1}
    nodes = [
        {"id": "load", "type": "python", "code": "outputs.update(load(inputs))"},
        {"id": "pass_context", "type": "python", "code": "pass"},
        {"id": "persist", "type": "python", "code": "persist(inputs)"},
        {"id": "mark", "type": "python", "code": "outputs.update(commit(inputs))\noutputs['batch_ids'] = ','.join(a['id'] for a in inputs['enriched_alerts'])"},
    ]
    workflow = Workflow.from_dict({"name": "synthetic_checkpoint_pipeline", "start": "load", "nodes": nodes,
        "edges": [{"from": a, "to": b} for a, b in [("load", "pass_context"), ("pass_context", "persist"), ("persist", "mark")]]})
    persisted = []
    fail = {"persist": True}
    def persist(inputs):
        if fail["persist"]:
            raise OSError("result disk full")
        persisted.extend(a["id"] for a in inputs["enriched_alerts"])
    def run():
        runtime = PythonExecRuntime(tool_registry=SimpleNamespace(), globals={
            "load": lambda values: repairs.load_gt_dedup_incremental(values, root=tmp_path),
            "persist": persist, "commit": repairs.commit_gt_dedup_offsets})
        return WorkflowEngine(workflow, runtime=runtime, dataflow_mode=dataflow_mode).run(initial_inputs=initial)

    original = state.read_bytes()
    with pytest.raises(NodeExecutionError, match="disk full"):
        run()
    assert state.read_bytes() == original
    assert persisted == []
    fail["persist"] = False
    real_atomic = repairs._atomic_bytes
    def fail_replace(*args, **kwargs):
        raise PermissionError("checkpoint is read-only")
    monkeypatch.setattr(repairs, "_atomic_bytes", fail_replace)
    with pytest.raises(NodeExecutionError, match="read-only"):
        run()
    assert state.read_bytes() == original
    monkeypatch.setattr(repairs, "_atomic_bytes", real_atomic)
    first = run()
    assert first.outputs["processed_mark_status"] == "ok"
    assert json.loads(state.read_bytes())["offsets"]["unrelated-file"] == 42
    second = run()
    assert second.outputs["batch_ids"] == "next"
    assert json.loads(state.read_bytes())["offsets"][str(path)] == path.stat().st_size
    # A storage failure after writing results replays that batch (at least once).
    assert persisted == ["old", "old", "next"]
    assert run().outputs["batch_ids"] == ""


def test_scheduled_loader_backfills_uncommitted_dates_without_writing_progress(tmp_path):
    old = _dedup_fixture(tmp_path, "2026-10-26", [{"id": "done"}, {"id": "old"}])
    recent = _dedup_fixture(tmp_path, "2026-10-27", [{"id": "yesterday"}])
    start = len((json.dumps({"id": "done"}) + "\n").encode())
    state = _save_test_offsets(tmp_path, {str(old): start})
    before = state.read_bytes()
    result = repairs.load_gt_dedup_incremental({"input_date": "2026-10-28", "_trigger": "poller"}, root=tmp_path)
    assert [a["id"] for a in result["enriched_alerts"]] == ["old", "yesterday"]
    assert result["file_offsets"] == {str(old): old.stat().st_size, str(recent): recent.stat().st_size}
    assert result["load_stats"]["backfill_days"] == 7
    assert state.read_bytes() == before


def test_manual_date_and_explicit_path_keep_targeted_replay_semantics(tmp_path):
    old = _dedup_fixture(tmp_path, "2026-10-26", [{"id": "older"}])
    _dedup_fixture(tmp_path, "2026-10-27", [{"id": "target"}])
    manual = repairs.load_gt_dedup_incremental({"input_date": "2026-10-27"}, root=tmp_path)
    assert [a["id"] for a in manual["enriched_alerts"]] == ["target"]
    explicit = repairs.load_gt_dedup_incremental({"input_paths": [str(old)], "input_date": "2026-10-28"}, root=tmp_path)
    assert [a["id"] for a in explicit["enriched_alerts"]] == ["older"]
    with pytest.raises(FileNotFoundError):
        repairs.load_gt_dedup_incremental({"input_path": str(tmp_path / "missing")}, root=tmp_path)


def test_record_budget_resumes_at_complete_line_and_never_skips_partial_tail(tmp_path):
    path = _dedup_fixture(tmp_path, "2026-10-28", [{"id": "one"}, {"id": "two"}])
    with path.open("ab") as stream:
        stream.write(b'{"id": "partial"')
    inputs = {"input_date": "2026-10-28", "incremental_max_records": 1}
    first = repairs.load_gt_dedup_incremental(inputs, root=tmp_path)
    assert [a["id"] for a in first["enriched_alerts"]] == ["one"]
    assert first["load_stats"]["scan_limit_reached"]
    _save_test_offsets(tmp_path, first["file_offsets"])
    second = repairs.load_gt_dedup_incremental(inputs, root=tmp_path)
    assert [a["id"] for a in second["enriched_alerts"]] == ["two"]
    _save_test_offsets(tmp_path, second["file_offsets"])
    third = repairs.load_gt_dedup_incremental(inputs, root=tmp_path)
    assert third["enriched_alerts"] == []
    assert third["file_offsets"] == second["file_offsets"]
    with path.open("ab") as stream:
        stream.write(b'}\n')
    last = repairs.load_gt_dedup_incremental(inputs, root=tmp_path)
    assert [a["id"] for a in last["enriched_alerts"]] == ["partial"]


def test_processed_shards_cannot_starve_unread_shards_at_file_budget(tmp_path):
    done = _dedup_fixture(tmp_path, "2026-10-27", [{"id": "done"}])
    needed = _dedup_fixture(tmp_path, "2026-10-28", [{"id": "needed"}])
    _save_test_offsets(tmp_path, {str(done): done.stat().st_size})
    result = repairs.load_gt_dedup_incremental({"_trigger": "poller", "input_date": "2026-10-28", "incremental_max_files": 1}, root=tmp_path)
    assert result["loaded_files"] == [str(needed)]
    assert [a["id"] for a in result["enriched_alerts"]] == ["needed"]


def test_byte_budget_and_corrupt_or_truncated_data_cannot_advance_checkpoint(tmp_path):
    path = _dedup_fixture(tmp_path, "2026-10-28", [{"id": "too-large-for-budget"}])
    with pytest.raises(ValueError, match="exceeds incremental_max_bytes"):
        repairs.load_gt_dedup_incremental({"input_date": "2026-10-28", "incremental_max_bytes": 4}, root=tmp_path)
    assert not (tmp_path / "stream_alert_triage_gt" / "processed_offset.json").exists()
    _save_test_offsets(tmp_path, {str(path): path.stat().st_size + 1})
    with pytest.raises(ValueError, match="below saved offset"):
        repairs.load_gt_dedup_incremental({"input_path": str(path)}, root=tmp_path)
    _save_test_offsets(tmp_path, {})
    path.write_bytes(b'{bad json}\n')
    with pytest.raises(ValueError, match="Invalid dedup JSONL"):
        repairs.load_gt_dedup_incremental({"input_path": str(path)}, root=tmp_path)


def test_replay_preserves_selection_policy_and_reports_missing_source(tmp_path):
    _dedup_fixture(tmp_path, "2026-10-28", [
        {"id": "follower", "is_duplicate": "true"},
        {"id": "leader", "is_duplicate": True, "_triage_leader": "true"},
        {"id": "leader"}, {"id": "new", "is_duplicate": False},
    ])
    result = repairs.load_gt_dedup_incremental({"input_date": "2026-10-28"}, root=tmp_path)
    assert [a["id"] for a in result["enriched_alerts"]] == ["leader", "new"]
    assert result["load_stats"]["skipped_policy"] == 1
    assert result["load_stats"]["skipped_duplicate_raw_log_id"] == 1
    missing = repairs.load_gt_dedup_incremental({"input_date": "2026-10-29"}, root=tmp_path)
    assert missing["load_stats"]["source_health"] == "no_files"


def test_default_replay_batch_is_small_enough_for_downstream_work(tmp_path):
    _dedup_fixture(tmp_path, "2026-10-28", [{"id": str(i)} for i in range(11)])
    result = repairs.load_gt_dedup_incremental({"input_date": "2026-10-28"}, root=tmp_path)
    assert len(result["enriched_alerts"]) == 10
    assert result["load_stats"]["scan_limit_reached"]

"""Cumulative stage budgets and real process supervision, without model calls."""

import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from flocks.security import batch
from flocks.security.batch_timeouts import (
    DEFAULT_PHASE_TIMEOUTS, PHASES, ExecutionControlError, PhaseTimeout, enter_phase, read_control,
    timeout_details, validate_budgets,
)


def budgets(**overrides):
    return {**dict.fromkeys(PHASES, 30), **overrides}


def make_phase_batch(tmp_path, monkeypatch, **overrides):
    monkeypatch.setattr(batch, "registry_root", lambda: tmp_path / "registry")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
    source = tmp_path / "input" / "0"
    source.mkdir(parents=True)
    (source / "description.txt").write_text("Review source")
    (source / "repo-vul.tar.gz").write_bytes(b"archive")
    return batch.prepare_batch(
        source.parent, run_dir=tmp_path / "run", concurrency=1, model="test/model",
        poc=True, max_snapshot_bytes=1024, phase_timeouts=budgets(**overrides),
    )


def test_stage_time_is_independent_and_reentry_is_cumulative():
    current = {"attempt": "attempt"}
    limits = budgets(baseline=10, verification=20)
    enter_phase(current, limits, "baseline", now=100)
    enter_phase(current, limits, "baseline", now=104)
    assert current["phase_timeout"]["deadline"] == 110
    enter_phase(current, limits, "verification", now=108)
    assert current["phase_timeout"]["deadline"] == 128
    enter_phase(current, limits, "baseline", now=120)
    assert current["phase_timeout"]["deadline"] == 122
    with pytest.raises(PhaseTimeout) as caught:
        enter_phase(current, limits, "poc_generation", now=123)
    assert caught.value.details["timeout_phase"] == "baseline"
    assert caught.value.details["phase_elapsed_seconds"] == 11


def test_cleanup_handoff_does_not_renew_budget():
    current = {}
    enter_phase(current, budgets(baseline=1, cleanup=2), "baseline", now=100)
    enter_phase(current, budgets(baseline=1, cleanup=2), "cleanup", after_exit=True, now=103)
    enter_phase(current, budgets(baseline=1, cleanup=2), "cleanup", after_exit=True, now=104)
    assert current["phase_timeout"]["deadline"] == 105
    with pytest.raises(PhaseTimeout):
        enter_phase(current, budgets(), "cleanup", after_exit=True, now=106)


def test_internal_dynamic_phases_share_one_budget():
    current = {}
    enter_phase(current, budgets(dynamic_validation=10), "probing", now=100)
    enter_phase(current, budgets(dynamic_validation=10), "cybergym_solving", now=104)
    assert current["phase_timeout"]["deadline"] == 110


@pytest.mark.parametrize("invalid", [{}, {"unknown": 1}, budgets(baseline=0), budgets(baseline=True), budgets(baseline=1.5)])
def test_reject_invalid_configuration(invalid):
    with pytest.raises(ValueError):
        validate_budgets(invalid, poc=True, dynamic=True)


def test_disabled_optional_phases_need_no_budget():
    values = budgets()
    del values["poc_generation"], values["dynamic_validation"]
    assert validate_budgets(values, poc=False, dynamic=False) == values


def test_corrupt_read_keeps_last_deadline_and_attempts_are_isolated(tmp_path):
    current = {"attempt": "one"}
    enter_phase(current, budgets(), "baseline")
    (tmp_path / "current.json").write_text("incomplete")
    assert read_control(tmp_path, "one", current) == current
    with pytest.raises(ExecutionControlError):
        read_control(tmp_path, "two", current)
    with pytest.raises(ExecutionControlError):
        read_control(tmp_path, "one")


def test_backward_clock_is_control_failure():
    current = {}
    enter_phase(current, budgets(), "baseline", now=100)
    with pytest.raises(ExecutionControlError):
        timeout_details(current, now=99)


def test_new_config_has_no_overall_timeout(tmp_path, monkeypatch):
    root = make_phase_batch(tmp_path, monkeypatch)
    config = batch.read_json(root / "batch.json")
    assert config["version"] == 2
    assert "task_timeout" not in config


PHASE_WORKER = r'''
import json, os, sys, time
from pathlib import Path
from flocks.security.batch import atomic_json, read_json, file_lock
from flocks.security.batch_timeouts import enter_phase
from flocks.utils.process_identity import process_identity
root, key, attempt, mode = sys.argv[1:]
root = Path(root)
directory = root / "tasks" / key
limits = read_json(root / "batch.json")["phase_timeouts"]
with file_lock(directory / "task.lock"):
    current = read_json(directory / "current.json")
    current.update(pid=os.getpid(), process_identity=process_identity(os.getpid()))
    atomic_json(directory / "current.json", current)
    def phase(name):
        enter_phase(current, limits, name)
        atomic_json(directory / "current.json", current)
    def wait(seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if (directory / "cancel.json").exists():
                atomic_json(directory / "result.json", {"status": "cancelled", "attempt": attempt})
                sys.exit(0)
            time.sleep(.01)
    if mode == "adopt":
        print("ready", flush=True)
        wait(30)
    elif mode == "blocked":
        phase("baseline")
        time.sleep(120)
    else:
        phase("baseline")
        wait(1.2 if mode == "stages" else .7)
        phase("poc_generation" if mode == "stages" else "baseline")
        wait(1.2 if mode == "stages" else .7)
    atomic_json(directory / "result.json", {"status": "completed", "attempt": attempt})
'''


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stages", "repeat", "blocked"])
async def test_supervisor_enforces_stage_deadlines(tmp_path, monkeypatch, mode):
    root = make_phase_batch(tmp_path, monkeypatch, baseline=2 if mode == "stages" else 1, poc_generation=2)
    monkeypatch.setattr(batch, "_worker_command", lambda root, key, attempt: [sys.executable, "-c", PHASE_WORKER, str(root), key, attempt, mode])
    result = await batch.run_batch(root, progress=lambda _: None)
    task = result["tasks"][0]
    if mode == "stages":
        assert task["status"] == "completed"
    else:
        assert task["status"] == "timed_out"
        assert task["failure_code"] == "phase_timeout"
        assert task["timeout_phase"] == "baseline"
    assert task["cleanup_status"] == "completed"
    assert not batch.task_running(root / "tasks" / "0")


@pytest.mark.asyncio
async def test_adoption_keeps_expired_deadline(tmp_path, monkeypatch):
    root = make_phase_batch(tmp_path, monkeypatch, baseline=1)
    directory = root / "tasks" / "0"
    current = {"attempt": "adopt", "started_at": time.time()}
    enter_phase(current, budgets(baseline=1), "baseline", now=time.monotonic() - 2)
    batch.atomic_json(directory / "current.json", current)
    child = subprocess.Popen([sys.executable, "-c", PHASE_WORKER, str(root), "0", "adopt", "adopt"], stdout=subprocess.PIPE, text=True, start_new_session=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        result = await batch.run_batch(root, progress=lambda _: None)
        assert result["tasks"][0]["timeout_phase"] == "baseline"
        assert result["tasks"][0]["status"] == "timed_out"
        assert batch.read_json(directory / "current.json")["attempt"] == "adopt"
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


@pytest.mark.asyncio
async def test_cleanup_timeout_preserves_success_and_stops_child(tmp_path, monkeypatch):
    root = make_phase_batch(tmp_path, monkeypatch, cleanup=1)
    directory = root / "tasks" / "0"
    current = {"attempt": "attempt", "started_at": time.time()}
    enter_phase(current, budgets(cleanup=1), "finalization")
    batch.atomic_json(directory / "current.json", current)
    result = {"attempt": "attempt", "status": "completed"}
    create = asyncio.create_subprocess_exec

    async def slow_cleanup(*args, **kwargs):
        code = "import time; from flocks.security import batch, batch_cleanup; batch._cleanup_child_work=lambda *a: time.sleep(60); batch_cleanup.main()"
        return await create(sys.executable, "-c", code, str(directory), "attempt", **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", slow_cleanup)
    started = time.monotonic()
    await batch.cleanup_child_work(directory, result)
    assert time.monotonic() - started < 5
    assert result["status"] == "completed"
    assert result["cleanup_status"] == "failed"
    assert not batch.task_running(directory)
    # A second cleanup cannot replenish the expired budget.
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **kw: pytest.fail("cleanup restarted"))
    await batch.cleanup_child_work(directory, result)


def test_cli_reads_explicit_phase_file_and_rejects_mixed_modes(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from flocks.cli.commands import security_batch
    from flocks.cli.commands.security import security_app

    make_phase_batch(tmp_path, monkeypatch)
    settings = tmp_path / "phases.json"
    settings.write_text(json.dumps(budgets()))
    monkeypatch.setattr(security_batch, "_run", lambda *_: None)
    args = ["batch", "run", str(tmp_path / "input"), "--run-dir", str(tmp_path / "cli"), "--phase-timeouts", str(settings)]
    result = CliRunner().invoke(security_app, args)
    assert result.exit_code == 0, result.output
    config = batch.read_json(tmp_path / "cli/batch.json")
    assert "task_timeout" not in config
    assert config["phase_timeouts"] == budgets()
    mixed = CliRunner().invoke(security_app, args + ["--task-timeout", "10"])
    assert mixed.exit_code != 0
    assert "not both" in mixed.output


@pytest.mark.parametrize("overall", [False, True])
def test_cli_without_json_uses_phase_defaults_unless_overall_is_explicit(tmp_path, monkeypatch, overall):
    from typer.testing import CliRunner
    from flocks.cli.commands import security_batch
    from flocks.cli.commands.security import security_app

    make_phase_batch(tmp_path, monkeypatch)
    monkeypatch.setattr(security_batch, "_run", lambda *_: None)
    args = ["batch", "run", str(tmp_path / "input"), "--run-dir", str(tmp_path / "cli")]
    if overall:
        args += ["--task-timeout", "7200"]
    result = CliRunner().invoke(security_app, args)
    assert result.exit_code == 0, result.output
    config = batch.read_json(tmp_path / "cli/batch.json")
    if overall:
        assert config["task_timeout"] == 7200
        assert "phase_timeouts" not in config
        assert config["version"] == 1
    else:
        assert config["phase_timeouts"] == DEFAULT_PHASE_TIMEOUTS
        assert "task_timeout" not in config
        assert config["version"] == 2


@pytest.mark.parametrize("contents", ["null", "{}", "[]"])
def test_invalid_explicit_json_does_not_fall_back_to_defaults(tmp_path, monkeypatch, contents):
    from typer.testing import CliRunner
    from flocks.cli.commands import security_batch
    from flocks.cli.commands.security import security_app

    make_phase_batch(tmp_path, monkeypatch)
    settings = tmp_path / "invalid.json"
    settings.write_text(contents)
    monkeypatch.setattr(security_batch, "_run", lambda *_: pytest.fail("invalid configuration ran"))
    result = CliRunner().invoke(security_app, [
        "batch", "run", str(tmp_path / "input"), "--run-dir", str(tmp_path / "cli"),
        "--phase-timeouts", str(settings),
    ])
    assert result.exit_code != 0
    assert not (tmp_path / "cli/batch.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "late_transition", "service_projection", "control_write"])
async def test_real_worker_preserves_phase_failure(tmp_path, monkeypatch, failure):
    from types import SimpleNamespace
    from flocks.cli.commands import security
    from flocks.security import batch_worker

    root = make_phase_batch(tmp_path, monkeypatch, baseline=1)
    directory = root / "tasks" / "0"
    current = {"attempt": "attempt", "started_at": time.time()}
    enter_phase(current, budgets(), "source_extraction")
    batch.atomic_json(directory / "current.json", current)
    scan_state = {"status": "cancelled", "integrity_status": "pending", "counts": {}, "failure_code": None}
    store = SimpleNamespace(
        get_scan=lambda _: {}, list_worker_batches=lambda _: [],
        scan_status=lambda _: dict(scan_state),
    )
    monkeypatch.setitem(sys.modules, "flocks_code_security.runtime", SimpleNamespace(get_runtime=lambda: SimpleNamespace(store=store)))
    monkeypatch.setattr(batch_worker, "extract_source", lambda *a, **kw: [])
    started_model = []

    async def audit(source, *, progress, phase_started, **kwargs):
        progress("scan.prepared", {"scan_id": "scan"})
        phase_started("baseline")
        started_model.append(True)
        try:
            if failure in {"late_transition", "service_projection"}:
                time.sleep(1.05)  # An expired stage cannot escape by changing phases.
                try:
                    phase_started("verification")
                except PhaseTimeout:
                    if failure == "service_projection":
                        # The service persists failure before its cleanup hook runs.
                        scan_state.update(status="failed", failure_code="phase_timeout")
                    raise
            await asyncio.sleep(60)
        finally:
            try:
                phase_started("cleanup")  # Cannot revoke a timeout by entering cleanup.
            except Exception:
                if failure != "service_projection":
                    raise  # The service logs cleanup exceptions and keeps the original error.

    monkeypatch.setattr(security, "_load_plugin_cli", lambda: (audit, None))
    if failure == "control_write":
        atomic = batch_worker.atomic_json

        def broken_write(path, value):
            if path.name == "current.json" and value.get("phase_timeout", {}).get("phase") == "baseline":
                raise OSError("disk error")
            atomic(path, value)

        monkeypatch.setattr(batch_worker, "atomic_json", broken_write)
    try:
        result = await batch_worker.execute(root, "0", "attempt")
        if failure in {"timeout", "late_transition", "service_projection"}:
            assert result["status"] == "timed_out"
            assert result["timeout_phase"] == "baseline"
            assert result["phase_budget_seconds"] == 1
            assert batch.read_json(directory / "current.json")["phase_timeout"]["phase"] == "baseline"
        else:
            assert result["status"] == "failed"
            assert result["failure_code"] == ExecutionControlError.code
            assert started_model == []
        assert batch.read_json(directory / "result.json")["failure_code"] == result["failure_code"]
    finally:
        batch_worker.cleanup_work(batch.read_json(directory / "current.json")["work_dir"], directory)


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_cleanup_termination_preserves_business_outcome(status):
    result = {"status": status, "audit_status": status, "failure_code": "audit_execution_failed" if status == "failed" else None}
    batch.apply_termination(result, {"status": "timed_out", "failure_code": "phase_timeout", "timeout_phase": "cleanup"})
    assert result["status"] == status
    assert result["failure_code"] != "phase_timeout"
    assert result["cleanup_status"] == "failed"


@pytest.mark.parametrize("code", ["phase_timeout", "model_quota_exhausted"])
def test_cleanup_timeout_does_not_replace_existing_failure(code):
    result = {"status": "failed", "audit_status": "failed", "failure_code": code}
    batch.apply_termination(result, {
        "status": "timed_out", "failure_code": "phase_timeout", "timeout_phase": "cleanup",
    })
    assert result["status"] == "failed"
    assert result["failure_code"] == code
    assert "timeout_phase" not in result
    assert result["cleanup_status"] == "failed"


@pytest.mark.asyncio
async def test_explicit_clean_can_retry_exhausted_cleanup_without_rerunning_audit(tmp_path, monkeypatch):
    root = make_phase_batch(tmp_path, monkeypatch, cleanup=1)
    directory = root / "tasks" / "0"
    current = {"attempt": "attempt", "started_at": time.time()}
    enter_phase(current, budgets(cleanup=1), "cleanup", now=time.monotonic() - 2)
    batch.atomic_json(directory / "current.json", current)
    batch.atomic_json(directory / "result.json", {"attempt": "attempt", "status": "timed_out", "cleanup_status": "failed"})
    result = await batch.clean_batch(root)
    assert result["tasks"][0]["status"] == "timed_out"
    assert result["tasks"][0]["cleanup_status"] == "completed"
    assert batch.read_json(directory / "current.json")["attempt"] == "attempt"


@pytest.mark.asyncio
async def test_worker_saves_success_before_cleanup_and_preserves_it_on_timeout(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from flocks.cli.commands import security
    from flocks.security import batch_worker

    root = make_phase_batch(tmp_path, monkeypatch, cleanup=1)
    directory = root / "tasks" / "0"
    current = {"attempt": "attempt", "started_at": time.time()}
    enter_phase(current, budgets(), "source_extraction")
    batch.atomic_json(directory / "current.json", current)
    store = SimpleNamespace(
        get_scan=lambda _: {}, list_worker_batches=lambda _: [],
        scan_status=lambda _: {"status": "completed", "integrity_status": "valid", "counts": {}, "failure_code": None},
    )
    monkeypatch.setitem(sys.modules, "flocks_code_security.runtime", SimpleNamespace(get_runtime=lambda: SimpleNamespace(store=store)))
    monkeypatch.setattr(batch_worker, "extract_source", lambda *a, **kw: [])

    async def audit(source, *, progress, phase_started, **kwargs):
        progress("scan.prepared", {"scan_id": "scan"})
        phase_started("cleanup")
        assert batch.read_json(directory / "result.json")["status"] == "completed"
        await asyncio.sleep(60)

    monkeypatch.setattr(security, "_load_plugin_cli", lambda: (audit, None))
    try:
        result = await batch_worker.execute(root, "0", "attempt")
        assert result["status"] == "completed"
        assert result["cleanup_status"] == "failed"
        assert result["runtime"]["termination_reason"] == "phase_timeout"
    finally:
        batch_worker.cleanup_work(batch.read_json(directory / "current.json")["work_dir"], directory)


@pytest.mark.asyncio
async def test_cancel_request_retains_phase_timeout_reason(tmp_path):
    from flocks.security.batch_worker import wait_for_cancel

    current = {"attempt": "attempt"}
    enter_phase(current, budgets(baseline=1), "baseline", now=time.monotonic() - 2)
    batch.atomic_json(tmp_path / "current.json", current)
    termination = timeout_details(current)
    batch.request_cancel(tmp_path, termination)
    batch.request_cancel(tmp_path)  # Repeated cancellation must not erase the root cause.
    assert await wait_for_cancel(tmp_path, "attempt", time.time(), 30) == termination


@pytest.mark.asyncio
async def test_expired_observation_is_rechecked_before_termination(tmp_path, monkeypatch):
    from copy import deepcopy

    root = make_phase_batch(tmp_path, monkeypatch, baseline=2, poc_generation=2)
    monkeypatch.setattr(batch, "_worker_command", lambda root, key, attempt: [sys.executable, "-c", PHASE_WORKER, str(root), key, attempt, "stages"])
    real_read = batch.read_control
    observations = []

    def read_with_old_first_observation(*args):
        current = real_read(*args)
        if not observations:
            current = deepcopy(current)
            current["phase_timeout"]["deadline"] = time.monotonic() - 1
        observations.append(current)
        return current

    monkeypatch.setattr(batch, "read_control", read_with_old_first_observation)
    result = await batch.run_batch(root, progress=lambda _: None)
    assert result["tasks"][0]["status"] == "completed"
    assert len(observations) >= 2


@pytest.mark.asyncio
async def test_explicit_task_retry_gets_new_attempt_and_budget(tmp_path, monkeypatch):
    root = make_phase_batch(tmp_path, monkeypatch, baseline=2, poc_generation=2)
    directory = root / "tasks" / "0"
    current = {"attempt": "old", "started_at": time.time() - 60}
    enter_phase(current, budgets(baseline=2), "baseline", now=time.monotonic() - 60)
    batch.atomic_json(directory / "current.json", current)
    batch.atomic_json(directory / "result.json", {
        "attempt": "old", "status": "timed_out", "failure_code": "phase_timeout",
        "cleanup_status": "completed", "source_cleanup_status": "completed",
    })
    monkeypatch.setattr(batch, "_worker_command", lambda root, key, attempt: [sys.executable, "-c", PHASE_WORKER, str(root), key, attempt, "stages"])
    result = await batch.run_batch(root, retry_failed=True, progress=lambda _: None)
    assert result["tasks"][0]["status"] == "completed"
    assert result["tasks"][0]["attempt"] != "old"


@pytest.mark.parametrize("audit_status", ["failed", "completed"])
def test_service_failed_projection_does_not_erase_phase_timeout(audit_status):
    from flocks.security.batch_worker import merge_scan_summary

    result = {"status": "timed_out", "failure_code": "phase_timeout", "timeout_phase": "verification"}
    merge_scan_summary(result, {
        "status": audit_status, "audit_status": audit_status,
        "failure_code": "phase_timeout" if audit_status == "failed" else None,
        "error": "verification deadline exceeded" if audit_status == "failed" else None,
    })
    assert result["status"] == ("timed_out" if audit_status == "failed" else "completed")


@pytest.mark.parametrize("phase_mode", [True, False])
def test_only_legacy_worker_starts_final_cleanup(tmp_path, monkeypatch, phase_mode):
    from flocks.cli.commands import security
    from flocks.security import batch_worker

    root = make_phase_batch(tmp_path, monkeypatch)
    directory = root / "tasks" / "0"
    if not phase_mode:
        config = batch.read_json(root / "batch.json")
        config.pop("phase_timeouts")
        config["task_timeout"] = 30
        batch.atomic_json(root / "batch.json", config)
    batch.atomic_json(directory / "current.json", {"attempt": "attempt"})
    cleanups = []

    async def execute(*args):
        result = {"attempt": "attempt", "status": "completed"}
        batch.atomic_json(directory / "result.json", result)
        return result

    async def shutdown(runner, target):
        return await runner(target)

    async def cleanup(*args):
        cleanups.append(True)

    monkeypatch.setattr(sys, "argv", ["worker", str(root), "0", "attempt"])
    monkeypatch.setattr(batch_worker, "execute", execute)
    monkeypatch.setattr(security, "_run_audit_with_cleanup", shutdown)
    monkeypatch.setattr(batch, "cleanup_child_work", cleanup)
    with pytest.raises(SystemExit) as exited:
        batch_worker.main()
    assert exited.value.code == 0
    assert len(cleanups) == (0 if phase_mode else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("expires", [False, True])
@pytest.mark.parametrize("direct_cleanup", [False, True])
async def test_resume_adopts_existing_cleanup_without_starting_another(tmp_path, monkeypatch, expires, direct_cleanup):
    root = make_phase_batch(tmp_path, monkeypatch, cleanup=2 if expires else 30)
    directory = root / "tasks" / "0"
    current = {"attempt": "attempt", "started_at": time.time()}
    enter_phase(current, batch.read_json(root / "batch.json")["phase_timeouts"], "cleanup")
    batch.atomic_json(directory / "current.json", current)
    batch.atomic_json(directory / "result.json", {"attempt": "attempt", "status": "completed"})
    code = '''
import sys, time
from flocks.security import batch, batch_cleanup
cleanup = batch._cleanup_child_work
def slow(*args):
    print("ready", flush=True)
    time.sleep(60 if sys.argv[3] == "expire" else 1)
    cleanup(*args)
batch._cleanup_child_work = slow
batch_cleanup.main()
'''
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(directory), "attempt", "expire" if expires else "finish"],
        stdout=subprocess.PIPE, text=True, start_new_session=True,
    )
    try:
        assert await asyncio.to_thread(child.stdout.readline) == "ready\n"
        monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **kw: pytest.fail("duplicate cleanup or audit"))
        async with asyncio.timeout(10):
            if direct_cleanup:
                # The caller may hold task.lock; only the cleanup owner's lock
                # may determine whether its filesystem work has stopped.
                with batch.file_lock(directory / "task.lock"):
                    result = batch.task_result(directory)
                    await batch.cleanup_child_work(directory, result)
            else:
                result = (await batch.run_batch(root, progress=lambda _: None))["tasks"][0]
        assert result["status"] == "completed"
        assert result["cleanup_status"] == ("failed" if expires else "completed")
        assert not batch.task_running(directory)
        assert batch.read_json(directory / "current.json")["phase_timeout"]["deadline"] == current["phase_timeout"]["deadline"]
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["finish", "expire", "cancel"])
async def test_resume_during_cleanup_startup_waits_for_lock_owner(tmp_path, monkeypatch, outcome):
    root = make_phase_batch(tmp_path, monkeypatch, cleanup=2 if outcome == "expire" else 30)
    directory = root / "tasks" / "0"
    current = {"attempt": "attempt", "started_at": time.time()}
    enter_phase(current, batch.read_json(root / "batch.json")["phase_timeouts"], "cleanup")
    batch.atomic_json(directory / "current.json", current)
    batch.atomic_json(directory / "result.json", {"attempt": "attempt", "status": "completed"})
    code = '''
import sys, time
from pathlib import Path
from flocks.security import batch, batch_cleanup
directory = Path(sys.argv[1])
print("booting", flush=True)
while not (directory / "startup-gate").exists(): time.sleep(.01)
cleanup = batch._cleanup_child_work
def slow(*args):
    print("locked", flush=True)
    time.sleep(.5 if sys.argv[3] == "finish" else 60)
    cleanup(*args)
batch._cleanup_child_work = slow
batch_cleanup.main()
'''
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(directory), "attempt", outcome],
        stdout=subprocess.PIPE, text=True, start_new_session=True,
    )
    create = asyncio.create_subprocess_exec
    spawned = asyncio.Event()

    async def resume_old_child(*args, **kwargs):
        # The orphan starts after the supervisor observed an unlocked task,
        # but before the new cleanup process acquires cleanup.lock.
        (directory / "startup-gate").touch()
        assert await asyncio.to_thread(child.stdout.readline) == "locked\n"
        process = await create(*args, **kwargs)
        spawned.set()
        return process

    try:
        assert await asyncio.to_thread(child.stdout.readline) == "booting\n"
        monkeypatch.setattr(asyncio, "create_subprocess_exec", resume_old_child)
        async with asyncio.timeout(10):
            running = asyncio.create_task(batch.run_batch(root, progress=lambda _: None))
            if outcome == "cancel":
                await spawned.wait()
                running.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await running
            else:
                await running
        result = batch.task_result(directory)
        assert result["status"] == "completed"
        assert result["cleanup_status"] == ("completed" if outcome == "finish" else "failed")
        assert not batch.task_running(directory)
        assert batch.read_json(directory / "current.json")["phase_timeout"]["deadline"] == current["phase_timeout"]["deadline"]
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
        child.wait()


QUOTA_WORKER = '''
import os, sys, time
from pathlib import Path
from flocks.security import batch
from flocks.security.batch_timeouts import enter_phase
from flocks.utils.process_identity import process_identity
root, key, attempt = sys.argv[1:]
directory = Path(root) / 'tasks' / key
with batch.file_lock(directory / 'task.lock'):
    current = batch.read_json(directory / 'current.json')
    current.update(pid=os.getpid(), process_identity=process_identity(os.getpid()))
    enter_phase(current, batch.read_json(Path(root) / 'batch.json')['phase_timeouts'], 'baseline')
    batch.atomic_json(directory / 'current.json', current)
    batch.atomic_json(directory / 'result.json', {
        'attempt': attempt, 'status': 'failed',
        'failure_code': 'model_quota_exhausted', 'error': 'quota exhausted',
    })
    while not (directory / 'cancel.json').exists(): time.sleep(.01)
    time.sleep(.3)
'''


@pytest.mark.asyncio
@pytest.mark.parametrize("termination", ["timeout", "cancel", "interrupt"])
async def test_supervisor_termination_preserves_recorded_quota_failure(tmp_path, monkeypatch, termination):
    root = make_phase_batch(tmp_path, monkeypatch, baseline=1 if termination == "timeout" else 30)
    directory = root / "tasks" / "0"
    monkeypatch.setattr(batch, "_worker_command", lambda root, key, attempt: [
        sys.executable, "-c", QUOTA_WORKER, str(root), key, attempt,
    ])
    running = asyncio.create_task(batch.run_batch(root, progress=lambda _: None))
    try:
        async with asyncio.timeout(10):
            while not (directory / "result.json").exists():
                await asyncio.sleep(.01)
            if termination == "cancel":
                batch.request_cancel(directory)
            elif termination == "interrupt":
                running.cancel()
            if termination == "interrupt":
                with pytest.raises(asyncio.CancelledError):
                    await running
            else:
                await running
        result = batch.task_result(directory)
        assert result["status"] == "failed"
        assert result["failure_code"] == "model_quota_exhausted"
        assert result["error"] == "quota exhausted"
        assert not batch.task_running(directory)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_mode", ["cancel", "wait_timeout", "finish"])
@pytest.mark.parametrize("audit_status", ["completed", "failed"])
async def test_cleanup_refreshes_recovered_result_before_settling(tmp_path, monkeypatch, exit_mode, audit_status):
    root = make_phase_batch(tmp_path, monkeypatch)
    directory = root / "tasks" / "0"
    current = {"attempt": "attempt", "started_at": time.time()}
    enter_phase(current, budgets(), "finalization")
    batch.atomic_json(directory / "current.json", current)
    result = {"attempt": "attempt", "status": "interrupted", "error": "worker disappeared"}
    create = asyncio.create_subprocess_exec
    wait_for = asyncio.wait_for
    code = '''
import sys, time
from flocks.security import batch, batch_cleanup
def reconcile_then_cleanup(directory, result):
    # Reconciliation can recover a committed audit before filesystem cleanup.
    saved = {
        "attempt": result["attempt"], "status": sys.argv[3], "audit_status": sys.argv[3],
        "integrity_status": "valid", "output_dir": "sealed-output", "poc_count": 2,
    }
    if sys.argv[3] == "failed":
        saved.update(failure_code="coverage_blocked", error="coverage incomplete")
    batch.atomic_json(directory / "result.json", saved)
    (directory / "reconciled").touch()
    if sys.argv[4] != "finish":
        time.sleep(60)
    saved.update(cleanup_status="completed", source_cleanup_status="completed")
    batch.atomic_json(directory / "result.json", saved)
batch._cleanup_child_work = reconcile_then_cleanup
batch_cleanup.main()
'''

    async def child(*args, **kwargs):
        return await create(sys.executable, "-c", code, str(directory), "attempt", audit_status, exit_mode, **kwargs)

    async def reconciled():
        while not (directory / "reconciled").exists():
            await asyncio.sleep(.01)

    injected = False

    async def timeout_after_reconciliation(awaitable, timeout):
        nonlocal injected
        if not injected:
            injected = True
            try:
                await reconciled()
            finally:
                awaitable.close()
            raise TimeoutError("supervisor cleanup wait expired")
        return await wait_for(awaitable, timeout)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", child)
    if exit_mode == "wait_timeout":
        monkeypatch.setattr(asyncio, "wait_for", timeout_after_reconciliation)
    cleaning = asyncio.create_task(batch.cleanup_child_work(directory, result))
    try:
        async with asyncio.timeout(5):
            if exit_mode == "cancel":
                await reconciled()
                cleaning.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await cleaning
            else:
                await cleaning
        saved = batch.task_result(directory)
        assert saved == result
        assert saved["status"] == saved["audit_status"] == audit_status
        assert saved["integrity_status"] == "valid"
        assert saved["output_dir"] == "sealed-output"
        assert saved["poc_count"] == 2
        if audit_status == "completed":
            assert "error" not in saved
        else:
            assert saved["failure_code"] == "coverage_blocked"
            assert saved["error"] == "coverage incomplete"
        assert saved["cleanup_status"] == ("completed" if exit_mode == "finish" else "failed")
        assert not batch.task_running(directory)
    finally:
        cleaning.cancel()
        await asyncio.gather(cleaning, return_exceptions=True)


@pytest.mark.asyncio
async def test_exhausted_cleanup_keeps_new_termination_when_no_child_started(tmp_path, monkeypatch):
    root = make_phase_batch(tmp_path, monkeypatch, cleanup=1)
    directory = root / "tasks" / "0"
    current = {"attempt": "attempt"}
    enter_phase(current, budgets(cleanup=1), "cleanup", now=time.monotonic() - 2)
    batch.atomic_json(directory / "current.json", current)
    result = {"attempt": "attempt", "status": "failed"}
    batch.atomic_json(directory / "result.json", result)
    batch.apply_termination(result, timeout_details(current))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **kw: pytest.fail("expired cleanup started"))
    await batch.cleanup_child_work(directory, result)
    assert result["status"] == "timed_out"
    assert result["failure_code"] == "phase_timeout"
    assert result["timeout_phase"] == "cleanup"
    assert batch.task_result(directory) == result

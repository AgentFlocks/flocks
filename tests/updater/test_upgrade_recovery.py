"""An upgrade that fails on the way must not take the service with it.

The source of an installation is replaced with the service stopped. Whatever fails after that
(npm missing, a dependency that will not download, a build that breaks, a new version that will
not start) has to end with the previous version running again, as far as that can be done.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flocks.cli import service_manager
from flocks.cli.service_manager import _node_install_dirs_outside_path as REAL_NODE_DIRS
from flocks.updater import installation_status, restart_handoff, updater
from flocks.updater.restart_handoff import _stop_services_after_failed_start as REAL_STOP_AFTER_FAILED_START
from tests.updater.conftest import REAL_TOOLCHAIN_REFUSAL
from tests.updater.test_restart_handoff import _FakeResponder, _simple_upgrade_handoff_args, _statuses


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A real tree, a real backup of it and a release ready to replace it; the rest of the machine is stubbed."""
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "flocks-root"))
    monkeypatch.setenv(restart_handoff.handoff_progress.UPGRADE_PROGRESS_TOKEN_ENV, "tok-123")  # secret-guard: allow - a made-up token for the test
    _FakeResponder.instances = []
    monkeypatch.setattr(restart_handoff.handoff_progress, "ProgressResponder", _FakeResponder)

    args = _simple_upgrade_handoff_args(tmp_path)
    cleanup = tmp_path / "cleanup"
    cleanup.mkdir()
    args[args.index("--"):args.index("--")] = ["--cleanup-dir", str(cleanup)]

    install = tmp_path / "install"
    _write(install / "pyproject.toml", 'version = "v2026.10.30"\n')
    _write(install / "flocks" / "keep.py", "old\n")
    _write(install / "webui" / "package.json", '{"version": "old"}\n')
    _write(install / "webui" / "src" / "app.ts", "old\n")
    _write(install / "webui" / "dist" / "index.html", "the old interface\n")
    _write(install / "webui" / "dist" / "assets" / "app.js", "old\n")
    _write(install / "webui" / "node_modules" / "esbuild" / "index.js", "compiler\n")
    _write(install / ".venv" / "bin" / "python", "#!/bin/sh\n")
    _write(install / ".flocks" / "runtime.json", "{}\n")

    backup = tmp_path / "backup.tar.gz"
    updater._write_backup_archive(install, backup)

    staged = tmp_path / "staged"
    _write(staged / "pyproject.toml", 'version = "v2026.11.4"\n')
    _write(staged / "flocks" / "keep.py", "new\n")
    _write(staged / "flocks" / "added.py", "new\n")
    _write(staged / "webui" / "package.json", '{"version": "new"}\n')
    _write(staged / "webui" / "src" / "app.ts", "new\n")

    results: list[dict] = []
    events: list[str] = []
    resyncs: list[dict] = []

    async def fake_sync(**kwargs):
        resyncs.append(kwargs)
        return None

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(message))
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda _pid: True)
    monkeypatch.setattr(restart_handoff, "_stop_services_before_upgrade", lambda _args: events.append("stop") or True)
    monkeypatch.setattr(restart_handoff, "_stop_services_after_failed_start", lambda _args: events.append("stop-again") or True)
    monkeypatch.setattr(restart_handoff, "_start_service_after_upgrade", lambda _args: events.append("start") or (True, "", ""))
    monkeypatch.setattr(restart_handoff.updater_module, "_sync_project_dependencies", fake_sync)
    monkeypatch.setattr(restart_handoff, "_write_upgrade_result", lambda **kwargs: results.append(kwargs))
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.3.31")

    class Machine:
        pass

    m = Machine()
    m.args, m.install, m.backup, m.cleanup, m.results, m.events, m.resyncs = args, install, backup, cleanup, results, events, resyncs
    return m


def _fail_at(*steps: str, error: str = "it went wrong"):
    def run_tasks(handoff_args):
        for step in steps:
            handoff_args.upgrade_step_callback(step)
        return error

    return run_tasks


def _assert_previous_install_is_back(install: Path) -> None:
    assert (install / "pyproject.toml").read_text(encoding="utf-8") == 'version = "v2026.10.30"\n'
    assert (install / "flocks" / "keep.py").read_text(encoding="utf-8") == "old\n"
    assert not (install / "flocks" / "added.py").exists()
    assert (install / "webui" / "src" / "app.ts").read_text(encoding="utf-8") == "old\n"
    assert (install / "webui" / "package.json").read_text(encoding="utf-8") == '{"version": "old"}\n'
    assert (install / "webui" / "dist" / "index.html").read_text(encoding="utf-8") == "the old interface\n"
    assert (install / "webui" / "dist" / "assets" / "app.js").read_text(encoding="utf-8") == "old\n"
    assert (install / "webui" / "node_modules" / "esbuild" / "index.js").exists()
    assert (install / ".flocks" / "runtime.json").exists()
    # `flocks start` rebuilds a WebUI older than its sources, with an npm that may be what failed.
    assert not service_manager._webui_needs_build(install / "webui")


def test_a_missing_npm_puts_the_install_back_and_starts_the_service(machine, monkeypatch) -> None:
    error = "npm was not found. Run scripts/install.sh to install Node.js and npm."
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", _fail_at("check_runtime", error=error))

    assert restart_handoff.run(machine.args) == 1

    _assert_previous_install_is_back(machine.install)
    assert machine.resyncs == []  # the runtime was not touched yet
    assert machine.events.count("start") == 1
    final = machine.results[-1]
    assert final["phase"] == "failed"
    assert final["failed_stage"] == "install"
    assert final["error"] == error
    assert final["restored_from_backup"] is True
    assert final["service_restarted"] is True
    assert final["progress"]["recovery"] == "restored"
    assert final["progress"]["failed_step"] == "check_runtime"
    assert _statuses(final["progress"])["check_runtime"] == "failed"
    assert machine.backup.exists()
    assert not machine.cleanup.exists()


def test_the_failure_stays_on_the_page_that_opens_next(machine, monkeypatch) -> None:
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", _fail_at("check_runtime", error="npm was not found."))

    restart_handoff.run(machine.args)

    shown = installation_status.read_installation_result("zh-CN")
    assert shown["recovery"] == "restored"
    assert shown["title"] == "升级到 v2026.4.1 没有完成，已恢复到 v2026.3.31"
    assert shown["detail"] == "npm was not found."
    # The enterprise upgrade tells its attempts apart by the installer's record: this one is not that.
    assert installation_status.installation_result_id() is None


def test_a_long_tool_output_still_fits_the_record(machine, monkeypatch) -> None:
    """The reader drops a record over its limit whole: the page would then say nothing."""
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", _fail_at("build_webui", error="npm error 失败 " * 5000))

    restart_handoff.run(machine.args)

    assert installation_status.source_upgrade_notice_path().stat().st_size < installation_status.MAX_RECORD_BYTES
    shown = installation_status.read_installation_result("zh-CN")
    assert shown["recovery"] == "restored"
    assert shown["detail"].startswith("npm error")


def test_a_program_left_as_a_mix_of_both_versions_keeps_its_notice_either_way(machine, monkeypatch) -> None:
    machine.backup.write_text("not an archive", encoding="utf-8")
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", _fail_at("check_runtime"))
    restart_handoff.run(machine.args)

    # The new pyproject.toml stayed in the tree: that is the version asked for by hand-started service.
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.4.1")

    assert installation_status.read_installation_result("zh-CN")["recovery"] == "failed"


def test_the_newer_of_an_installer_record_and_an_upgrade_notice_is_shown(machine, monkeypatch) -> None:
    def installer_record(when: str, record_id: str) -> None:
        path = installation_status.installation_result_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "version": 1, "id": record_id, "phase": "failed", "recovery": "restored", "updated_at": when,
                "messages": {"zh-CN": {"title": "企业版升级未完成", "message": "原安装已保留。"}},
            }),
            encoding="utf-8",
        )

    installer_record("2020-01-01T00:00:00.123456789Z", "older-installer-attempt")
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", _fail_at("check_runtime"))
    restart_handoff.run(machine.args)

    assert installation_status.read_installation_result("zh-CN")["id"].startswith("source-upgrade-")
    # What the enterprise upgrade tells its attempts apart by stays the installer's.
    assert installation_status.installation_result_id() == "older-installer-attempt"

    installer_record("2999-01-01T00:00:00Z", "later-installer-attempt")
    assert installation_status.read_installation_result("zh-CN")["id"] == "later-installer-attempt"


def test_the_notice_is_history_once_another_version_runs(machine, monkeypatch) -> None:
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", _fail_at("check_runtime"))
    restart_handoff.run(machine.args)

    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.4.1")

    assert installation_status.read_installation_result("zh-CN") is None


def test_a_later_upgrade_that_works_clears_the_notice(machine, monkeypatch) -> None:
    installation_status.write_source_upgrade_notice(
        target_version="2026.4.1", current_version="2026.3.31", source="restored", service_running=True, detail="earlier", backup_path="/b"
    )
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda _args: None)

    assert restart_handoff.run(machine.args) == 0

    assert not installation_status.source_upgrade_notice_path().exists()


def test_the_dialog_reads_the_failure_before_the_port_goes_to_the_restored_service(machine, monkeypatch) -> None:
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", _fail_at("check_runtime"))

    restart_handoff.run(machine.args)

    responder = _FakeResponder.instances[0]
    assert responder.events == ["start", f"wait:{restart_handoff.RECOVERY_NOTICE_WAIT_SECONDS}", "stop"]
    assert responder.seen_when_told["phase"] == "failed"
    assert responder.seen_when_told["recovery"] == "restoring"
    assert len(_FakeResponder.instances) == 1  # the service answers on the port now


def test_nobody_ever_reads_the_failure_without_the_recovery(machine, monkeypatch) -> None:
    """The dialog stops following at a failure with no recovery under way."""
    args = restart_handoff._parse_args(machine.args)
    reporter = restart_handoff._UpgradeReporter(args, "tok-123", ["stop", "start"])
    reporter.start("stop")
    seen: list[dict] = []
    reporter.progress._on_change = seen.append

    reporter.begin_recovery("it went wrong", failed_stage="stop", backup_path=None)

    assert seen
    assert all(snapshot["recovery"] == "restoring" for snapshot in seen if snapshot["phase"] == "failed")


def test_a_failed_build_brings_the_runtime_back_too(machine, monkeypatch) -> None:
    monkeypatch.setattr(
        restart_handoff,
        "_run_upgrade_tasks",
        _fail_at("check_runtime", "sync_dependencies", "build_webui", error="Frontend build failed: exit 1"),
    )

    assert restart_handoff.run(machine.args) == 1

    _assert_previous_install_is_back(machine.install)
    assert [sync["install_root"] for sync in machine.resyncs] == [machine.install]
    assert machine.results[-1]["progress"]["recovery"] == "restored"


def test_a_runtime_that_will_not_sync_back_still_gets_its_start(machine, monkeypatch) -> None:
    async def failing_sync(**_kwargs):
        return "no network"

    monkeypatch.setattr(restart_handoff.updater_module, "_sync_project_dependencies", failing_sync)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", _fail_at("sync_dependencies", "build_webui"))

    assert restart_handoff.run(machine.args) == 1

    assert any(event.startswith("dependency_resync_failed") for event in machine.events)
    assert "start" in machine.events
    assert machine.results[-1]["progress"]["recovery"] == "restored"


def test_a_new_version_that_will_not_start_goes_back_to_the_previous_one(machine, monkeypatch) -> None:
    starts = iter([(False, "", "ImportError: cannot import name 'thing'"), (True, "", "")])
    markers: list[str] = []

    def run_tasks(handoff_args):
        for step in ("sync_dependencies", "build_webui", "finalize"):
            handoff_args.upgrade_step_callback(step)
        return None

    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", run_tasks)
    monkeypatch.setattr(restart_handoff, "_start_service_after_upgrade", lambda _args: next(starts))
    monkeypatch.setattr(restart_handoff.updater_module, "_write_version_marker", markers.append)

    assert restart_handoff.run(machine.args) == 1

    _assert_previous_install_is_back(machine.install)
    assert machine.events.count("stop-again") == 1  # what the failed start left running
    assert markers == ["2026.3.31"]
    final = machine.results[-1]
    assert final["failed_stage"] == "start"
    assert "ImportError" in final["error"]
    assert final["progress"]["recovery"] == "restored"
    assert final["service_restarted"] is True


def test_stopping_what_a_failed_start_left_does_not_touch_the_daemon_captured_earlier(machine, monkeypatch) -> None:
    """Its pid is minutes old by then and may belong to another process."""
    seen: list[dict] = []
    monkeypatch.setattr(service_manager, "stop_all", lambda _console: None)
    monkeypatch.setattr(restart_handoff, "_stop_supervisor_before_restart", lambda **kwargs: seen.append(kwargs) or True)
    args = restart_handoff._parse_args(machine.args)

    assert REAL_STOP_AFTER_FAILED_START(args) is True

    assert seen == [{"backend_port": 5273, "service_ports": (5273,)}]


def test_a_previous_version_that_will_not_start_either_says_so(machine, monkeypatch) -> None:
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", _fail_at("check_runtime", error="npm was not found."))
    monkeypatch.setattr(restart_handoff, "_start_service_after_upgrade", lambda _args: (False, "", "address in use"))

    assert restart_handoff.run(machine.args) == 1

    _assert_previous_install_is_back(machine.install)
    final = machine.results[-1]
    assert final["error"] == "npm was not found."
    assert final["restored_from_backup"] is True
    assert final["service_restarted"] is False
    assert "address in use" in final["restore_error"]
    assert final["progress"]["recovery"] == "failed"
    # Served again for the dialog, now that nothing else wants the port.
    assert _FakeResponder.instances[-1].events == ["start", f"linger:{restart_handoff.PROGRESS_FAILURE_LINGER_SECONDS}"]
    shown = installation_status.read_installation_result("zh-CN")
    assert shown["recovery"] == "failed"
    assert "程序是 v2026.3.31" in shown["message"]


def test_a_full_temporary_directory_does_not_stop_the_restore(machine, monkeypatch) -> None:
    """A full disk is often why the upgrade failed. Then the restore has to work beside the install."""
    real_extract = updater._extract_archive
    extracted_under: list[Path] = []

    def extract(archive, destination):
        extracted_under.append(Path(destination).parent)
        if len(extracted_under) == 1:
            raise OSError(28, "No space left on device")
        return real_extract(archive, destination)

    monkeypatch.setattr(updater, "_extract_archive", extract)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", _fail_at("check_runtime"))

    assert restart_handoff.run(machine.args) == 1

    assert extracted_under[-1] == machine.install.parent
    _assert_previous_install_is_back(machine.install)
    assert machine.results[-1]["progress"]["recovery"] == "restored"


def test_nothing_on_the_failure_notice_offers_installing_by_hand(machine, monkeypatch) -> None:
    machine.backup.write_text("not an archive", encoding="utf-8")
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", _fail_at("check_runtime"))
    restart_handoff.run(machine.args)

    record = json.loads(installation_status.source_upgrade_notice_path().read_text(encoding="utf-8"))
    for language in ("zh-CN", "en-US"):
        text = json.dumps(record["messages"][language], ensure_ascii=False)
        for offer in ("install.sh", "离线", "安装包", "offline", "installer"):
            assert offer not in text


def test_an_unreadable_backup_leaves_the_manual_path(machine, monkeypatch) -> None:
    machine.backup.write_text("not an archive", encoding="utf-8")
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", _fail_at("check_runtime"))

    assert restart_handoff.run(machine.args) == 1

    assert "start" not in machine.events
    # The dialog may have read "restoring" already; it waits for the last word all the same.
    responder = _FakeResponder.instances[-1]
    assert responder.outcome_changes == 1
    assert responder.events[-1] == f"linger:{restart_handoff.PROGRESS_FAILURE_LINGER_SECONDS}"
    final = machine.results[-1]
    assert final["restored_from_backup"] is False
    assert final["restore_error"]
    assert final["progress"]["recovery"] == "failed"
    assert machine.cleanup.exists()
    assert machine.backup.exists()
    assert str(machine.backup) in installation_status.read_installation_result("zh-CN")["message"]


def test_a_component_already_replaced_is_not_rolled_back_under_it(machine, monkeypatch) -> None:
    machine.args[machine.args.index("--"):machine.args.index("--")] = ["--extra-wheel", "/tmp/component.whl"]
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", _fail_at("install_components", "build_webui"))

    assert restart_handoff.run(machine.args) == 1

    assert (machine.install / "flocks" / "keep.py").read_text(encoding="utf-8") == "new\n"
    assert "start" not in machine.events
    assert any(event.startswith("rollback_not_possible") for event in machine.events)
    assert "recovery" not in machine.results[-1]["progress"]


def test_files_a_channel_may_have_put_in_place_are_not_rolled_back_under_it(machine, monkeypatch) -> None:
    machine.args[machine.args.index("--"):machine.args.index("--")] = ["--success-file", '["/tmp/a", "/tmp/b"]']
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", _fail_at("finalize"))

    assert restart_handoff.run(machine.args) == 1

    assert (machine.install / "flocks" / "keep.py").read_text(encoding="utf-8") == "new\n"
    assert any(event.startswith("rollback_not_possible") for event in machine.events)


def test_files_a_channel_has_not_reached_yet_do_not_stop_the_rollback(machine, monkeypatch) -> None:
    machine.args[machine.args.index("--"):machine.args.index("--")] = ["--success-file", '["/tmp/a", "/tmp/b"]']
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", _fail_at("build_webui"))

    assert restart_handoff.run(machine.args) == 1

    _assert_previous_install_is_back(machine.install)


def test_no_copy_of_the_built_interface_means_no_replacement(machine, monkeypatch) -> None:
    """Without it a later failure could not put this version back: better not to start."""
    def no_space(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(restart_handoff.shutil, "copytree", no_space)
    monkeypatch.setattr(restart_handoff, "_apply_new_source", lambda _args: pytest.fail("the source must stay untouched"))

    assert restart_handoff.run(machine.args) == 1

    assert (machine.install / "webui" / "dist" / "index.html").exists()
    assert machine.events.count("start") == 1
    final = machine.results[-1]
    assert final["failed_stage"] == "source_replace"
    assert "No space left on device" in final["error"]
    assert final["progress"]["recovery"] == "restored"
    assert installation_status.read_installation_result("zh-CN")["recovery"] == "unchanged"


def test_a_component_not_reached_yet_does_not_stop_the_rollback(machine, monkeypatch) -> None:
    machine.args[machine.args.index("--"):machine.args.index("--")] = ["--extra-wheel", "/tmp/component.whl"]
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", _fail_at("check_runtime"))

    assert restart_handoff.run(machine.args) == 1

    _assert_previous_install_is_back(machine.install)


def test_a_stop_that_timed_out_changes_nothing_and_starts_the_service_again(machine, monkeypatch) -> None:
    monkeypatch.setattr(restart_handoff, "_stop_services_before_upgrade", lambda _args: False)
    monkeypatch.setattr(restart_handoff, "_apply_new_source", lambda _args: pytest.fail("the source must stay untouched"))

    assert restart_handoff.run(machine.args) == 1

    assert (machine.install / "flocks" / "keep.py").read_text(encoding="utf-8") == "old\n"
    assert machine.events.count("start") == 1
    final = machine.results[-1]
    assert final["failed_stage"] == "stop"
    assert final["progress"]["recovery"] == "restored"
    shown = installation_status.read_installation_result("zh-CN")
    assert shown["recovery"] == "unchanged"
    assert "没有开始" in shown["title"]


def test_a_stop_that_timed_out_and_a_service_that_will_not_start_say_the_program_is_untouched(machine, monkeypatch) -> None:
    monkeypatch.setattr(restart_handoff, "_stop_services_before_upgrade", lambda _args: False)
    monkeypatch.setattr(restart_handoff, "_start_service_after_upgrade", lambda _args: (False, "", "port in use"))

    assert restart_handoff.run(machine.args) == 1

    assert machine.results[-1]["progress"]["recovery"] == "failed"
    shown = installation_status.read_installation_result("zh-CN")
    assert shown["recovery"] == "failed"
    assert "新旧混合" not in shown["message"]


def test_a_crash_after_the_replacement_goes_back_too(machine, monkeypatch) -> None:
    def crash(*_args):
        raise KeyError("something unexpected")

    # A crash outside the steps that report their own failures.
    monkeypatch.setattr(restart_handoff, "_start_failure_message", crash)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda _args: None)
    monkeypatch.setattr(restart_handoff, "_start_service_after_upgrade", lambda _args: (False, "", ""))

    assert restart_handoff.run(machine.args) == 1

    assert (machine.install / "flocks" / "keep.py").read_text(encoding="utf-8") == "old\n"
    assert "something unexpected" in machine.results[-1]["error"]


def test_no_built_interface_before_the_upgrade_means_none_to_put_back(machine, monkeypatch) -> None:
    import shutil

    shutil.rmtree(machine.install / "webui" / "dist")
    updater._write_backup_archive(machine.install, machine.backup)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", _fail_at("check_runtime"))

    assert restart_handoff.run(machine.args) == 1

    assert not (machine.install / "webui" / "dist").exists()
    assert (machine.install / "flocks" / "keep.py").read_text(encoding="utf-8") == "old\n"


def test_handoff_failure_message_says_how_the_machine_was_left(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    assert "exit code 1" in updater._handoff_failure_message(1)

    updater._write_upgrade_result_state({"last_error": "npm was not found.", "recovery": "restored"})
    assert updater._handoff_failure_message(1) == "npm was not found. The installation is as it was before the upgrade."

    updater._write_upgrade_result_state({"last_error": "npm was not found.", "recovery": "failed"})
    assert "could not be brought back" in updater._handoff_failure_message(1)
    assert "upgrade-result.json" in updater._handoff_failure_message(1)

    updater._write_upgrade_result_state({"last_error": "a component was replaced"})
    assert updater._handoff_failure_message(1).endswith(f"See {updater._upgrade_result_path()}.")
    assert json.loads(updater._upgrade_result_path().read_text(encoding="utf-8"))["last_error"]


# ------------------------------------------------------------------ #
# Before anything changes
# ------------------------------------------------------------------ #


@pytest.fixture
def source_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Reaches the point where a downloaded source release has been extracted."""
    from types import SimpleNamespace

    from flocks.updater import deploy

    install_root = tmp_path / "install-root"
    (install_root / ".venv").mkdir(parents=True)
    staged = tmp_path / "staged-release"
    _write(staged / "webui" / "package.json", "{}\n")
    _write(staged / "pyproject.toml", 'version = "v2026.11.4"\n')
    archive = tmp_path / "flocks.tar.gz"
    archive.write_text("archive", encoding="utf-8")
    calls: list[str] = []

    async def fake_config():
        return SimpleNamespace(
            archive_format="tar.gz", sources=["github"], repo="AgentFlocks/Flocks", token=None, gitee_token=None,
            backup_retain_count=3, base_url=None, gitee_repo=None,
        )

    async def fake_download(**_kwargs):
        return archive

    def fake_backup(*_args, **_kwargs):
        calls.append("backup")
        return None

    def fake_spawn(*_args, **_kwargs):
        calls.append("spawn")
        raise AssertionError("the handoff must not start")

    monkeypatch.setattr(updater, "_frontend_toolchain_refusal", REAL_TOOLCHAIN_REFUSAL)
    monkeypatch.setattr(deploy, "detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(updater, "_get_updater_config", fake_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.10.30")
    monkeypatch.setattr(updater, "_download_with_fallback", fake_download)
    monkeypatch.setattr(updater, "_extract_archive", lambda *_args, **_kwargs: staged)
    monkeypatch.setattr(updater, "_backup_current_version", fake_backup)
    monkeypatch.setattr(updater, "_spawn_restart_handoff", fake_spawn)
    monkeypatch.setattr(updater, "_record_update_journal", lambda _message: None)
    monkeypatch.setattr(updater, "_running_as_systemd_service_main_process", lambda: False)
    monkeypatch.setattr(updater, "_find_executable", lambda _name: "/usr/bin/uv")
    return SimpleNamespace(staged=staged, calls=calls)


async def _stages(*args, **kwargs) -> list:
    return [step async for step in updater.perform_update(*args, **kwargs)]


@pytest.mark.asyncio
async def test_no_npm_refuses_the_upgrade_while_nothing_has_changed(source_release, monkeypatch) -> None:
    monkeypatch.setattr(service_manager, "resolve_npm_executable", lambda: None)

    steps = await _stages("2026.11.4", locale="zh-CN")

    assert [step.stage for step in steps] == ["fetching", "checking", "error"]
    assert "这台机器上找不到 npm" in steps[-1].message
    assert "服务和程序都没有动" in steps[-1].message
    assert "scripts/install." in steps[-1].message
    assert source_release.calls == []  # no backup, no handoff, so no stopped service


@pytest.mark.asyncio
async def test_an_old_node_refuses_the_upgrade_and_says_which(source_release, monkeypatch) -> None:
    monkeypatch.setattr(service_manager, "resolve_npm_executable", lambda: "/usr/bin/npm")
    monkeypatch.setattr(service_manager, "node_version_satisfies_requirement", lambda: False)
    monkeypatch.setattr(service_manager, "get_node_major_version", lambda: 18)

    steps = await _stages("2026.11.4")

    assert steps[-1].stage == "error"
    assert "Node.js 18 is older than the required 22" in steps[-1].message
    assert "The upgrade did not start" in steps[-1].message
    assert source_release.calls == []


@pytest.mark.asyncio
async def test_a_usable_toolchain_goes_on_to_the_backup(source_release, monkeypatch) -> None:
    monkeypatch.setattr(service_manager, "resolve_npm_executable", lambda: "/usr/bin/npm")
    monkeypatch.setattr(service_manager, "node_version_satisfies_requirement", lambda: True)

    steps = await _stages("2026.11.4")

    assert "checking" not in [step.stage for step in steps]
    assert source_release.calls == ["backup"]


@pytest.mark.asyncio
async def test_a_tree_with_its_interface_built_needs_no_npm(source_release, monkeypatch) -> None:
    """What the transition package is: a built webui/dist and no webui/package.json."""
    (source_release.staged / "webui" / "package.json").unlink()
    _write(source_release.staged / "webui" / "dist" / "index.html", "built\n")
    monkeypatch.setattr(service_manager, "resolve_npm_executable", lambda: None)

    steps = await _stages("2026.11.4")

    assert "checking" not in [step.stage for step in steps]
    assert source_release.calls == ["backup"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tag", ["v2026.11.01", "2026.11.01"])
async def test_the_staging_step_names_the_version_once(source_release, monkeypatch, tmp_path, tag) -> None:
    """The enterprise dialog passes a tag with its v: it read "Staging vv2026.11.01 for handoff"."""
    monkeypatch.setattr(service_manager, "resolve_npm_executable", lambda: "/usr/bin/npm")
    monkeypatch.setattr(service_manager, "node_version_satisfies_requirement", lambda: True)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_args, **_kwargs: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_build_restart_argv", lambda *_args: (_ for _ in ()).throw(RuntimeError("stop here")))

    steps = await _stages(tag)

    assert [step.message for step in steps if step.stage == "applying"] == ["Staging v2026.11.01 for handoff..."]


# ------------------------------------------------------------------ #
# npm ci empties node_modules before it fails
# ------------------------------------------------------------------ #


def _npm_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, ci_works: bool) -> Path:
    webui = tmp_path / "webui"
    _write(webui / "package.json", "{}\n")
    _write(webui / "package-lock.json", "{}\n")
    _write(webui / "node_modules" / "esbuild" / "index.js", "compiler\n")
    calls: list[list[str]] = []

    async def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        if cmd[1] == "install":
            return 1, "", "UNABLE_TO_GET_ISSUER_CERT_LOCALLY"
        if cmd[1] == "ci":
            # What npm ci does first, whatever comes next.
            assert not (webui / "node_modules").exists()
            _write(webui / "node_modules" / "partial" / "index.js", "partial\n")
            return (0, "", "") if ci_works else (1, "", "UNABLE_TO_GET_ISSUER_CERT_LOCALLY")
        _write(webui / "dist" / "index.html", "built\n")
        return 0, "", ""

    monkeypatch.setattr(updater, "_run_async", fake_run)
    monkeypatch.setattr(updater, "_bundled_node_install_dir", lambda: None)
    monkeypatch.setattr(updater, "_find_executable", lambda name: "/usr/bin/npm" if name == "npm" else None)
    monkeypatch.setattr(updater, "_record_update_journal", lambda _message: None)
    return webui


@pytest.mark.asyncio
async def test_a_failed_npm_ci_leaves_node_modules_as_they_were(tmp_path, monkeypatch) -> None:
    webui = _npm_workspace(tmp_path, monkeypatch, ci_works=False)

    error = await updater._build_frontend_workspace(webui)

    assert "UNABLE_TO_GET_ISSUER_CERT_LOCALLY" in error
    assert (webui / "node_modules" / "esbuild" / "index.js").exists()
    assert not (webui / "node_modules" / "partial").exists()
    assert not (webui / "node_modules.before-npm-ci").exists()


@pytest.mark.asyncio
async def test_node_modules_outlast_a_change_of_npm_candidate(tmp_path, monkeypatch) -> None:
    """Windows has two npm candidates, and moving on to the second empties the workspace."""
    webui = tmp_path / "webui"
    _write(webui / "package.json", "{}\n")
    _write(webui / "package-lock.json", "{}\n")
    _write(webui / "node_modules" / "esbuild" / "index.js", "compiler\n")
    seen: list[str] = []

    async def fake_run(cmd, **_kwargs):
        seen.append(" ".join(cmd))
        if seen != ["bundled-npm install"]:  # npm install leaves what it has alone when it cannot download
            _write(webui / "node_modules" / "partial" / f"{len(seen)}.js", "partial\n")
        return 1, "", "network down"

    monkeypatch.setattr(updater, "_run_async", fake_run)
    monkeypatch.setattr(updater, "_record_update_journal", lambda _message: None)
    monkeypatch.setattr(
        updater,
        "_resolve_frontend_npm_candidates",
        lambda *, npm_registry=None: [
            updater._FrontendNpmCandidate(npm="bundled-npm", env=None, source="bundled"),
            updater._FrontendNpmCandidate(npm="system-npm", env=None, source="system"),
        ],
    )

    error = await updater._build_frontend_workspace(webui)

    assert "network down" in error
    assert seen == ["bundled-npm install", "bundled-npm ci", "system-npm install", "system-npm ci"]
    assert (webui / "node_modules" / "esbuild" / "index.js").exists()
    assert not (webui / "node_modules" / "partial").exists()
    assert not (webui / "node_modules.before-npm-ci").exists()


@pytest.mark.asyncio
async def test_a_failed_build_after_npm_ci_leaves_node_modules_as_they_were(tmp_path, monkeypatch) -> None:
    webui = tmp_path / "webui"
    _write(webui / "package.json", "{}\n")
    _write(webui / "package-lock.json", "{}\n")
    _write(webui / "node_modules" / "esbuild" / "index.js", "compiler\n")

    async def fake_run(cmd, **_kwargs):
        if cmd[1] == "install":
            return 1, "", "broken"
        if cmd[1] == "ci":
            _write(webui / "node_modules" / "fresh" / "index.js", "fresh\n")
            return 0, "", ""
        return 1, "", "tsc failed"

    monkeypatch.setattr(updater, "_run_async", fake_run)
    monkeypatch.setattr(updater, "_bundled_node_install_dir", lambda: None)
    monkeypatch.setattr(updater, "_find_executable", lambda name: "/usr/bin/npm" if name == "npm" else None)
    monkeypatch.setattr(updater, "_record_update_journal", lambda _message: None)

    assert "tsc failed" in await updater._build_frontend_workspace(webui)

    assert (webui / "node_modules" / "esbuild" / "index.js").exists()
    assert not (webui / "node_modules" / "fresh").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("raises_at", ["ci", "run"])
async def test_a_build_step_that_cannot_even_start_leaves_node_modules_as_they_were(tmp_path, monkeypatch, raises_at) -> None:
    """Out of processes, say: an exception, not a failed command. The backup does not hold node_modules."""
    webui = tmp_path / "webui"
    _write(webui / "package.json", "{}\n")
    _write(webui / "package-lock.json", "{}\n")
    _write(webui / "node_modules" / "esbuild" / "index.js", "compiler\n")

    async def fake_run(cmd, **_kwargs):
        if cmd[1] == raises_at:
            raise OSError(11, "Resource temporarily unavailable")
        if cmd[1] == "install":
            return 1, "", "broken"
        return 0, "", ""

    monkeypatch.setattr(updater, "_run_async", fake_run)
    monkeypatch.setattr(updater, "_bundled_node_install_dir", lambda: None)
    monkeypatch.setattr(updater, "_find_executable", lambda name: "/usr/bin/npm" if name == "npm" else None)
    monkeypatch.setattr(updater, "_record_update_journal", lambda _message: None)

    with pytest.raises(OSError):
        await updater._build_frontend_workspace(webui)

    assert (webui / "node_modules" / "esbuild" / "index.js").exists()
    assert not (webui / "node_modules.before-npm-ci").exists()


@pytest.mark.asyncio
async def test_a_working_npm_ci_replaces_node_modules(tmp_path, monkeypatch) -> None:
    webui = _npm_workspace(tmp_path, monkeypatch, ci_works=True)

    assert await updater._build_frontend_workspace(webui) is None

    assert (webui / "node_modules" / "partial" / "index.js").exists()
    assert not (webui / "node_modules" / "esbuild").exists()
    assert not (webui / "node_modules.before-npm-ci").exists()


# ------------------------------------------------------------------ #
# A Node.js the service's PATH does not reach
# ------------------------------------------------------------------ #


def _fake_node(home: Path, version: str, *, npm: bool = True) -> Path:
    bin_dir = home / ".nvm" / "versions" / "node" / f"v{version}" / "bin"
    bin_dir.mkdir(parents=True)
    node = bin_dir / "node"
    node.write_text(f"#!/bin/sh\necho v{version}\n", encoding="utf-8")
    node.chmod(0o755)
    if npm:
        (bin_dir / "npm").write_text("#!/bin/sh\n", encoding="utf-8")
    return bin_dir.parent


skip_on_windows = pytest.mark.skipif(__import__("sys").platform == "win32", reason="POSIX paths")


@skip_on_windows
def test_nvm_is_found_when_the_path_of_the_service_has_no_node(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NVM_DIR", raising=False)
    monkeypatch.delenv("FLOCKS_NODE_HOME", raising=False)
    monkeypatch.delenv("FLOCKS_INSTALL_ROOT", raising=False)
    monkeypatch.setattr(service_manager, "which", lambda _name: None)
    monkeypatch.setattr(service_manager, "_node_install_dirs_outside_path", REAL_NODE_DIRS)
    _fake_node(tmp_path, "20.11.0")
    newest = _fake_node(tmp_path, "22.12.0")
    _fake_node(tmp_path, "9.0.0")

    assert service_manager.node_dir_outside_path() == newest
    assert service_manager.resolve_npm_executable() == str(newest.resolve() / "bin" / "npm")
    assert service_manager.node_version_satisfies_requirement() is True
    # The updater's own lookup finds the same one, and puts it on the PATH npm runs with.
    assert updater._bundled_node_install_dir() == newest


@skip_on_windows
def test_the_node_chosen_comes_first_on_the_path_npm_runs_with(tmp_path, monkeypatch) -> None:
    """npm is #!/usr/bin/env node: with an old system Node earlier on PATH, that one would build."""
    nvm_bin = str(tmp_path / "nvm" / "bin")
    monkeypatch.setenv("PATH", f"/usr/bin:{nvm_bin}:/bin")
    node_dir = Path(nvm_bin).parent
    monkeypatch.setattr(service_manager, "_bundled_node_install_dir", lambda: node_dir)

    assert updater._build_frontend_subprocess_env_for_node_dir(node_dir)["PATH"] == f"{nvm_bin}:/usr/bin:/bin"
    assert service_manager.build_frontend_env(service_manager.ServiceConfig())["PATH"].split(":")[:3] == [nvm_bin, "/usr/bin", "/bin"]


@skip_on_windows
def test_a_node_too_old_or_without_npm_is_no_answer(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NVM_DIR", raising=False)
    monkeypatch.setattr(service_manager, "which", lambda _name: None)
    monkeypatch.setattr(
        service_manager,
        "_node_install_dirs_outside_path",
        lambda: [_fake_node(tmp_path, "20.11.0"), _fake_node(tmp_path, "22.12.0", npm=False)],
    )

    assert service_manager.node_dir_outside_path() is None


@skip_on_windows
def test_a_usable_node_on_the_path_comes_first(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NVM_DIR", raising=False)
    monkeypatch.setattr(service_manager, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(service_manager, "_node_install_dirs_outside_path", REAL_NODE_DIRS)
    monkeypatch.setattr(service_manager, "_node_major_of", lambda _node: 22)
    _fake_node(tmp_path, "22.12.0")

    assert service_manager.node_dir_outside_path() is None


@skip_on_windows
def test_an_old_node_on_the_path_gives_way_to_a_usable_one_elsewhere(tmp_path, monkeypatch) -> None:
    """The system's Node.js on a service's PATH, nvm's newer one on the login shell's."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NVM_DIR", raising=False)
    monkeypatch.delenv("FLOCKS_NODE_HOME", raising=False)
    monkeypatch.delenv("FLOCKS_INSTALL_ROOT", raising=False)
    system_node = tmp_path / "system" / "node"
    system_node.parent.mkdir()
    system_node.write_text("#!/bin/sh\necho v18.19.0\n", encoding="utf-8")
    system_node.chmod(0o755)
    monkeypatch.setattr(service_manager, "which", lambda name: str(system_node) if name == "node" else "/usr/bin/npm")
    monkeypatch.setattr(service_manager, "_node_install_dirs_outside_path", REAL_NODE_DIRS)
    newest = _fake_node(tmp_path, "22.12.0")

    assert service_manager.node_dir_outside_path() == newest
    assert service_manager.get_node_major_version() == 22

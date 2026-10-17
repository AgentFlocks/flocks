"""Pinned release, tiers, MCP registration, install record and host checks for REA."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from flocks.rea import runtime


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("v26.8.1", (26, 8, 1)),
        ("22.19.0", (22, 19, 0)),
        ("v24.11.3\n", (24, 11, 3)),
        ("v27.0.0-nightly20261001", None),
        ("node", None),
        ("", None),
    ],
)
def test_parse_node_version(raw, expected):
    assert runtime.parse_node_version(raw) == expected


@pytest.mark.parametrize(
    ("version", "supported"),
    [
        ((22, 18, 9), False),
        ((22, 19, 0), True),
        ((22, 99, 0), True),
        ((23, 5, 0), False),
        ((24, 10, 9), False),
        ((24, 11, 0), True),
        ((25, 0, 0), False),
        ((26, 0, 0), True),
        ((30, 1, 2), True),
        ((20, 11, 1), False),
    ],
)
def test_node_version_range_matches_rea_engines(version, supported):
    assert runtime.node_version_supported(version) is supported


@pytest.mark.asyncio
async def test_environment_reports_missing_node_and_npx(monkeypatch):
    monkeypatch.setattr(runtime, "node_executable", lambda: None)
    monkeypatch.setattr(runtime, "npx_executable", lambda: None)
    checks = {check.key: check for check in await runtime.check_environment(refresh=True)}
    assert checks["node"].ok is False and checks["node"].code == "missing"
    assert checks["npx"].ok is False and checks["npx"].code == "missing"
    assert checks["storage"].ok is True


@pytest.mark.asyncio
async def test_environment_rejects_unsupported_node(fake_toolchain, monkeypatch):
    monkeypatch.setenv("FAKE_NODE_VERSION", "v23.4.0")
    checks = {check.key: check for check in await runtime.check_environment(refresh=True)}
    assert checks["node"].ok is False
    assert checks["node"].code == "unsupported"
    assert checks["node"].detail == "v23.4.0"
    assert checks["npx"].ok is True


@pytest.mark.asyncio
async def test_unparseable_node_output_is_not_shown(fake_toolchain, monkeypatch):
    monkeypatch.setenv("FAKE_NODE_VERSION", "/opt/internal/bin/node: bad option")
    checks = {check.key: check for check in await runtime.check_environment(refresh=True)}
    assert checks["node"].ok is False
    assert checks["node"].code == "unreadable"
    assert checks["node"].detail is None


@pytest.mark.asyncio
async def test_environment_is_cached_until_refresh(fake_toolchain, monkeypatch):
    first = await runtime.check_environment(refresh=True)
    assert all(check.ok for check in first)
    monkeypatch.setenv("FAKE_NODE_VERSION", "v20.0.0")
    assert (await runtime.check_environment())[0].ok is True
    assert (await runtime.check_environment(refresh=True))[0].ok is False


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root ignores directory permissions")
async def test_environment_reports_an_unwritable_data_directory(monkeypatch, tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    monkeypatch.setattr(runtime, "state_dir", lambda: locked / "data" / "rea")
    try:
        checks = {check.key: check for check in await runtime.check_environment(refresh=True)}
    finally:
        locked.chmod(0o700)
    assert checks["storage"].ok is False
    assert checks["storage"].code == "not_writable"


def test_paths_follow_flocks_root(rea_isolated_env):
    root = rea_isolated_env / ".flocks"
    assert runtime.install_record_path() == root / "data" / "rea" / "install.json"
    assert runtime.validation_root() == root / "data" / "rea" / "validation"
    assert runtime.legacy_install_root() == root / "plugins" / "rea"
    assert runtime.installed_skill_dir() == root / "plugins" / "skills" / runtime.REA_SKILL_NAME


def test_the_service_is_started_with_npx_and_the_pinned_version(fake_toolchain):
    npx = str(fake_toolchain / "npx")
    assert runtime.rea_command() == [npx, "-y", "rea-agents@6.4.0"]
    light = runtime.mcp_server_config("light")
    assert light == {
        "type": "local",
        "command": [npx, "-y", "rea-agents@6.4.0", "--mcp"],
        "enabled": True,
        "timeout": 60,
    }
    assert "environment" not in light  # the light tier writes no environment

    full = runtime.mcp_server_config("full")
    assert full["command"] == light["command"]
    assert full["environment"] == {
        "GHIDRA_INSTALL_DIR": "/opt/ghidra_12.1.4_PUBLIC",
        "JAVA_HOME": "/usr/lib/jvm/java-21-openjdk",
    }
    custom = runtime.mcp_server_config("full", java_home="/jdk21", ghidra_dir="/data/ghidra")
    assert custom["environment"] == {"GHIDRA_INSTALL_DIR": "/data/ghidra", "JAVA_HOME": "/jdk21"}


def test_without_npx_there_is_no_service_command(monkeypatch):
    monkeypatch.setattr(runtime, "npx_executable", lambda: None)
    assert runtime.rea_command() is None
    assert runtime.mcp_server_config("light") is None


def test_registration_matching_and_detection(fake_toolchain):
    from flocks.config.config_writer import ConfigWriter

    light = runtime.mcp_server_config("light")
    full = runtime.mcp_server_config("full")
    assert runtime.mcp_registered() is False
    assert runtime.mcp_config_matches(None, light) is False

    ConfigWriter.add_mcp_server(runtime.MCP_SERVER_NAME, light)
    current = runtime.current_mcp_config()
    assert runtime.mcp_registered() is True
    assert runtime.mcp_config_matches(current, light) is True
    # A tier change (environment) or another version is a different registration.
    assert runtime.mcp_config_matches(current, full) is False
    assert runtime.mcp_config_matches({**light, "command": [*light["command"][:2], "rea-agents@6.3.0", "--mcp"]}, light) is False
    assert runtime.mcp_config_matches({**light, "enabled": False}, light) is False

    ConfigWriter.add_mcp_server(runtime.MCP_SERVER_NAME, {**light, "enabled": False})
    assert runtime.mcp_registered() is False
    ConfigWriter.add_mcp_server(runtime.MCP_SERVER_NAME, {**light, "command": ["node", "/elsewhere/rea.mjs", "--mcp"]})
    assert runtime.mcp_registered() is False


def test_install_record_states(rea_isolated_env, fake_toolchain):
    from flocks.config.config_writer import ConfigWriter
    from tests.rea.conftest import write_skill

    assert runtime.read_install_record() is None
    assert runtime.is_ready() is False
    runtime.write_install_record({"version": runtime.REA_VERSION, "tier": "light", "verification": {"ok": False}})
    assert runtime.recorded_tier() == "light"
    assert runtime.verification_passed() is False
    assert runtime.ever_verified() is False

    runtime.write_install_record({
        "version": runtime.REA_VERSION, "tier": "light", "verification": {"ok": True}, "firstPassedAt": 1,
    })
    assert runtime.verification_passed() is True
    assert runtime.ever_verified() is True
    assert runtime.is_configured() is False and runtime.is_ready() is False  # nothing configured yet

    write_skill(runtime.installed_skill_dir())
    ConfigWriter.add_mcp_server(runtime.MCP_SERVER_NAME, runtime.mcp_server_config("light"))
    assert runtime.is_configured() is True
    assert runtime.is_ready() is True

    runtime.write_install_record({"version": "6.3.0", "tier": "light", "verification": {"ok": True}})
    assert runtime.is_ready() is False
    runtime.drop_install_record()
    runtime.drop_install_record()  # dropping twice is fine
    assert runtime.read_install_record() is None


def test_concurrent_record_writes_never_share_a_temp_file(rea_isolated_env):
    errors = []

    def write(index: int) -> None:
        try:
            for round_ in range(25):
                runtime.write_install_record({"writer": index, "round": round_, "pad": "x" * 2000})
        except Exception as exc:  # pragma: no cover - the failure being tested
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    record = json.loads(runtime.install_record_path().read_text(encoding="utf-8"))
    assert record["pad"] == "x" * 2000
    assert not list(runtime.install_record_path().parent.glob(".install.json.*.tmp"))


def test_public_detail_hides_home_and_url_credentials():
    home = str(Path.home())
    text = f"npm error 404 https://ci:token123@registry.internal.example/rea-agents\n at {home}/.flocks/plugins"
    shown = runtime.public_detail(text)
    assert "token123" not in shown and "ci:" not in shown
    assert "https://***@registry.internal.example/rea-agents" in shown
    assert home not in shown and "~/.flocks/plugins" in shown
    assert "\n" not in shown
    assert runtime.public_detail("x" * 50, 10) == "x" * 9 + "…"
    assert runtime.public_detail(None) is None


def test_full_tier_lookups_honour_the_overrides(tmp_path, monkeypatch):
    ghidra = tmp_path / "opt" / "ghidra_12.1.4_PUBLIC"
    monkeypatch.setenv("FLOCKS_REA_GHIDRA_DIR", str(ghidra))
    assert runtime.ghidra_target_dir() == str(ghidra)
    assert runtime.find_ghidra_dir() is None
    (ghidra / "support").mkdir(parents=True)
    (ghidra / "support" / "analyzeHeadless").write_text("", encoding="utf-8")
    assert runtime.find_ghidra_dir() is None  # incomplete without the application properties
    (ghidra / "Ghidra").mkdir()
    # Another Ghidra version is not the one REA 6.4.0 was validated with.
    (ghidra / "Ghidra" / "application.properties").write_text("application.version=11.4.2\n", encoding="utf-8")
    assert runtime.find_ghidra_dir() is None
    for text in ("# application.version=12.1.4\napplication.version=11.4.2\n", "application.version=12.1.40\n"):
        (ghidra / "Ghidra" / "application.properties").write_text(text, encoding="utf-8")
        assert runtime.find_ghidra_dir() is None
    (ghidra / "Ghidra" / "application.properties").write_text("application.version=12.1.4\n", encoding="utf-8")
    assert runtime.find_ghidra_dir() == str(ghidra)

    monkeypatch.delenv("FLOCKS_REA_GHIDRA_DIR")
    assert runtime.ghidra_target_dir() == runtime.GHIDRA_DIR_DEFAULT


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="shell script stands in for java")
async def test_find_java_home_needs_jdk_21(tmp_path, monkeypatch):
    home = tmp_path / "jdk"
    (home / "bin").mkdir(parents=True)
    java = home / "bin" / "java"
    monkeypatch.setattr(runtime, "java_home_candidates", lambda: [str(home)])

    (home / "bin" / "javac").write_text("", encoding="utf-8")
    java.write_text('#!/bin/sh\necho \'openjdk version "17.0.12" 2024-07-16\' >&2\n', encoding="utf-8")
    java.chmod(0o755)
    assert await runtime.find_java_home() is None
    java.write_text('#!/bin/sh\necho \'openjdk version "21.0.12" 2026-07-15\' >&2\n', encoding="utf-8")
    assert await runtime.find_java_home() == str(home)
    # A JRE 21 (no javac) is not the JDK the full tier needs.
    (home / "bin" / "javac").unlink()
    assert await runtime.find_java_home() is None


def test_java_home_candidates_put_the_override_first(monkeypatch):
    lookup = runtime.unpatched_java_home_candidates
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setenv("FLOCKS_REA_JAVA_HOME", "/custom/jdk21")
    candidates = lookup()
    assert candidates[:2] == ["/custom/jdk21", "/usr/lib/jvm/java-21-openjdk"]
    assert len(candidates) == len(set(candidates))
    monkeypatch.delenv("FLOCKS_REA_JAVA_HOME")
    assert lookup()[0] == "/usr/lib/jvm/java-21-openjdk"
    # A JDK the host already points at through the standard variable is looked at too.
    monkeypatch.setenv("JAVA_HOME", "/srv/jdk-21")
    assert lookup()[:2] == ["/srv/jdk-21", "/usr/lib/jvm/java-21-openjdk"]


@pytest.mark.skipif(os.name == "nt", reason="symlinked java launcher")
def test_the_jdk_behind_java_on_path_is_a_candidate(tmp_path, monkeypatch):
    home = tmp_path / "jdk-21"
    (home / "bin").mkdir(parents=True)
    for name in ("java", "javac"):
        (home / "bin" / name).write_text("", encoding="utf-8")
    (home / "release").write_text('JAVA_VERSION="21.0.4"\n', encoding="utf-8")
    links = tmp_path / "usr-bin"
    links.mkdir()
    (links / "java").symlink_to(home / "bin" / "java")
    (links / "java").chmod(0o755)
    (home / "bin" / "java").chmod(0o755)
    monkeypatch.setenv("PATH", str(links))
    assert str(home.resolve()) in runtime.unpatched_java_home_candidates()


_HEALTHY = {
    "healthy": True,
    "server": {"name": "rea", "version": "6.4.0"},
    "inventory": {"tools": {"expected": 170, "observed": 170}},
    "request_flow": {"tool": "binary_session", "ok": True},
    "checks": [{"name": "initialize", "ok": True}, {"name": "request-flow", "ok": True}],
}


def _doctor(**changes):
    data = json.loads(json.dumps(_HEALTHY))
    for key, value in changes.items():
        data[key] = value
    return json.dumps(data)


def test_parse_doctor_report_accepts_a_healthy_service():
    report = runtime.parse_doctor_report("npm warn exec The following package was not found\n" + _doctor())
    assert report.ok is True
    assert report.server_version == "6.4.0"
    assert (report.tools_expected, report.tools_observed) == (170, 170)
    assert (report.request_tool, report.request_ok) == ("binary_session", True)
    assert report.failed_checks == ()
    assert report.to_dict()["failed_checks"] == []


@pytest.mark.parametrize(
    "raw",
    [
        _doctor(healthy=False),
        _doctor(checks=[{"name": "initialize", "ok": True}, {"name": "inventory:prompts", "ok": False}]),
        _doctor(request_flow={"tool": "binary_session", "ok": False}),
        _doctor(request_flow={"tool": "binary_session"}),
        _doctor(inventory={"tools": {"expected": 170, "observed": 12}}),
        _doctor(inventory={"tools": {"expected": 0, "observed": 0}}),
        _doctor(inventory={"tools": {"expected": True, "observed": True}}),
        _doctor(inventory={}),
        "[]",
        "doctor crashed",
        "",
    ],
)
def test_parse_doctor_report_rejects_anything_short_of_a_working_service(raw):
    assert runtime.parse_doctor_report(raw).ok is False


def test_parse_doctor_report_keeps_the_failed_check_names_and_raw_errors():
    report = runtime.parse_doctor_report(
        _doctor(checks=[{"name": "inventory:tools", "ok": False}, {"name": "request-flow", "ok": False}]),
    )
    assert report.failed_checks == ("inventory:tools", "request-flow")
    assert runtime.parse_doctor_report("spawn ENOENT\n").error == "spawn ENOENT"
    assert runtime.parse_doctor_report("").error == "no output"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "ok", "observed", "error"),
    [("ok", True, 170, None), ("unhealthy", False, 12, None), ("crash", False, None, "doctor crashed")],
)
async def test_run_doctor_runs_rea_through_npx(fake_toolchain, monkeypatch, mode, ok, observed, error):
    monkeypatch.setenv("FAKE_DOCTOR_MODE", mode)
    report = await runtime.run_doctor()
    assert report.ok is ok
    assert report.tools_observed == observed
    assert report.error == error


@pytest.mark.asyncio
async def test_run_doctor_without_npx(monkeypatch):
    monkeypatch.setattr(runtime, "npx_executable", lambda: None)
    report = await runtime.run_doctor()
    assert report.ok is False
    assert report.error == "npx not found"


@pytest.mark.asyncio
async def test_run_doctor_passes_the_full_tier_environment(monkeypatch):
    seen = {}

    async def fake_run(command, timeout, env=None):
        seen["command"], seen["env"] = command, env
        return 0, _doctor()

    monkeypatch.setattr(runtime, "rea_command", lambda: ["npx", "-y", "rea-agents@6.4.0"])
    monkeypatch.setattr(runtime, "run_process", fake_run)
    report = await runtime.run_doctor({"GHIDRA_INSTALL_DIR": "/opt/g", "JAVA_HOME": "/jdk"})
    assert report.ok is True
    assert seen["command"] == ["npx", "-y", "rea-agents@6.4.0", "mcp", "doctor", "--json"]
    assert seen["env"]["GHIDRA_INSTALL_DIR"] == "/opt/g" and seen["env"]["JAVA_HOME"] == "/jdk"


@pytest.mark.asyncio
async def test_a_healthy_report_with_a_failing_exit_code_is_not_trusted(monkeypatch):
    async def fake_run(command, timeout, env=None):
        return 3, _doctor()

    monkeypatch.setattr(runtime, "rea_command", lambda: ["npx", "-y", "rea-agents@6.4.0"])
    monkeypatch.setattr(runtime, "run_process", fake_run)
    report = await runtime.run_doctor()
    assert report.ok is False
    assert report.error == "exit code 3"
    assert report.tools_observed == 170


def test_windows_runs_npx_cli_with_node_and_never_through_cmd(tmp_path, monkeypatch):
    install = tmp_path / "Program Files" / "nodejs"
    install.mkdir(parents=True)
    shim = install / "npx.cmd"
    shim.write_text("", encoding="utf-8")
    node = str(install / "node.exe")
    monkeypatch.setattr(runtime, "_is_windows", lambda: True)
    monkeypatch.setattr(runtime, "npx_executable", lambda: str(shim))
    monkeypatch.setattr(runtime, "node_executable", lambda: node)
    monkeypatch.setenv("APPDATA", str(tmp_path / "John Smith" / "AppData" / "Roaming"))

    # No npx CLI script anywhere: npx counts as missing rather than going through cmd.exe.
    assert runtime.npx_command() is None
    check = runtime._check_npx()
    assert check.ok is False and check.code == "missing"

    global_cli = tmp_path / "John Smith" / "AppData" / "Roaming" / "npm" / "node_modules" / "npm" / "bin" / "npx-cli.js"
    global_cli.parent.mkdir(parents=True)
    global_cli.write_text("", encoding="utf-8")
    assert runtime.npx_command() == [node, str(global_cli)]

    bundled_cli = install / "node_modules" / "npm" / "bin" / "npx-cli.js"
    bundled_cli.parent.mkdir(parents=True)
    bundled_cli.write_text("", encoding="utf-8")
    assert runtime.npx_command() == [node, str(bundled_cli.resolve())]
    assert runtime.rea_command() == [node, str(bundled_cli.resolve()), "-y", "rea-agents@6.4.0"]

    native = tmp_path / "Volta" / "bin" / "npx.exe"
    monkeypatch.setattr(runtime, "npx_executable", lambda: str(native))
    assert runtime.npx_command() == [str(native)]


_TREE_SCRIPT = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
open(sys.argv[1], "w").write(str(child.pid))
time.sleep(60)
"""


def _wait_for_pid(pid_file, timeout=10.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pid_file.exists() and pid_file.read_text().strip():
            return int(pid_file.read_text())
        time.sleep(0.05)
    raise AssertionError("child pid not written")


def _gone(pid, timeout=5.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
async def test_run_process_timeout_kills_the_whole_tree(tmp_path):
    import asyncio
    import sys

    pid_file = tmp_path / "child.pid"
    script = tmp_path / "tree.py"
    script.write_text(_TREE_SCRIPT, encoding="utf-8")
    run = asyncio.create_task(runtime.run_process([sys.executable, str(script), str(pid_file)], timeout=3))
    grandchild = await asyncio.to_thread(_wait_for_pid, pid_file)
    with pytest.raises(asyncio.TimeoutError):
        await run
    assert _gone(grandchild)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
async def test_cancelling_run_process_kills_the_whole_tree(tmp_path):
    import asyncio
    import sys

    pid_file = tmp_path / "child.pid"
    script = tmp_path / "tree.py"
    script.write_text(_TREE_SCRIPT, encoding="utf-8")
    run = asyncio.create_task(runtime.run_process([sys.executable, str(script), str(pid_file)], timeout=60))
    grandchild = await asyncio.to_thread(_wait_for_pid, pid_file)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert _gone(grandchild)


_ORPHAN_SCRIPT = """
import subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
open(sys.argv[1], "w").write(str(child.pid))
"""


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
async def test_descendants_are_killed_after_the_leader_already_exited(tmp_path):
    import asyncio
    import sys

    # The leader exits at once; its child keeps the output pipe open, so the
    # read times out while the leader's return code is already known.
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "orphan.py"
    script.write_text(_ORPHAN_SCRIPT, encoding="utf-8")
    run = asyncio.create_task(runtime.run_process([sys.executable, str(script), str(pid_file)], timeout=3))
    orphan = await asyncio.to_thread(_wait_for_pid, pid_file)
    with pytest.raises(asyncio.TimeoutError):
        await run
    assert _gone(orphan)


@pytest.mark.asyncio
async def test_run_in_thread_waits_for_the_thread_when_every_task_is_cancelled():
    import asyncio
    import threading
    import time

    started = threading.Event()
    finished = threading.Event()

    def slow():
        started.set()
        time.sleep(0.4)
        finished.set()

    outer = asyncio.create_task(runtime.run_in_thread(slow))
    await asyncio.to_thread(started.wait, 5)
    # What a shutting-down loop does: cancel every task, the inner one included.
    current = asyncio.current_task()
    for task in asyncio.all_tasks():
        if task is not current:
            task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer
    assert finished.is_set()



@pytest.mark.asyncio
async def test_run_in_thread_still_runs_and_ends_when_cancelled_while_executors_are_busy():
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor

    # A pooled executor with every worker busy would only queue the work; a
    # shutdown that cancels everything then drops it and the wait never ends.
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
    blocker = threading.Event()
    busy = loop.run_in_executor(None, blocker.wait)
    ran = threading.Event()
    try:
        outer = asyncio.create_task(runtime.run_in_thread(ran.set))
        await asyncio.sleep(0)
        current = asyncio.current_task()
        for task in asyncio.all_tasks():
            if task is not current:
                task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(outer, timeout=5)
        assert ran.is_set()
    finally:
        blocker.set()
        await asyncio.gather(busy, return_exceptions=True)


@pytest.mark.asyncio
async def test_run_in_thread_returns_results_and_raises_errors():
    assert await runtime.run_in_thread(lambda a, b=0: a + b, 2, b=3) == 5

    def boom():
        raise PermissionError("read-only")

    with pytest.raises(PermissionError):
        await runtime.run_in_thread(boom)



@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
async def test_tree_is_killed_while_shutdown_keeps_cancelling_every_task(tmp_path):
    import asyncio
    import sys

    pid_file = tmp_path / "child.pid"
    script = tmp_path / "tree.py"
    script.write_text(_TREE_SCRIPT, encoding="utf-8")
    run = asyncio.create_task(runtime.run_process([sys.executable, str(script), str(pid_file)], timeout=60))
    grandchild = await asyncio.to_thread(_wait_for_pid, pid_file)
    current = asyncio.current_task()
    # Keep cancelling everything for a while, as a shutdown does: later rounds
    # also hit whatever the first cancel set in motion before it could run.
    for _ in range(20):
        for task in asyncio.all_tasks():
            if task is not current:
                task.cancel()
        await asyncio.sleep(0)
    with pytest.raises(asyncio.CancelledError):
        await run
    assert _gone(grandchild)


@pytest.mark.asyncio
async def test_a_hung_taskkill_is_stopped_and_the_child_killed(monkeypatch):
    class FakeKiller:
        killed = False

        def poll(self):
            return 0 if self.killed else None

        def kill(self):
            self.killed = True

    class FakeProc:
        pid = 4321
        returncode = None
        kills = 0

        def kill(self):
            self.kills += 1

    monkeypatch.setattr(runtime, "_KILL_WAIT_SECONDS", 0.2)
    killer, proc = FakeKiller(), FakeProc()
    await runtime._finish_tree_kill(proc, killer)
    assert killer.killed is True
    assert proc.kills >= 1


@pytest.mark.parametrize(("text", "shown"), [
    ("cd /srv/my-tools/ghidra/support/gradle && ./gradlew buildNatives", "cd <path> && ./gradlew buildNatives"),
    ("Permission denied: ~/.flocks/plugins/skills/reverse-engineer-anything", "Permission denied: <path>"),
    ("JAVA_HOME=/srv/jdk-21 GHIDRA_INSTALL_DIR=D:\\tools\\ghidra", "JAVA_HOME=<path> GHIDRA_INSTALL_DIR=<path>"),
    ('{"result":{"path":"/srv/flocks/data/rea/validation/r/native/calc","format":"mach-o"}}',
     '{"result":{"path":"<path>","format":"mach-o"}}'),
    ("数据目录 /srv/flocks/data 不可写", "数据目录 <path> 不可写"),
    # Folder names with spaces, either separator, UNC shares.
    ("cd /srv/my tools/ghidra/support/gradle && ./gradlew buildNatives", "cd <path> && ./gradlew buildNatives"),
    ("data at C:/Users/John Smith/.flocks/data now", "data at <path> now"),
    ("C:\\Program Files\\Ghidra\\support missing", "<path> missing"),
    ("share \\\\fileserver\\tools\\ghidra 12\\support is gone", "share <path> is gone"),
    # A network address keeps its path, query and fragment; a local file address is a path.
    ("see https://example.com/a?next=/admin#/x and /srv/x", "see https://example.com/a?next=/admin#/x and <path>"),
    ("open file:///home/ops/.flocks/data/rea/x.js failed", "open <path> failed"),
    ("FILE://C:/Users/John/x.dll and file://fileserver/share/x", "<path> and <path>"),
    # Not host paths: URLs, relative paths, ratios, the guide's own locations.
    ("https://ghfast.top/https://github.com/NationalSecurityAgency/ghidra/releases/x.zip",
     "https://ghfast.top/https://github.com/NationalSecurityAgency/ghidra/releases/x.zip"),
    ("REA 定位 calc 于 index.js:2-4; 170/170 tools; ./gradlew", "REA 定位 calc 于 index.js:2-4; 170/170 tools; ./gradlew"),
    ("install JDK 21 at /usr/lib/jvm/java-21-openjdk, unpack to /opt/ghidra_12.1.4_PUBLIC.",
     "install JDK 21 at /usr/lib/jvm/java-21-openjdk, unpack to /opt/ghidra_12.1.4_PUBLIC."),
])
def test_conversations_keep_no_host_paths(text, shown):
    assert runtime.redact_paths(text) == shown


def test_a_card_field_holding_a_path_is_replaced_whole():
    assert runtime.redact_paths({"path": "/srv/my tools/ghidra", "detail": "C:/Users/John Smith"}) == {
        "path": "<path>", "detail": "<path>",
    }
    assert runtime.redact_paths({"path": "/opt/ghidra_12.1.4_PUBLIC"}) == {"path": "/opt/ghidra_12.1.4_PUBLIC"}


def test_redaction_walks_card_data():
    data = {"blockers": [{"code": "ghidra_dir_occupied", "path": "/srv/ghidra", "env": "FLOCKS_REA_GHIDRA_DIR"}],
            "ok": False, "count": 2, "notes": ["at /srv/x", None]}
    assert runtime.redact_paths(data) == {
        "blockers": [{"code": "ghidra_dir_occupied", "path": "<path>", "env": "FLOCKS_REA_GHIDRA_DIR"}],
        "ok": False, "count": 2, "notes": ["at <path>", None],
    }

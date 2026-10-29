"""Host cleanup keeps exit races distinct from a live child's signal failure."""

from __future__ import annotations

import errno
import os
import select
import signal
import subprocess
import sys

import pytest

from flocks.workflow.repl_runtime import HostProcessPythonExecRuntime


pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process-group signals")


@pytest.fixture
def child_process():
    native_killpg = os.killpg
    proc = subprocess.Popen(
        [sys.executable, "-c", (
            "import signal, sys\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('ready', flush=True)\n"
            "sys.stdin.read(1)\n"
        )],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        assert select.select([proc.stdout], [], [], 3)[0], "child did not become ready"
        assert proc.stdout.readline() == "ready\n"
        yield proc
    finally:
        if proc.poll() is None:
            native_killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=1)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            stream.close()


def test_termination_handles_native_child_exit_between_poll_and_signal(child_process, monkeypatch):
    proc = child_process
    native_poll = proc.poll
    raced = False

    def poll_before_child_exit():
        nonlocal raced
        result = native_poll()
        if not raced:
            assert result is None
            raced = True
            proc.stdin.write("x")
            proc.stdin.flush()
            assert proc.stdout.read() == ""
        return result

    monkeypatch.setattr(proc, "poll", poll_before_child_exit)
    HostProcessPythonExecRuntime._terminate_process(proc)

    assert raced
    assert proc.wait(timeout=1) == 0


@pytest.mark.parametrize("denied_signal", [signal.SIGTERM, signal.SIGKILL])
@pytest.mark.parametrize("exits_after_denial", [True, False])
def test_signal_denial_is_ignored_only_after_child_exit(
    child_process, monkeypatch, denied_signal, exits_after_denial,
):
    proc = child_process
    native_killpg = os.killpg
    denied = PermissionError(errno.EPERM, "controlled process-group denial")
    signals = []

    def deny_after_optional_exit(pgid, signum):
        assert pgid == proc.pid
        signals.append(signum)
        if signum == denied_signal:
            if exits_after_denial:
                proc.stdin.write("x")
                proc.stdin.flush()
            raise denied
        return native_killpg(pgid, signum)

    monkeypatch.setattr(os, "killpg", deny_after_optional_exit)
    if exits_after_denial:
        HostProcessPythonExecRuntime._terminate_process(proc)
        assert proc.wait(timeout=1) == 0
    else:
        with pytest.raises(PermissionError) as caught:
            HostProcessPythonExecRuntime._terminate_process(proc)
        assert caught.value is denied
        assert proc.poll() is None
    assert signals == ([signal.SIGTERM] if denied_signal == signal.SIGTERM else [signal.SIGTERM, signal.SIGKILL])


def test_termination_escalates_when_sigterm_is_ignored(child_process, monkeypatch):
    native_killpg = os.killpg
    signals = []

    def record_signal(pgid, signum):
        assert pgid == child_process.pid
        signals.append(signum)
        return native_killpg(pgid, signum)

    monkeypatch.setattr(os, "killpg", record_signal)
    HostProcessPythonExecRuntime._terminate_process(child_process)

    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert child_process.returncode == -signal.SIGKILL

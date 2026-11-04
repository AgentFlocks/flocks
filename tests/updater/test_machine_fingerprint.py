"""flocks.updater.machine_fingerprint: this machine as the enterprise core names it.

Case list (编号 | 场景):
  MF1 the digest is the core's (activation.MachineFingerprint): sha256 of the domain and the id, trimmed
      and lower-cased, its first 16 bytes in hex; no id gives none
  MF2 on Linux the machine id comes from /etc/machine-id, then the D-Bus copy; none when neither has one;
      one that cannot be read is MachineIdUnreadable, not none
  MF3 on macOS it comes from the platform UUID ioreg reports; ioreg not running, failing or naming none
      is MachineIdUnreadable (every Mac has one)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from flocks.updater import machine_fingerprint as mf


def test_mf1_the_digest_is_the_cores():
    # printf 'flocks-machine/1\0abc' | shasum -a 256 | cut -c1-32, the core's fingerprintOf("abc").
    assert mf.fingerprint_of(" ABC\n") == "85e01c97341975df4bd62d6721b5d102"
    assert mf.fingerprint_of("  ") == ""


def test_mf2_linux_reads_the_machine_id_then_the_dbus_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    etc, dbus = tmp_path / "machine-id", tmp_path / "dbus-machine-id"
    monkeypatch.setattr(mf.sys, "platform", "linux")
    monkeypatch.setattr(mf, "LINUX_MACHINE_ID_FILES", (etc, dbus))
    assert mf.machine_fingerprint() == ""
    dbus.write_text("ABC\n")
    assert mf.machine_fingerprint() == mf.fingerprint_of("abc")
    etc.write_text("\n")
    assert mf.machine_fingerprint() == mf.fingerprint_of("abc"), "an empty machine-id falls through to the D-Bus copy"
    etc.write_text("def\n")
    assert mf.machine_fingerprint() == mf.fingerprint_of("def")

    def unreadable(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(mf.MachineIdUnreadable, match="Permission denied"):
        mf.machine_fingerprint()


def test_mf3_macos_reads_the_platform_uuid(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mf.sys, "platform", "darwin")
    calls: list[list[str]] = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout='  "IOPlatformUUID" = "1A2B-3C4D"\n', stderr="")

    monkeypatch.setattr(mf.subprocess, "run", run)
    assert mf.machine_fingerprint() == mf.fingerprint_of("1a2b-3c4d")
    assert calls == [["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"]]

    monkeypatch.setattr(mf.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="nothing", stderr=""))
    with pytest.raises(mf.MachineIdUnreadable, match="IOPlatformUUID"):
        mf.machine_fingerprint()
    failing = '  "IOPlatformUUID" = "1A2B-3C4D"\n'
    monkeypatch.setattr(mf.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout=failing, stderr=""))
    with pytest.raises(mf.MachineIdUnreadable, match="退出码 1"):
        mf.machine_fingerprint()

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("/usr/sbin/ioreg", 10)

    monkeypatch.setattr(mf.subprocess, "run", timeout)
    with pytest.raises(mf.MachineIdUnreadable):
        mf.machine_fingerprint()

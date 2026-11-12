"""flocks.updater.machine_fingerprint: this machine as the enterprise core names it.

Case list (编号 | 场景):
  MF1 the machine-id digest is the core's: sha256 of the domain and the id, trimmed and lower-cased, its first
      16 bytes in hex; no id gives none
  MF2 the device id is the core's and the library's: MD5 of "flocks" and what it hashes, the digits libdevid
      printed on a Linux VM (in lower case), real UUIDs put in its SMBIOS table among them; UUIDs format as
      dmidecode prints them, in lower case; the SMBIOS table walk finds the type 1 UUID and stops at a table cut
      short -- the same cases as flockspro go/internal/devid
  MF3 on Linux a readable SMBIOS table with the host's UUID gives the device id; without root (no table), or a
      UUID the firmware leaves unset, the machine id gives the digest, from /etc/machine-id, then the D-Bus copy;
      none when neither has one; one that cannot be read is MachineIdUnreadable, not none
  MF4 an AWS Xen host hashes its board asset tag, else its hypervisor UUID, which any account may read
  MF5 on macOS the platform UUID ioreg reports gives the device id; ioreg not running, failing or naming none
      is MachineIdUnreadable (every Mac has one)
  MF6 elsewhere (Windows) the host is not named: applications go out unbound, as before
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


def test_mf2_the_device_id_is_the_librarys():
    # get_dev_id("flocks", "") of libdevid-1.2.6 on a QEMU VM whose firmware sets no UUID: none read without
    # root, "Not Settable" with.
    assert mf.device_id_of("") == "58ab768421a90d0fec6ecff8ea0b1bf5"
    assert mf.device_id_of("Not Settable") == "a6d4534ba1eb061f2eb21438bdaec761"
    # Real UUIDs as the table holds them (SMBIOS 2.8), and what libdevid printed for each once root bind-mounted
    # that table over /sys/firmware/dmi/tables/DMI. Upper-case text hashes to other ids.
    for raw, want in {
        "44454c4c350010308030b4c04f4b4b32": "4118c84a2b529d4532ecef9d021c2ae3",
        "0123456789abcdeffedcba9876543210": "c7093283e36914e17c95fb40281665fc",
        "a1b2c3d4e5f60718293a4b5c6d7e8f90": "2d71ce041ce578e2da445b203efb372f",
    }.items():
        assert mf.device_id_of(mf.format_smbios_uuid(bytes.fromhex(raw), 2, 8)) == want, raw

    uuid = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88])
    assert mf.format_smbios_uuid(bytes(16), 2, 8) == "Not Settable"
    assert mf.format_smbios_uuid(b"\xff" * 16, 3, 0) == "Not Present"
    assert mf.format_smbios_uuid(uuid, 2, 8) == "78563412-bc9a-f0de-1122-334455667788"
    assert mf.format_smbios_uuid(uuid, 3, 2) == "78563412-bc9a-f0de-1122-334455667788"
    assert mf.format_smbios_uuid(uuid, 2, 5) == "12345678-9abc-def0-1122-334455667788"
    assert mf.format_smbios_uuid(uuid[:15], 2, 8) == ""

    bios = bytes([0, 0x12, 0, 0]) + bytes(0x0E) + b"v1\0\0"
    system = bytes([1, 0x1B, 1, 0, 1, 2, 0, 0]) + uuid + bytes([6, 0, 0]) + b"QEMU\0pc\0\0"
    end = bytes([127, 4, 2, 0, 0, 0])
    assert mf.smbios_system_uuid(bios + system + end, 2, 8) == "78563412-bc9a-f0de-1122-334455667788"
    for name, cut in {
        "no type 1": bios + end,
        "type 1 before the UUID": bytes([1, 0x08, 1, 0, 1, 2, 0, 0, 0, 0]),
        "length past the end": bytes([1, 0x40, 1, 0, 0, 0]),
        "length under four": bytes([1, 2, 0, 0, 0, 0]),
        "UUID cut short": system[:20],
        "empty": b"",
    }.items():
        assert mf.smbios_system_uuid(cut, 2, 8) == "", name


def _linux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    paths = {
        "etc": tmp_path / "machine-id", "dbus": tmp_path / "dbus-machine-id", "entry": tmp_path / "smbios_entry_point",
        "table": tmp_path / "DMI", "hypervisor": tmp_path / "hypervisor-uuid", "tag": tmp_path / "board_asset_tag",
    }
    monkeypatch.setattr(mf.sys, "platform", "linux")
    monkeypatch.setattr(mf, "LINUX_MACHINE_ID_FILES", (paths["etc"], paths["dbus"]))
    monkeypatch.setattr(mf, "LINUX_SMBIOS_ENTRY_POINT", paths["entry"])
    monkeypatch.setattr(mf, "LINUX_SMBIOS_TABLE", paths["table"])
    monkeypatch.setattr(mf, "LINUX_HYPERVISOR_UUID", paths["hypervisor"])
    monkeypatch.setattr(mf, "LINUX_BOARD_ASSET_TAG", paths["tag"])
    return paths


def _table(uuid: bytes) -> bytes:
    return bytes([1, 0x1B, 1, 0, 1, 2, 0, 0]) + uuid + bytes([6, 0, 0]) + b"QEMU\0pc\0\0" + bytes([127, 4, 2, 0, 0, 0])


def test_mf3_linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = _linux(monkeypatch, tmp_path)
    # No SMBIOS table to read (no root here) and no machine id: nothing names the host.
    assert mf.machine_identity() == ("", "")
    paths["dbus"].write_text("ABC\n")
    assert mf.machine_identity() == (mf.fingerprint_of("abc"), mf.KIND_MACHINE_ID)
    paths["etc"].write_text("\n")
    assert mf.machine_fingerprint() == mf.fingerprint_of("abc"), "an empty machine-id falls through to the D-Bus copy"
    paths["etc"].write_text("def\n")
    assert mf.machine_identity() == (mf.fingerprint_of("def"), mf.KIND_MACHINE_ID)

    # SMBIOS 3.0 entry point (version at bytes 7 and 8), and the table root may read.
    uuid = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88])
    paths["entry"].write_bytes(b"_SM3_" + bytes([0x18, 0x18, 3, 2]) + bytes(15))
    paths["table"].write_bytes(_table(uuid))
    assert mf.machine_identity() == (mf.device_id_of("78563412-bc9a-f0de-1122-334455667788"), mf.KIND_DEVID)
    # A 32-bit entry point of SMBIOS 2.4: the UUID as the firmware has it.
    paths["entry"].write_bytes(b"_SM_" + bytes([0, 0x1F, 2, 4]) + bytes(23))
    assert mf.machine_fingerprint() == mf.device_id_of("12345678-9abc-def0-1122-334455667788")
    # A UUID the firmware leaves unset names no host: the digest does.
    paths["table"].write_bytes(_table(bytes(16)))
    assert mf.machine_identity() == (mf.fingerprint_of("def"), mf.KIND_MACHINE_ID)
    paths["table"].write_bytes(_table(uuid))

    def unreadable(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied")

    # The machine id is not read when the device id is there; without it, it must be.
    monkeypatch.setattr(Path, "read_text", unreadable)
    assert mf.machine_identity()[1] == mf.KIND_DEVID
    paths["table"].unlink()
    with pytest.raises(mf.MachineIdUnreadable, match="Permission denied"):
        mf.machine_fingerprint()


def test_mf4_an_aws_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = _linux(monkeypatch, tmp_path)
    paths["hypervisor"].write_text("ec2e1916-9099-7caf-fd21-012345abcdef\n")
    assert mf.machine_identity() == (mf.device_id_of("ec2e1916-9099-7caf-fd21-012345abcdef"), mf.KIND_DEVID)
    paths["tag"].write_text("i-0abc\n")
    assert mf.machine_identity() == (mf.device_id_of("i-0abc"), mf.KIND_DEVID)
    paths["hypervisor"].write_text("4c4c4544-0035-3010-8030-b4c04f4b4b32\n")
    assert mf.machine_identity() == ("", ""), "a hypervisor UUID that is no EC2 one is no AWS host"


def test_mf5_macos_reads_the_platform_uuid(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mf.sys, "platform", "darwin")
    calls: list[list[str]] = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout='  "IOPlatformUUID" = "1A2B-3C4D"\n', stderr="")

    monkeypatch.setattr(mf.subprocess, "run", run)
    assert mf.machine_identity() == (mf.device_id_of("1a2b-3c4d"), mf.KIND_DEVID)
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


def test_mf6_elsewhere_the_host_is_not_named(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mf.sys, "platform", "win32")
    assert mf.machine_identity() == ("", "")

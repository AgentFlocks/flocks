"""
This machine, as the enterprise core names it.

The enterprise core (flockspro ``go/internal/activation``) knows a host by two fingerprints, and holds a licence
bound to either to that host:

- the digest of the identifier the operating system keeps for it (/etc/machine-id, the Mac's platform UUID), the
  way every core has named it since licences were first bound to a machine;
- its device id, where it can be read: the fingerprint the company's other products license hosts by, computed
  as the company's libdevid computes it on its default path -- the MD5 of the device type ``flocks`` and the
  host's SMBIOS system UUID as dmidecode prints it, in lower case (on an AWS Xen host, its board asset tag or
  hypervisor UUID), in hex (``flockspro go/internal/devid``).

An application names the digest for the licence to be bound to wherever the host keeps an identifier, as the
core's own request does: an enterprise core from before the device id knows the host by the digest alone, and
so does one run by an account that cannot read the device id (on Linux only root may read the SMBIOS table, and
a core under an account of its own reads it only from a record root made). Bound to the device id, the licence
would be another host's to those. The device id goes beside it (``devid``), for Console's records. Only a host
that keeps no identifier (a container without /etc/machine-id) is named by its device id, where it has one.

Both are 32 lower-case hex digits, the shape Console takes. The raw identifiers never leave this module.

A host that keeps neither has no fingerprint, and Console shows the operator that the licence cannot be bound.
One whose identifier cannot be read now (a file it may not read, ioreg failing) raises ``MachineIdUnreadable``
instead: an application then waits until it can be read, rather than go out as one that cannot be bound by
accident -- or bound to the device id, which the core may not read.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

#: Keeps this digest apart from any other hash of the same machine id (the core's machineDomain).
_DOMAIN = b"flocks-machine/1\x00"
_PLATFORM_UUID = re.compile(r'"IOPlatformUUID"\s*=\s*"([^"]+)"')
#: Where Linux keeps the machine id; older distributions keep only the D-Bus copy.
LINUX_MACHINE_ID_FILES = (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id"))
#: The device type Flocks hashes into its device id: each product has its own.
DEV_TYPE = "flocks"
#: What dmidecode prints for a UUID the firmware leaves all zeros or all ones: neither names a host.
_PLACEHOLDER_UUIDS = frozenset({"", "Not Settable", "Not Present"})
#: Where Linux exposes the SMBIOS tables (root only) and an AWS Xen host its identifiers (anyone).
LINUX_SMBIOS_ENTRY_POINT = Path("/sys/firmware/dmi/tables/smbios_entry_point")
LINUX_SMBIOS_TABLE = Path("/sys/firmware/dmi/tables/DMI")
LINUX_HYPERVISOR_UUID = Path("/sys/hypervisor/uuid")
LINUX_BOARD_ASSET_TAG = Path("/sys/devices/virtual/dmi/id/board_asset_tag")

#: What a fingerprint is (the core's activation.MachineKind*): the device id, or the machine-id digest.
KIND_DEVID = "devid"
KIND_MACHINE_ID = "machine-id"


class MachineIdUnreadable(RuntimeError):
    """The host keeps an identifier, and it cannot be read now; ``str()`` says why."""


def fingerprint_of(raw: str) -> str:
    """The digest of a machine id, as the core computes it; "" for none."""
    text = raw.strip().lower()
    if not text:
        return ""
    return hashlib.sha256(_DOMAIN + text.encode("utf-8")).hexdigest()[:32]


def device_id_of(source: str) -> str:
    """The device id of what it hashes (the SMBIOS UUID, or an AWS host's identifier), as the core computes
    it, in lower case: libdevid prints the same digits in upper case."""
    return hashlib.md5((DEV_TYPE + source).encode("utf-8"), usedforsecurity=False).hexdigest()


def format_smbios_uuid(raw: bytes, major: int, minor: int) -> str:
    """A 16-byte SMBIOS UUID as dmidecode prints it and libdevid hashes it: all ones "Not Present", all zeros
    "Not Settable"; from SMBIOS 2.6 the first three fields little-endian; lower-case hex in 8-4-4-4-12 groups.
    "" for too few."""
    if len(raw) < 16:
        return ""
    data = bytearray(raw[:16])
    if data == b"\xff" * 16:
        return "Not Present"
    if data == b"\x00" * 16:
        return "Not Settable"
    if major > 2 or (major == 2 and minor >= 6):
        data[0:4] = data[0:4][::-1]
        data[4:6] = data[4:6][::-1]
        data[6:8] = data[6:8][::-1]
    text = data.hex()
    return f"{text[0:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:32]}"


def smbios_system_uuid(table: bytes, major: int, minor: int) -> str:
    """The UUID of the System Information structure (type 1) in a raw SMBIOS structure table, as dmidecode
    prints it; "" when there is none. Firmware's table is read defensively: a structure claiming more than
    is there ends the walk."""
    uuid, p = "", 0
    while p + 4 <= len(table):
        kind, length = table[p], table[p + 1]
        if length < 4 or p + length > len(table):
            break
        # The UUID came with SMBIOS 2.1, which made the structure 0x19 long.
        if kind == 1 and length >= 0x19:
            uuid = format_smbios_uuid(table[p + 8 : p + 24], major, minor)
        if kind == 127:  # end of table
            break
        # The formatted area is followed by the structure's strings, which end with two NULs.
        nxt = p + length
        while nxt + 1 < len(table) and (table[nxt] != 0 or table[nxt + 1] != 0):
            nxt += 1
        p = nxt + 2
    return uuid


def _first_line(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0].strip()
    except OSError:
        return ""


def _linux_device_id() -> str:
    """This Linux host's device id, "" where it cannot be read as the host's own: without root the SMBIOS
    table is closed, and a UUID the firmware leaves unset names no host."""
    hypervisor = _first_line(LINUX_HYPERVISOR_UUID)
    if hypervisor[:3].lower() == "ec2":
        return device_id_of(_first_line(LINUX_BOARD_ASSET_TAG) or hypervisor)
    major, minor = 2, 6
    try:
        entry = LINUX_SMBIOS_ENTRY_POINT.read_bytes()
    except OSError:
        entry = b""
    if len(entry) >= 9 and entry[:5] == b"_SM3_":
        major, minor = entry[7], entry[8]
    elif len(entry) >= 8 and entry[:4] == b"_SM_":
        major, minor = entry[6], entry[7]
    try:
        table = LINUX_SMBIOS_TABLE.read_bytes()
    except OSError:
        return ""
    uuid = smbios_system_uuid(table, major, minor)
    return "" if uuid in _PLACEHOLDER_UUIDS else device_id_of(uuid)


def _linux_machine_id() -> str:
    for path in LINUX_MACHINE_ID_FILES:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise MachineIdUnreadable(f"读不了 {path}：{exc}") from exc
        except UnicodeDecodeError as exc:
            # A damaged file names no host this module can digest as the core does: it cannot be read.
            raise MachineIdUnreadable(f"{path} 的内容不是文本，机器标识读不出来：{exc}") from exc
        if text:
            return text
    return ""


def _platform_uuid() -> str:
    try:
        # The absolute path keeps a PATH entry from standing in for it, as in the core.
        result = subprocess.run(
            ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MachineIdUnreadable(f"ioreg 没有运行成功：{exc}") from exc
    match = _PLATFORM_UUID.search(result.stdout or "")
    if result.returncode != 0 or match is None:
        # Every Mac has a platform UUID: not finding it is a failure, not a host without one.
        raise MachineIdUnreadable(f"ioreg 没有给出 IOPlatformUUID（退出码 {result.returncode}）")
    return match.group(1)


class MachineNames(NamedTuple):
    """This host as an application names it: the fingerprint a licence is bound to and which one it is
    (``KIND_MACHINE_ID`` or ``KIND_DEVID``), and the device id beside it where it can be read ("" where not)."""

    fingerprint: str
    kind: str
    devid: str


def _device_id_of_platform_uuid(uuid: str) -> str:
    """A Mac's device id: its platform UUID stands in for the SMBIOS one, as in the core, in the lower case
    libdevid hashes a UUID in (ioreg prints it in upper); "" for a placeholder."""
    return "" if uuid.strip() in _PLACEHOLDER_UUIDS else device_id_of(uuid.lower())


def machine_names() -> MachineNames:
    """This host's fingerprint for a licence, what it is, and its device id: the machine-id digest wherever the
    host keeps an identifier, else the device id where it can be read; ("", "", "") where neither. Raises
    ``MachineIdUnreadable`` when the host keeps an identifier that cannot be read now. Blocking (ioreg runs up
    to 10 seconds): call it off the event loop."""
    if sys.platform.startswith("linux"):
        device = _linux_device_id()
        digest = fingerprint_of(_linux_machine_id())
        if digest:
            return MachineNames(digest, KIND_MACHINE_ID, device)
        return MachineNames(device, KIND_DEVID, device) if device else MachineNames("", "", "")
    if sys.platform == "darwin":
        uuid = _platform_uuid()
        return MachineNames(fingerprint_of(uuid), KIND_MACHINE_ID, _device_id_of_platform_uuid(uuid))
    # Windows and the rest: applications from here go out unbound, as they always have.
    return MachineNames("", "", "")


def machine_identity() -> tuple[str, str]:
    """This host's fingerprint for a licence and what it is (``machine_names``)."""
    names = machine_names()
    return names.fingerprint, names.kind


def machine_fingerprints() -> set[str]:
    """Every fingerprint this host goes by that can be read now -- its device id and the digest of its
    identifier -- as the core holds a licence bound to either to this host; empty where none can be read.
    Never raises: what cannot be read is left out. Blocking, as ``machine_identity`` is."""
    found: set[str] = set()
    if sys.platform.startswith("linux"):
        found.add(_linux_device_id())
        try:
            found.add(fingerprint_of(_linux_machine_id()))
        except MachineIdUnreadable:
            pass
    elif sys.platform == "darwin":
        try:
            uuid = _platform_uuid()
        except MachineIdUnreadable:
            uuid = ""
        if uuid.strip():
            found.add(_device_id_of_platform_uuid(uuid))
        found.add(fingerprint_of(uuid))
    return {item for item in found if item}


def machine_fingerprint() -> str:
    """This host's fingerprint (``machine_identity``); "" where it keeps no identifier."""
    return machine_identity()[0]

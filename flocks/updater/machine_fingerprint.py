"""
This machine, as the enterprise core names it.

The enterprise core (flockspro ``go/internal/activation/machine.go``) identifies the host by a digest
of the identifier the operating system keeps for it, and holds a licence Console bound to that digest
to this host. An application for a licence sends the same digest, so Console can bind the licence it
issues to the machine the application came from: a copy of the licence on another machine is then no
licence there. The raw identifier never leaves this module; Console sees only the digest.

A host that keeps no identifier (a container without /etc/machine-id) has no digest, and Console shows
the operator that the licence cannot be bound. One whose identifier cannot be read now (a file it may
not read, ioreg failing) raises ``MachineIdUnreadable`` instead: an application then waits until it
can be read, rather than go out as one that cannot be bound by accident.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

#: Keeps this digest apart from any other hash of the same machine id (the core's machineDomain).
_DOMAIN = b"flocks-machine/1\x00"
_PLATFORM_UUID = re.compile(r'"IOPlatformUUID"\s*=\s*"([^"]+)"')
#: Where Linux keeps the machine id; older distributions keep only the D-Bus copy.
LINUX_MACHINE_ID_FILES = (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id"))


class MachineIdUnreadable(RuntimeError):
    """The host keeps an identifier, and it cannot be read now; ``str()`` says why."""


def _machine_id() -> str:
    if sys.platform.startswith("linux"):
        for path in LINUX_MACHINE_ID_FILES:
            try:
                text = path.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise MachineIdUnreadable(f"读不了 {path}：{exc}") from exc
            if text:
                return text
        return ""
    if sys.platform == "darwin":
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
    # The enterprise edition runs on Linux and macOS only.
    return ""


def fingerprint_of(raw: str) -> str:
    """The digest of a machine id, as the core computes it; "" for none."""
    text = raw.strip().lower()
    if not text:
        return ""
    return hashlib.sha256(_DOMAIN + text.encode("utf-8")).hexdigest()[:32]


def machine_fingerprint() -> str:
    """This host's digest; "" where it keeps no identifier (a container without /etc/machine-id).
    Raises ``MachineIdUnreadable`` when it keeps one that cannot be read now. Blocking (ioreg runs up
    to 10 seconds): call it off the event loop."""
    return fingerprint_of(_machine_id())

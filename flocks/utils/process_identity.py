"""Process birth identity for detecting PID reuse across local workers."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Any


def process_identity(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    if sys.platform.startswith("linux"):
        try:
            stat_text = Path(f"/proc/{value}/stat").read_text(encoding="utf-8")
            start_ticks = stat_text.rsplit(")", 1)[1].split()[19]
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        except (IndexError, OSError):
            return None
        return f"linux:{boot_id}:{start_ticks}"
    if sys.platform == "win32":
        return _windows_process_identity(value)
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(value)],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    started_at = " ".join(result.stdout.split())
    return f"posix:{started_at}" if result.returncode == 0 and started_at else None


def _windows_process_identity(pid: int) -> str | None:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        try:
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
        finally:
            kernel32.CloseHandle(handle)
        created = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return f"windows:{created}"
    except (AttributeError, OSError):
        return None

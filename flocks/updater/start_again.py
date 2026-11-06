"""After a failed installer: the service that ran before it runs again.

The installer starts it again itself. This is for when it could not: killed, out of memory, the
machine short of something halfway. Run from the shell that ran the installer, which outlives the
server, with this file's path and no flocks package behind it (``python -I start_again.py plan``):
whatever else is broken, the standard library is there.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

_WAIT_SECONDS = 5


def _answers(host: str, port: int) -> bool:
    host = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        plan = json.loads(Path(args[0]).read_text(encoding="utf-8"))
        host, port, command = plan["host"], int(plan["port"]), list(plan["argv"])
    except (IndexError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"start_again: no plan to start the service from: {exc}", flush=True)
        return 1
    # Something answering there is the service the installer put back, or the installer's own.
    for _ in range(_WAIT_SECONDS):
        if _answers(host, port):
            print(f"start_again: {host}:{port} answers, nothing to do", flush=True)
            return 0
        time.sleep(1)
    print(f"start_again: nothing answers on {host}:{port}: {' '.join(command)}", flush=True)
    try:
        return subprocess.run(command, cwd=plan.get("cwd") or None, check=False, stdin=subprocess.DEVNULL).returncode
    except OSError as exc:
        print(f"start_again: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

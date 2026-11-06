"""After a failed installer: the service that ran before it runs again.

The installer starts it again itself. This is for when it could not: killed, out of memory, the
machine short of something halfway. Run from the shell that ran the installer, which outlives the
server, with this file's path and no flocks package behind it (``python -I start_again.py plan``):
whatever else is broken, the standard library is there.
"""

from __future__ import annotations

import http.client
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

_WAIT_SECONDS = 5


def _flocks_answers(host: str, port: int) -> bool:
    """A Flocks service there, the way a page tells: anything can hold the port."""
    host = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://{host}:{port}/api/health", timeout=3) as response:
            body = json.loads(response.read(65536))
    except (OSError, ValueError, http.client.HTTPException):
        return False
    # Older versions say healthy, newer ones status.
    return isinstance(body, dict) and (body.get("healthy") is True or body.get("status") == "healthy")


def _installer_kept_the_machine(plan: dict) -> bool:
    """This run's record says retained: the machine stays as the installer left it, and the record
    says why. Not this file's to undo."""
    try:
        record = json.loads(Path(plan["result_file"]).read_text(encoding="utf-8"))
    except (KeyError, TypeError, OSError, ValueError):
        return False
    return isinstance(record, dict) and record.get("id") != plan.get("prior_result") and record.get("recovery") == "retained"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        plan = json.loads(Path(args[0]).read_text(encoding="utf-8"))
        host, port, command = plan["host"], int(plan["port"]), list(plan["argv"])
    except (IndexError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"start_again: no plan to start the service from: {exc}", flush=True)
        return 1
    if _installer_kept_the_machine(plan):
        print("start_again: the installer kept the machine as it is, nothing to do", flush=True)
        return 0
    # A service answering there is the one the installer put back, or the installer's own.
    for _ in range(_WAIT_SECONDS):
        if _flocks_answers(host, port):
            print(f"start_again: flocks answers on {host}:{port}, nothing to do", flush=True)
            return 0
        time.sleep(1)
    print(f"start_again: no flocks answers on {host}:{port}: {' '.join(command)}", flush=True)
    try:
        code = subprocess.run(command, cwd=plan.get("cwd") or None, check=False, stdin=subprocess.DEVNULL).returncode
    except OSError as exc:
        print(f"start_again: {exc}", flush=True)
        return 1
    print(f"start_again: exit {code}, flocks answers: {_flocks_answers(host, port)}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())

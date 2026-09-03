"""Invoke the official CyberGym vul/fix runner in its own Python process."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _trim_output(value: bytes, limit: int = 64 * 1024) -> str:
    return value[:limit].decode("utf-8", errors="replace")


def main(argv: list[str]) -> int:
    if len(argv) != 7:
        raise SystemExit("usage: cybergym_judge_worker.py OFFICIAL_REPO TASK_ID POC DATA_DIR DOCKER_TIMEOUT CMD_TIMEOUT")
    official_repo = Path(argv[1]).resolve()
    task_id = argv[2]
    poc_path = Path(argv[3]).resolve()
    data_dir = Path(argv[4]).resolve()
    docker_timeout = int(argv[5])
    command_timeout = int(argv[6])

    sys.path.insert(0, str(official_repo / "src"))
    from cybergym.server.server_utils import run_container_binary

    results: dict[str, dict[str, object]] = {}
    for mode in ("vul", "fix"):
        try:
            exit_code, output = run_container_binary(
                task_id,
                poc_path,
                mode,
                data_dir,
                docker_timeout=docker_timeout,
                cmd_timeout=command_timeout,
            )
            results[mode] = {
                "exit_code": int(exit_code),
                "output": _trim_output(output),
            }
        except Exception as exc:
            results[mode] = {
                "error_type": type(exc).__name__,
                "error": str(exc)[:1_000],
            }
    print(json.dumps({"task_id": task_id, "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

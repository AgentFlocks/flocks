"""Run the workflow reliability regression on Windows, macOS or Linux.

From a Python 3.12 source checkout with development dependencies installed:
    python scripts/check_workflow_reliability.py

For an isolated test broker (creates and deletes only UUID-named test topics):
    python scripts/check_workflow_reliability.py --kafka-broker 127.0.0.1:19092

Results default to ~/.flocks/workspace/outputs/<execution-date>/.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile
import uuid


TEST_PATHS = [
    "tests/workflow", "tests/ingest", "tests/storage",
    "tests/tool/test_channel_message.py",
    "tests/server/routes/test_workflow_run_route.py",
    "tests/server/routes/test_workflow_poller_routes.py",
    "tests/server/routes/test_workflow_summary_routes.py",
    "tests/server/routes/test_workflow_publish_api.py",
    "tests/server/routes/test_workflow_trigger_routes.py",
    "tests/server/routes/test_workflow_health_monitor.py",
    "tests/server/routes/test_workflow_routes_visibility.py",
    "tests/server/routes/test_channel_routes.py",
    "tests/server/routes/test_channel_security_lifecycle.py",
]

SOURCE_FILES = [
    "flocks/ingest/kafka/manager.py", "flocks/workflow/store.py",
    "flocks/workflow/poller_manager.py", "flocks/workflow/validation.py",
    "flocks/workflow/notification_delivery.py", "flocks/workflow/health_notifications.py",
    "flocks/workflow/runner.py", "flocks/workflow/workflow_lint.py",
    "flocks/server/routes/workflow.py", "uv.lock",
    ".flocks/plugins/workflows/tdp_alert_triage/workflow.json",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--kafka-broker", help="Dedicated disposable test broker only")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        print("This dev branch requires Python 3.12 (>=3.12,<3.13). "
              "Run this script with the checkout's Python 3.12 environment.", file=sys.stderr)
        return 2
    source = Path(__file__).resolve().parents[1]
    now = datetime.now()
    destination = args.output_dir or (
        Path.home() / ".flocks" / "workspace" / "outputs" / now.date().isoformat()
        / f"workflow-reliability-{now:%H%M%S}-{uuid.uuid4().hex[:8]}"
    )
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    versions = {}
    for name in ("pytest", "pytest-asyncio", "aiokafka", "aiosqlite"):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            print(f"Missing {name}. Install the checkout's locked development dependencies first.", file=sys.stderr)
            return 2
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=source, text=True,
            capture_output=True, timeout=10, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=source, text=True,
            capture_output=True, timeout=10, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        revision, dirty = "unavailable (source archive)", "unavailable"
    environment = {
        "started_at": now.isoformat(), "source": str(source), "revision": revision,
        "working_tree_changes": dirty, "platform": platform.platform(),
        "python": sys.version, "python_executable": sys.executable,
        "sqlite": sqlite3.sqlite_version, "dependencies": versions,
        "real_kafka_requested": bool(args.kafka_broker),
        "source_sha256": {
            name: hashlib.sha256((source / name).read_bytes()).hexdigest()
            for name in SOURCE_FILES
        },
    }
    command = [sys.executable, "-m", "pytest", *TEST_PATHS, "-q", "--tb=short",
               f"--junitxml={destination / 'junit.xml'}"]
    # Offline regression uses disposable state; the user's running Flocks is not
    # started/stopped and its live config/database are not used by this runner.
    with tempfile.TemporaryDirectory(prefix="flocks-reliability-") as temporary:
        root = Path(temporary)
        env = os.environ.copy()
        env.update({
            "FLOCKS_ROOT": str(root), "FLOCKS_SHARED_ROOT": str(root),
            "FLOCKS_CONFIG_DIR": str(root / "config"),
            "FLOCKS_DATA_DIR": str(root / "data"),
            "FLOCKS_WORKSPACE_DIR": str(root / "workspace"),
            "FLOCKS_CONFIG_CONTENT": "{}", "PYTHONUTF8": "1",
        })
        env.pop("FLOCKS_KAFKA_TEST_BROKER", None)
        if args.kafka_broker:
            env["FLOCKS_KAFKA_TEST_BROKER"] = args.kafka_broker
        print(f"Results: {destination}", flush=True)
        with (destination / "pytest.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
    environment["finished_at"] = datetime.now().isoformat()
    environment["exit_code"] = result.returncode
    (destination / "environment.json").write_text(
        json.dumps(environment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print((destination / "pytest.log").read_text(encoding="utf-8", errors="replace")[-6000:])
    print(f"RESULT_PATH={destination}")
    print(f"EXIT_CODE={result.returncode}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

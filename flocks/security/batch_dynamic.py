"""Bound local dynamic containers across isolated batch worker processes."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
import os
import re
import shutil
import tempfile
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from flocks.security.batch import atomic_json, file_lock, read_json

LABEL = "flocks.batch.task"


def load_manifest(path: Path) -> dict:
    from flocks.cli.commands.security import _read_cybergym_manifest

    try:
        from flocks_code_security.cybergym_runtime import CyberGymTargetManifest
    except ModuleNotFoundError as exc:
        if exc.name != "flocks_code_security":
            raise
        source = str(Path(__file__).resolve().parents[2] / ".flocks/plugins/flocks-code-security/src")
        if source not in sys.path:
            sys.path.insert(0, source)
        from flocks_code_security.cybergym_runtime import CyberGymTargetManifest
    manifest = CyberGymTargetManifest.from_dict(_read_cybergym_manifest(path))
    if not manifest.gdb_supported or not manifest.fuzzer_supported:
        raise ValueError(f"{path.name} must enable gdb_supported and fuzzer_supported for dynamic batch audits")
    return manifest.public_dict()


def docker(*args: str) -> str:
    try:
        result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Docker is unavailable: {exc}") from exc
    if result.returncode:
        raise RuntimeError(f"Docker {' '.join(args[:2])}: {result.stderr.strip()[:1000]}")
    return result.stdout.strip()


def preflight(manifests: list[dict]) -> None:
    docker("info", "--format", "{{.ServerVersion}}")
    for image in sorted({item["vulnerable_runner"] for item in manifests}):
        docker("image", "inspect", image)


def owner(task_dir: Path) -> str:
    config = read_json(task_dir.parents[1] / "batch.json")
    return f"{config['batch_id']}:{task_dir.name}"


def remove_containers(task_owner: str, name: str | None = None) -> None:
    args = ["ps", "--all", "--quiet", "--filter", f"label={LABEL}={task_owner}"]
    if name:
        args += ["--filter", f"name=^/{re.escape(name)}$"]
    for container in docker(*args).splitlines():
        # A normally exiting --rm container can disappear between listing and removal.
        result = subprocess.run(["docker", "rm", "--force", container], capture_output=True, text=True, timeout=20)
        if result.returncode and "No such container" not in result.stderr:
            raise RuntimeError(f"Container cleanup failed: {result.stderr.strip()[:1000]}")


async def _remove(task_owner: str, name: str) -> None:
    operation = asyncio.create_task(asyncio.to_thread(remove_containers, task_owner, name))
    try:
        await asyncio.shield(operation)
    except asyncio.CancelledError:
        await operation
        raise


def scratch_root() -> str | None:
    task = os.environ.get("FLOCKS_CODE_SECURITY_BATCH_TASK")
    if not task:
        return None
    root = Path(task) / "data/code-security/runtime/dynamic"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return str(root)


@contextmanager
def scratch_directory(*, prefix: str):
    root = scratch_root()
    if root is None:
        with tempfile.TemporaryDirectory(prefix=prefix) as temporary:
            yield temporary
        return
    temporary = tempfile.mkdtemp(prefix=prefix, dir=root)
    # On failure leave inputs with the task until its container cleanup succeeds.
    # In particular, do not unmount/delete a fuzz corpus while Docker is unavailable.
    yield temporary
    shutil.rmtree(temporary)


def docker_container_name(command: list[str]) -> str | None:
    """Read Docker options only, stopping at the image before target arguments."""
    if command[:2] != ["docker", "run"]:
        return None
    switches = {
        "--rm",
        "--read-only",
        "--init",
        "--privileged",
        "--interactive",
        "--tty",
        "--detach",
        "-i",
        "-t",
        "-it",
        "-d",
    }
    index = 2
    while index < len(command):
        option = command[index]
        if option == "--" or not option.startswith("-"):
            return None
        if option.startswith("--name="):
            return option.partition("=")[2] or None
        if option == "--name":
            return command[index + 1] if index + 1 < len(command) else None
        index += 1 if option in switches or "=" in option else 2
    return None


@asynccontextmanager
async def container_slot(command: list[str]):
    task = os.environ.get("FLOCKS_CODE_SECURITY_BATCH_TASK")
    if not task or command[:2] != ["docker", "run"]:
        yield command
        return
    task_dir = Path(task)
    root = task_dir.parents[1]
    config = read_json(root / "batch.json")
    task_owner = owner(task_dir)
    existing_name = docker_container_name(command)
    name = existing_name or f"flocks-batch-{uuid4().hex}"
    tagged = command[:2] + ["--label", f"{LABEL}={task_owner}"]
    if existing_name is None:
        tagged += ["--name", name]
    tagged += command[2:]
    while True:
        for index in range(config["dynamic_concurrency"]):
            slot = root / "dynamic-slots" / str(index)
            try:
                lock = file_lock(slot.with_suffix(".lock"))
                lock.__enter__()
            except BlockingIOError:
                continue
            try:
                if slot.exists():
                    stale = read_json(slot)
                    await _remove(stale["owner"], stale["name"])
                atomic_json(slot, {"owner": task_owner, "name": name})
                try:
                    yield tagged
                finally:
                    # Keep the slot occupied until Docker confirms removal; retain its
                    # lease on failure so a subsequent worker retries cleanup first.
                    await _remove(task_owner, name)
                    slot.unlink(missing_ok=True)
                return
            finally:
                lock.__exit__(None, None, None)
        await asyncio.sleep(0.2)

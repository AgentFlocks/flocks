"""If the old updater fails while installing the transition package, put the old version back.

Started by flocks_transition_build.py while the old updater runs ``uv sync``. Standard library only,
and it touches nothing unless an old updater's handoff is above it and ends in failure, so a ``uv
sync`` by hand does nothing here. Everything it needs it reads from that handoff's arguments before it
ends: where the source is, the backup the old updater made of it, how the service was started.

What goes back: the source from the backup, the version records as they were at this point, the
service, and the interface as far as this machine can build it. The old updater deleted the old one
and did not back it up, so the old version starts with the transition package's, which is built
already, and its own is built from the source that came back, the service running meanwhile. Before
``uv sync`` the old updater runs nothing of ours: a failure to stop the service or to replace the
source is not covered here.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

HANDOFF = "flocks.updater.restart_handoff"
POLL_SECONDS = 1.0
START_ANSWER_SECONDS = 120
INTERFACE_BUILD_SECONDS = 900


def say(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} transition-watchdog: {message}", flush=True)


def _ps(pid: int, field: str) -> str:
    done = subprocess.run(["ps", "-o", f"{field}=", "-p", str(pid)], capture_output=True, text=True, check=False)
    return done.stdout.strip()


def _arguments(pid: int) -> list[str]:
    """A process's arguments, whole on Linux; split at spaces where only ps can tell (macOS)."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return _ps(pid, "args").split()
    return [part.decode(errors="replace") for part in raw.split(b"\0") if part]


def find_handoff(start: int) -> tuple[int, list[str]] | None:
    """The old updater's handoff among this process's ancestors."""
    pid = start
    for _ in range(8):
        if pid <= 1:
            return None
        arguments = _arguments(pid)
        if HANDOFF in arguments or any(HANDOFF in part for part in arguments[:4]):
            return pid, arguments
        try:
            pid = int(_ps(pid, "ppid"))
        except ValueError:
            return None
    return None


def option(arguments: list[str], name: str) -> str | None:
    if name in arguments:
        index = arguments.index(name)
        if index + 1 < len(arguments):
            return arguments[index + 1]
    return None


def flocks_root() -> Path:
    return Path(os.environ.get("FLOCKS_ROOT") or Path.home() / ".flocks")


def read_result(root: Path) -> dict:
    try:
        value = json.loads((root / "run" / "upgrade-result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie is gone too: nobody may be waiting for it now that uv has finished.
    return _ps(pid, "stat")[:1] not in ("", "Z")


def records(root: Path) -> dict[str, bytes | None]:
    """The version records as they are before the old updater writes the new ones."""
    kept: dict[str, bytes | None] = {}
    for path in (root / "version" / ".current_version", root / "run" / "pro-bundle-installed.json"):
        try:
            kept[str(path)] = path.read_bytes()
        except OSError:
            kept[str(path)] = None
    return kept


def restore_records(kept: dict[str, bytes | None]) -> None:
    for name, data in kept.items():
        path = Path(name)
        if data is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


def restore_source(backup: Path, install_root: Path) -> None:
    """The backup is the source under flocks/: it goes over what is there. What only the new source
    has stays: nothing old imports it, and webui/dist is the interface there is."""
    with tarfile.open(backup, "r:gz") as archive:
        for member in archive.getmembers():
            name = member.name.split("/", 1)[1] if "/" in member.name else ""
            if not name or member.isdev() or ".." in Path(name).parts:
                continue
            member.name = name
            target = install_root / name
            if target.is_symlink() or (target.exists() and not target.is_dir() and not member.isdir()):
                target.unlink()
            archive.extract(member, install_root, filter="tar")


def service_environment() -> dict[str, str]:
    """This process's environment as the handoff had it: what uv puts in front for a build goes."""
    env = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "UV_")) and key != "VIRTUAL_ENV"}
    env["PATH"] = os.pathsep.join(part for part in env.get("PATH", "").split(os.pathsep) if "/builds" not in part and "/uv/" not in part)
    return env


def answers(host: str, port: int) -> bool:
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


def stop_what_started(python: str, install_root: Path) -> None:
    """A service the old updater started and gave up on may still be coming up, on the source about
    to be replaced."""
    try:
        done = subprocess.run(
            [python, "-m", "flocks.cli.main", "stop"], cwd=install_root, env=service_environment(),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90, check=False,
        )
        say(f"stop exited {done.returncode}")
    except (OSError, subprocess.SubprocessError) as exc:
        say(f"stop: {exc}")


def start_service(python: str, install_root: Path, host: str, port: int) -> bool:
    command = [python, "-m", "flocks.cli.main", "start", "--host", host, "--port", str(port), "--no-browser", "--skip-webui-build"]
    say(f"starting the service: {' '.join(command)}")
    done = subprocess.run(command, cwd=install_root, env=service_environment(), stdin=subprocess.DEVNULL, check=False)
    if done.returncode == 2:
        # A version that does not know the option: it finds the interface there and leaves it.
        done = subprocess.run(command[:-1], cwd=install_root, env=service_environment(), stdin=subprocess.DEVNULL, check=False)
    say(f"start exited {done.returncode}")
    deadline = time.monotonic() + START_ANSWER_SECONDS
    while time.monotonic() < deadline:
        if answers(host, port):
            return True
        time.sleep(2)
    return False


def restore_component(python: str, arguments: list[str], install_root: Path) -> None:
    """``uv sync`` takes out what the lock does not name, the enterprise wheel among it, and the old
    updater puts it back only after: a failure in between leaves it out."""
    wheel, uv = option(arguments, "--pro-wheel-path"), option(arguments, "--uv-path")
    if not wheel or not uv or not Path(wheel).is_file():
        return
    probe = subprocess.run([python, "-c", "import flockspro"], cwd=install_root, env=service_environment(), capture_output=True, check=False)
    if probe.returncode == 0:
        return
    done = subprocess.run([uv, "pip", "install", "--python", python, "--no-deps", wheel], cwd=install_root, env=service_environment(), capture_output=True, text=True, check=False)
    say(f"enterprise component put back: exit {done.returncode} {done.stderr[-300:]}")


def go_back(arguments: list[str], root: Path, kept: dict[str, bytes | None]) -> bool:
    install_root = Path(option(arguments, "--install-root") or "")
    backup = Path(option(arguments, "--backup-path") or "")
    host = option(arguments, "--frontend-host") or option(arguments, "--backend-host") or "127.0.0.1"
    port = int(option(arguments, "--frontend-port") or option(arguments, "--backend-port") or 5173)
    python = arguments[0]
    if not install_root.is_dir():
        say(f"no install root in the handoff's arguments: {install_root}")
        return False
    if answers(host, port):
        # The old updater started it and was stopped before its last word: it runs, on this source.
        say(f"flocks answers on {host}:{port}: leaving everything as it is")
        return True
    stop_what_started(python, install_root)
    if backup.is_file():
        restore_source(backup, install_root)
        say(f"source restored from {backup}")
    else:
        say(f"no backup at {backup}: starting what is there")
    restore_records(kept)
    restore_component(python, arguments, install_root)
    return start_service(python, install_root, host, port)


def find_npm(install_root: Path) -> str | None:
    """Where an old updater looks (the node it installed, then the PATH), and nvm's for a service
    started without it. This process's PATH is what uv left of it."""
    paths = os.environ.get("PATH", "").split(os.pathsep)
    for tools in (flocks_root() / "tools" / "node", install_root.parent / "tools" / "node"):
        paths += [str(tools / "bin"), str(tools)]
    paths += sorted((str(path) for path in Path.home().glob(".nvm/versions/node/*/bin")), reverse=True)
    paths += ["/usr/local/bin", "/opt/homebrew/bin"]
    return shutil.which("npm", path=os.pathsep.join(paths))


def _interface_step(command: list[str], web: Path, env: dict[str, str], root: Path, phase: object) -> bool | None:
    """One npm command: whether it worked, None when another upgrade has begun and the tree is its now."""
    process = subprocess.Popen(command, cwd=web, env=env, stdin=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + INTERFACE_BUILD_SECONDS
    while process.poll() is None:
        begun = read_result(root).get("phase") != phase
        if begun or time.monotonic() > deadline:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait()
            return None if begun else False
        time.sleep(POLL_SECONDS)
    return process.returncode == 0


def rebuild_interface(install_root: Path, root: Path) -> None:
    """The old version's own interface again, from the source that came back, where this machine has
    what it takes (the old updater leaves webui/node_modules). Whatever goes wrong the transition
    package's stays: the old version runs with it, and the page offers the upgrade again."""
    web = install_root / "webui"
    npm = find_npm(install_root)
    if not (web / "package.json").is_file() or npm is None:
        say(f"the old interface cannot be built here (npm: {npm}): the transition package's stays")
        return
    phase = read_result(root).get("phase")
    dist, kept = web / "dist", web / "dist.transition"
    shutil.rmtree(kept, ignore_errors=True)
    if dist.is_dir():
        shutil.copytree(dist, kept)
    env = service_environment()
    env["PATH"] = str(Path(npm).parent) + os.pathsep + env.get("PATH", "")
    steps = [[npm, "run", "build"]]
    if not (web / "node_modules").is_dir():
        steps.insert(0, [npm, "ci", "--no-audit", "--no-fund"])
    say(f"building the old interface: {' '.join(steps[-1])}")
    built = True
    for step in steps:
        built = _interface_step(step, web, env, root, phase)
        if built is None:
            say("another upgrade has begun: leaving the interface to it")
            return
        if not built:
            break
    if built and (dist / "index.html").is_file():
        shutil.rmtree(kept, ignore_errors=True)
        say("the old interface is built again")
    elif kept.is_dir():
        shutil.rmtree(dist, ignore_errors=True)
        kept.rename(dist)
        say("the old interface did not build: the transition package's stays")


def mark(root: Path, recovered: bool) -> None:
    result = read_result(root)
    if not result:
        return
    result["recovered_by_watchdog"] = recovered
    path = root / "run" / "upgrade-result.json"
    temporary = path.with_suffix(".watchdog")
    temporary.write_text(json.dumps(result), encoding="utf-8")
    temporary.replace(path)


def only_one(root: Path):
    """uv runs each step of a build in a process of its own, and each starts one of these: the first
    one holds this as long as it runs, the others have nothing to do."""
    import fcntl

    path = root / "run" / "transition-watchdog.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    held = path.open("ab")
    try:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        held.close()
        return None
    return held


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    start = int(args[0]) if args else os.getppid()
    found = find_handoff(start)
    if found is None:
        return 0
    handoff, arguments = found
    root = flocks_root()
    held = only_one(root)
    if held is None:
        return 0
    kept = records(root)
    say(f"watching handoff {handoff}")
    while alive(handoff):
        time.sleep(POLL_SECONDS)
    # Its last word is written before it exits.
    result = read_result(root)
    phase, stage = result.get("phase"), result.get("failed_stage")
    if phase == "done":
        say("the upgrade finished")
        return 0
    say(f"the upgrade ended {phase} at {stage}: going back")
    try:
        recovered = go_back(arguments, root, kept)
    except Exception as exc:  # noqa: BLE001 - said, and there is nothing further up
        say(f"going back failed: {exc}")
        recovered = False
    say("the service is running again" if recovered else "the service did not come back")
    mark(root, recovered)
    # What is left is no reason to stop the next attempt being watched.
    held.close()
    install_root = option(arguments, "--install-root")
    if recovered and install_root:
        try:
            rebuild_interface(Path(install_root), root)
        except Exception as exc:  # noqa: BLE001 - said; the service runs
            say(f"building the old interface failed: {exc}")
    return 0 if recovered else 1


if __name__ == "__main__":
    sys.exit(main())

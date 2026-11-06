"""Build backend of the transition package's source tree (make_core.sh puts it there).

An old enterprise edition upgrades to the transition package with the updater it already has: it
stops the service, replaces the source and runs ``uv sync`` in it, and if that fails it leaves the
service stopped. That code is on the customer's machine and cannot be changed. This file is what can:
``uv sync`` asks the project's build backend for the root package, and that is the one moment the old
updater runs anything from the new source.

- No network: hatchling had to be fetched for this (uv revalidates it even when it is cached), which
  is the only thing a machine since 2026.7.23 is missing: uv.lock has not changed since. Standard
  library only, ``requires = []``.
- It starts flocks_transition_watchdog.py, which stays behind and puts the old version back if the
  old updater ends in failure.
"""

from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WATCHDOG = ROOT / "flocks_transition_watchdog.py"


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _version(project: dict) -> str:
    return str(project["version"]).lstrip("v")


def _dist_info(project: dict) -> str:
    return f"{project['name']}-{_version(project)}.dist-info"


def _metadata(project: dict) -> str:
    lines = ["Metadata-Version: 2.1", f"Name: {project['name']}", f"Version: {_version(project)}"]
    if project.get("requires-python"):
        lines.append(f"Requires-Python: {project['requires-python']}")
    lines += [f"Requires-Dist: {requirement}" for requirement in project.get("dependencies", [])]
    return "\n".join(lines) + "\n"


def _entry_points(project: dict) -> str:
    scripts = project.get("scripts") or {}
    return "[console_scripts]\n" + "".join(f"{name} = {target}\n" for name, target in scripts.items())


_WHEEL = "Wheel-Version: 1.0\nGenerator: flocks-transition\nRoot-Is-Purelib: true\nTag: py3-none-any\n"


def _arm_watchdog() -> None:
    """Never in the way of the build: without it the upgrade goes on as it would have."""
    try:
        log = Path(os.environ.get("FLOCKS_ROOT") or Path.home() / ".flocks") / "logs" / "transition-watchdog.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("ab") as out:
            subprocess.Popen(
                [sys.executable, "-I", str(WATCHDOG), str(os.getppid())],
                stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True, close_fds=True, cwd=str(ROOT),
            )
    except Exception as exc:  # noqa: BLE001 - see the docstring
        print(f"flocks-transition: no watchdog: {exc}", file=sys.stderr)


_armed = False


def _arm_once() -> None:
    global _armed
    if not _armed:
        _armed = True
        _arm_watchdog()


def get_requires_for_build_editable(config_settings=None) -> list[str]:
    _arm_once()
    return []


def get_requires_for_build_wheel(config_settings=None) -> list[str]:
    return []


def _write_metadata(directory: str, project: dict) -> str:
    info = Path(directory) / _dist_info(project)
    info.mkdir(parents=True, exist_ok=True)
    (info / "METADATA").write_text(_metadata(project), encoding="utf-8")
    (info / "WHEEL").write_text(_WHEEL, encoding="utf-8")
    (info / "entry_points.txt").write_text(_entry_points(project), encoding="utf-8")
    return info.name


def prepare_metadata_for_build_editable(metadata_directory: str, config_settings=None) -> str:
    _arm_once()
    return _write_metadata(metadata_directory, _project())


def prepare_metadata_for_build_wheel(metadata_directory: str, config_settings=None) -> str:
    return _write_metadata(metadata_directory, _project())


def _record_line(name: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{name},sha256={digest},{len(data)}"


def build_editable(wheel_directory: str, config_settings=None, metadata_directory=None) -> str:
    _arm_once()
    project = _project()
    info = _dist_info(project)
    files = {
        f"{project['name']}.pth": f"{ROOT}\n".encode(),
        f"{info}/METADATA": _metadata(project).encode(),
        f"{info}/WHEEL": _WHEEL.encode(),
        f"{info}/entry_points.txt": _entry_points(project).encode(),
    }
    record = "".join(_record_line(name, data) + "\n" for name, data in files.items()) + f"{info}/RECORD,,\n"
    filename = f"{project['name']}-{_version(project)}-py3-none-any.whl"
    with zipfile.ZipFile(Path(wheel_directory) / filename, "w", zipfile.ZIP_DEFLATED) as wheel:
        for name, data in files.items():
            wheel.writestr(name, data)
        wheel.writestr(f"{info}/RECORD", record)
    return filename


build_wheel = build_editable

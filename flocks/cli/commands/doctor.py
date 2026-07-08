"""Source-install repair command for the Flocks CLI."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console

from flocks.cli.install_profile import cn_installer_environment, is_cn_install_language

console = Console()


def doctor_command() -> None:
    """Run the source installer from the Flocks source directory."""
    source_root = _find_source_root()
    script = _select_source_install_script(source_root)
    command = _build_source_install_command(script)
    env = _build_source_install_env()

    console.print(f"[cyan]Flocks source directory:[/cyan] {source_root}")
    console.print(f"[cyan]Source install command:[/cyan] {_format_command(command)}")

    if _needs_windows_handoff():
        _start_windows_handoff(source_root, env=env)
        console.print(
            "[yellow]Windows detected: the installer will continue in this console "
            "after the current flocks.exe exits.[/yellow]"
        )
        return

    _run_source_install(command, source_root=source_root, env=env)

    console.print("[green]安装正常[/green]")
    _print_service_diagnosis()


def _find_source_root(start: Path | None = None) -> Path:
    """Find the repository root that owns the source install scripts."""
    current = (start or Path(__file__)).resolve()
    candidates = (current, *current.parents)

    for candidate in candidates:
        if candidate.is_file():
            continue
        if (candidate / "pyproject.toml").is_file() and (candidate / "scripts" / "install.sh").is_file():
            return candidate

    raise typer.BadParameter("Could not locate the Flocks source directory.")


def _select_source_install_script(source_root: Path) -> Path:
    """Select the platform-specific source install script."""
    suffix = ".ps1" if _is_windows() else ".sh"
    script = source_root / "scripts" / f"install{suffix}"

    if not script.is_file():
        raise typer.BadParameter(f"Source installer not found: {script}")

    return script


def _build_source_install_command(script: Path) -> list[str]:
    """Build the subprocess command for the selected installer."""
    if script.suffix == ".ps1":
        powershell = _find_powershell()
        return [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ]

    return ["bash", str(script)]


def _run_source_install(command: list[str], *, source_root: Path, env: dict[str, str] | None) -> None:
    """Run the selected source installer synchronously."""
    try:
        subprocess.run(command, cwd=source_root, check=True, env=env)
    except FileNotFoundError as error:
        console.print(f"[red]Failed to start installer: {error}[/red]")
        raise typer.Exit(1) from error
    except subprocess.CalledProcessError as error:
        raise typer.Exit(error.returncode or 1) from error


def _build_source_install_env() -> dict[str, str] | None:
    """Build installer environment from the persisted install language."""
    if not is_cn_install_language():
        return None
    env = os.environ.copy()
    for key, value in cn_installer_environment().items():
        if key == "FLOCKS_INSTALL_LANGUAGE":
            env[key] = value
            continue
        env.setdefault(key, value)
    return env


def _needs_windows_handoff() -> bool:
    """Return whether doctor must release the Windows console entrypoint first."""
    return _is_windows() and os.environ.get("FLOCKS_DOCTOR_WINDOWS_HANDOFF") != "1"


def _start_windows_handoff(source_root: Path, *, env: dict[str, str] | None) -> None:
    """Start a helper that waits for this process to exit before running doctor."""
    command = _build_windows_handoff_command(source_root, parent_pid=os.getpid())
    handoff_env = os.environ.copy() if env is None else env.copy()
    handoff_env["FLOCKS_DOCTOR_WINDOWS_HANDOFF"] = "1"

    try:
        subprocess.Popen(command, cwd=source_root, env=handoff_env, close_fds=True)
    except FileNotFoundError as error:
        console.print(f"[red]Failed to start installer handoff: {error}[/red]")
        raise typer.Exit(1) from error


def _build_windows_handoff_command(source_root: Path, *, parent_pid: int) -> list[str]:
    """Build a PowerShell command that reruns doctor after the current PID exits."""
    python_executable = _source_python_executable(source_root)
    helper_script = "; ".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"Wait-Process -Id {parent_pid} -ErrorAction SilentlyContinue",
            f"& {_quote_powershell_string(str(python_executable))} -m flocks.cli.main doctor",
            "exit $LASTEXITCODE",
        ]
    )
    return [
        _find_powershell(),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        helper_script,
    ]


def _source_python_executable(source_root: Path) -> Path:
    """Return the source venv Python, falling back to the current interpreter."""
    windows_python = source_root / ".venv" / "Scripts" / "python.exe"
    if windows_python.exists():
        return windows_python
    return Path(sys.executable)


def _quote_powershell_string(value: str) -> str:
    """Quote a string as a PowerShell single-quoted literal."""
    return "'" + value.replace("'", "''") + "'"


def _find_powershell() -> str:
    """Return the preferred PowerShell executable name or path."""
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell"


def _print_service_diagnosis() -> None:
    """Print a concise post-install service diagnosis."""
    try:
        from flocks.cli.service_manager import build_status_lines

        status_lines = build_status_lines()
    except Exception as error:
        console.print(f"[yellow]服务状态检查失败：{error}[/yellow]")
        console.print("[yellow]运行状态异常，请执行 `flocks restart`[/yellow]")
        return

    for line in status_lines:
        console.print(line)

    if _service_status_is_healthy(status_lines):
        console.print("[green]运行状态正常[/green]")
    else:
        console.print("[yellow]运行状态异常，请执行 `flocks restart`[/yellow]")


def _service_status_is_healthy(status_lines: list[str]) -> bool:
    """Return whether the current or legacy service status looks healthy."""
    daemon_running = any("daemon:" in line and "state=running" in line for line in status_lines)
    flocks_healthy = any("flocks:" in line and "state=healthy" in line for line in status_lines)
    if daemon_running and flocks_healthy:
        return True

    backend_running = any("后端运行中" in line for line in status_lines)
    webui_running = any("WebUI 运行中" in line for line in status_lines)
    return backend_running and webui_running


def _format_command(command: list[str]) -> str:
    """Return a shell-readable representation of the command."""
    return " ".join(shlex.quote(part) for part in command)


def _is_windows() -> bool:
    """Return whether the current platform should use PowerShell installers."""
    return sys.platform.startswith("win")

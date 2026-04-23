"""
Main CLI entry point

Provides command-line interface for Flocks
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

from flocks import __version__
from flocks.cli.commands import (
    admin_app,
    export_app,
    import_app,
    mcp_app,
    session_app,
    skill_app,
    stats_app,
    task_app,
)
from flocks.cli.commands.update import update_command
from flocks.cli.service_manager import (
    ServiceConfig,
    ServiceError,
    resolve_flocks_cli_command,
    restart_all,
    show_logs,
    show_status,
    start_all,
    stop_all,
)
from flocks.config.config import Config
from flocks.utils.log import Log, LogLevel

# Load .env file from current directory
load_dotenv()

app = typer.Typer(
    name="flocks",
    help="Flocks - AI-Native SecOps tool",
    add_completion=True,
    no_args_is_help=True,
)

# Register command groups
app.add_typer(session_app, name="session")
app.add_typer(mcp_app, name="mcp")
app.add_typer(export_app, name="export")
app.add_typer(import_app, name="import")
app.add_typer(stats_app, name="stats")
app.add_typer(task_app, name="task")
app.add_typer(skill_app, name="skills")
app.add_typer(admin_app, name="admin")

app.command(name="update")(update_command)

console = Console()


def version_callback(value: bool):
    """Print version and exit"""
    if value:
        console.print(f"Flocks version {__version__}")
        raise typer.Exit()


def logo() -> str:
    """Return ASCII logo"""
    return """
    ███████╗██╗      ██████╗  ██████╗██╗  ██╗███████╗
    ██╔════╝██║     ██╔═══██╗██╔════╝██║ ██╔╝██╔════╝
    █████╗  ██║     ██║   ██║██║     █████╔╝ ███████╗
    ██╔══╝  ██║     ██║   ██║██║     ██╔═██╗ ╚════██║
    ██║     ███████╗╚██████╔╝╚██████╗██║  ██╗███████║
    ╚═╝     ╚══════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝╚══════╝
    """


@app.callback()
def main_callback(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
    print_logs: bool = typer.Option(False, "--print-logs", help="Print logs to stderr"),
    log_level: str = typer.Option(LogLevel.INFO, "--log-level", help="Log level (DEBUG, INFO, WARN, ERROR)"),
):
    """
    Flocks - AI-powered development tool

    Flocks Python implementation
    """
    # Initialize logging
    asyncio.run(Log.init(print=print_logs, dev=False, level=log_level))

    Log.Default.info(
        "flocks.start",
        {
            "version": __version__,
            "args": sys.argv[1:],
        },
    )


def _service_config(
    no_browser: bool = False,
    skip_webui_build: bool = False,
    server_host: Optional[str] = None,
    server_port: Optional[int] = None,
    webui_host: Optional[str] = None,
    webui_port: Optional[int] = None,
) -> ServiceConfig:
    """Build service config from environment and CLI toggles."""
    global_config = Config.get_global()
    return ServiceConfig(
        backend_host=_resolve_host(
            cli_value=server_host,
            env_names=("FLOCKS_SERVER_HOST", "FLOCKS_BACKEND_HOST"),
            default=global_config.server_host,
        ),
        backend_port=_resolve_port(
            cli_value=server_port,
            env_names=("FLOCKS_SERVER_PORT", "FLOCKS_BACKEND_PORT"),
            default=global_config.server_port,
            label="server",
        ),
        frontend_host=_resolve_host(
            cli_value=webui_host,
            env_names=("FLOCKS_WEBUI_HOST", "FLOCKS_FRONTEND_HOST"),
            default="127.0.0.1",
        ),
        frontend_port=_resolve_port(
            cli_value=webui_port,
            env_names=("FLOCKS_WEBUI_PORT", "FLOCKS_FRONTEND_PORT"),
            default=5173,
            label="webui",
        ),
        no_browser=no_browser,
        skip_frontend_build=skip_webui_build,
    )


def _resolve_host(cli_value: Optional[str], env_names: tuple[str, ...], default: str) -> str:
    """Resolve a host value from CLI, environment, and default values."""
    if cli_value is not None:
        return cli_value
    for env_name in env_names:
        env_value = os.getenv(env_name)
        if env_value:
            return env_value
    return default


def _resolve_port(
    cli_value: Optional[int],
    env_names: tuple[str, ...],
    default: int,
    label: str,
) -> int:
    """Resolve a port value from CLI, environment, and default values."""
    if cli_value is not None:
        return cli_value
    for env_name in env_names:
        env_value = os.getenv(env_name)
        if not env_value:
            continue
        try:
            return int(env_value)
        except ValueError as error:
            raise ServiceError(f"{label} port from {env_name} must be an integer.") from error
    return default


def _handle_service_error(error: Exception) -> None:
    """Print service errors consistently."""
    console.print(f"[red][flocks] error: {error}[/red]")
    raise typer.Exit(1) from error


@app.command()
def start(
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open WebUI in a browser"),
    skip_webui_build: bool = typer.Option(
        False,
        "--skip-webui-build",
        help="Skip `npm run build` before starting WebUI",
    ),
    server_host: Optional[str] = typer.Option(None, "--server-host", help="Backend server host"),
    server_port: Optional[int] = typer.Option(None, "--server-port", help="Backend server port"),
    webui_host: Optional[str] = typer.Option(None, "--webui-host", help="WebUI host"),
    webui_port: Optional[int] = typer.Option(None, "--webui-port", help="WebUI port"),
):
    """
    Start backend and WebUI in daemon mode
    """
    try:
        start_all(
            _service_config(
                no_browser=no_browser,
                skip_webui_build=skip_webui_build,
                server_host=server_host,
                server_port=server_port,
                webui_host=webui_host,
                webui_port=webui_port,
            ),
            console,
        )
    except ServiceError as error:
        _handle_service_error(error)


@app.command()
def stop():
    """
    Stop backend and WebUI
    """
    try:
        stop_all(console)
    except ServiceError as error:
        _handle_service_error(error)


@app.command()
def restart(
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open WebUI in a browser"),
    skip_webui_build: bool = typer.Option(
        False,
        "--skip-webui-build",
        help="Skip `npm run build` before starting WebUI",
    ),
    server_host: Optional[str] = typer.Option(None, "--server-host", help="Backend server host"),
    server_port: Optional[int] = typer.Option(None, "--server-port", help="Backend server port"),
    webui_host: Optional[str] = typer.Option(None, "--webui-host", help="WebUI host"),
    webui_port: Optional[int] = typer.Option(None, "--webui-port", help="WebUI port"),
):
    """
    Restart backend and WebUI
    """
    try:
        restart_all(
            _service_config(
                no_browser=no_browser,
                skip_webui_build=skip_webui_build,
                server_host=server_host,
                server_port=server_port,
                webui_host=webui_host,
                webui_port=webui_port,
            ),
            console,
        )
    except ServiceError as error:
        _handle_service_error(error)


@app.command()
def status():
    """
    Show backend and WebUI status
    """
    try:
        show_status(console)
    except ServiceError as error:
        _handle_service_error(error)


@app.command()
def logs(
    backend: bool = typer.Option(False, "--backend", help="Only show backend logs"),
    webui: bool = typer.Option(False, "--webui", help="Only show WebUI logs"),
    follow: bool = typer.Option(True, "--follow/--no-follow", help="Follow logs in real time"),
    lines: int = typer.Option(50, "--lines", "-n", min=0, help="Number of recent lines to show"),
):
    """
    Show backend and WebUI logs
    """
    try:
        show_logs(console, backend=backend, webui=webui, follow=follow, lines=lines)
    except ServiceError as error:
        _handle_service_error(error)


@app.command(hidden=True)
def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Server host"),
    port: int = typer.Option(8000, "--port", "-p", help="Server port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes"),
):
    """
    Start the Flocks API server
    """
    import uvicorn

    console.print(Panel(logo(), border_style="cyan"))
    console.print(f"[cyan]Starting server on:[/cyan] http://{host}:{port}")
    console.print(f"[cyan]API docs:[/cyan] http://{host}:{port}/docs")
    console.print()

    uvicorn.run(
        "flocks.server.app:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


@app.command()
def tui(
    directory: Optional[Path] = typer.Option(None, "--directory", "-d", help="Project directory"),
    host: str = typer.Option("127.0.0.1", "--host", help="Server host"),
    port: int = typer.Option(8000, "--port", "-p", help="Server port"),
    session: Optional[str] = typer.Option(None, "--session", "-s", help="Session ID to continue"),
    auto_approve: bool = typer.Option(
        True,
        "--auto-approve/--no-auto-approve",
        help="Auto-approve all tool permissions without confirmation (default: True)",
    ),
):
    """
    Start Flocks with TUI interface

    This command starts the Flocks API server in the background and launches
    the TUI frontend to connect to it.
    """
    import os
    import subprocess
    import time

    import httpx

    # Determine paths
    flocks_dir = Path(__file__).parent.parent.parent
    tui_dir = flocks_dir / "tui"
    project_dir = directory or Path.cwd()

    # Check if bun is installed
    try:
        subprocess.run(["bun", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as error:
        console.print("[red]bun not found. Please install bun first:[/red]")
        console.print("  curl -fsSL https://bun.sh/install | bash")
        raise typer.Exit(1) from error

    # Check if TUI dependencies are installed
    if not (tui_dir / "node_modules").exists():
        console.print("[yellow]TUI dependencies not installed. Installing...[/yellow]")
        try:
            subprocess.run(
                ["bun", "install"],
                cwd=tui_dir,
                check=True,
            )
            console.print("[green]✓ TUI dependencies installed[/green]")
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Failed to install TUI dependencies: {e}[/red]")
            raise typer.Exit(1) from e

    console.print(Panel(logo(), border_style="cyan"))
    console.print("[cyan]Starting Flocks TUI...[/cyan]")
    console.print(f"[dim]Project directory: {project_dir}[/dim]")
    console.print(f"[dim]Server: http://{host}:{port}[/dim]")
    console.print()

    # Start server in background
    server_process = None
    try:
        console.print("[yellow]Starting Flocks server...[/yellow]")

        # Start server process
        env = os.environ.copy()

        # Set auto-approve environment variable for TUI mode
        if auto_approve:
            env["FLOCKS_AUTO_APPROVE"] = "true"
            console.print("[dim]Auto-approve enabled: All permissions will be automatically granted[/dim]")

        server_process = subprocess.Popen(
            resolve_flocks_cli_command() + [
                "serve",
                "--host",
                host,
                "--port",
                str(port),
            ],
            cwd=project_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait for server to be ready
        server_url = f"http://{host}:{port}"
        max_retries = 30
        for _i in range(max_retries):
            try:
                response = httpx.get(f"{server_url}/api/health", timeout=1.0)
                if response.status_code == 200:
                    console.print(f"[green]✓ Server started (PID: {server_process.pid})[/green]")
                    break
            except Exception:
                pass

            if server_process.poll() is not None:
                console.print("[red]Server process exited unexpectedly[/red]")
                raise typer.Exit(1)

            time.sleep(0.5)
        else:
            console.print("[red]Server failed to start within timeout[/red]")
            if server_process:
                server_process.terminate()
            raise typer.Exit(1)

        console.print()
        console.print("[green]═══════════════════════════════════════════════════════════════[/green]")
        console.print("[green]Launching TUI...[/green]")
        console.print("[green]═══════════════════════════════════════════════════════════════[/green]")
        console.print()
        console.print("[dim]Tips:[/dim]")
        console.print("  - Press Ctrl+K to open command palette")
        console.print("  - Press Ctrl+C to exit")
        console.print(f"  - API docs: {server_url}/docs")
        console.print()

        # Build TUI command - use flocks tui
        tui_cmd = [
            "bun",
            "run",
            "--conditions=browser",
            str(tui_dir / "src" / "index.ts"),
            "attach",
            server_url,
            "--dir",
            str(project_dir),
        ]
        if session:
            tui_cmd.extend(["--session", session])

        # Run TUI (blocking)
        subprocess.run(tui_cmd, cwd=tui_dir)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
    finally:
        # Cleanup server
        if server_process and server_process.poll() is None:
            console.print("[yellow]Stopping server...[/yellow]")
            server_process.terminate()
            try:
                server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_process.kill()
            console.print("[green]✓ Server stopped[/green]")


if __name__ == "__main__":
    app()

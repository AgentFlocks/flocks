"""
Main CLI entry point

Provides command-line interface for Flocks
"""

import asyncio
import os
import re
import secrets as secrets_lib
import sys
from pathlib import Path
from typing import Any, Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.markup import escape as rich_escape
from rich.panel import Panel

from flocks import __version__
from flocks.cli.commands import (
    acp_app,
    admin_app,
    agent_app,
    exec_command,
    workflow_app,
    device_app,
    model_app,
    plugin_app,
    BROWSER_CONTEXT_SETTINGS,
    browser_command,
    debug_app,
    doctor_command,
    export_app,
    import_app,
    mcp_app,
    session_app,
    skill_app,
    stats_app,
    task_app,
)
from flocks.cli.commands.update import update_command
from flocks.cli.service_config import (
    ServiceConfig,
    ServiceConfigError,
    build_service_config,
    restart_defaults_from_status_payload,
)
from flocks.cli.service_control import read_supervisor_status
from flocks.cli.service_manager import (
    ServiceError,
    resolve_flocks_cli_command,
    restart_all,
    restart_server,
    runtime_paths,
    show_logs,
    show_status,
    start_all,
    stop_all,
)
from flocks.cli.service_supervisor import run_service_daemon
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
app.command(name="exec", rich_help_panel="Agent CLI")(exec_command)
app.add_typer(agent_app, name="agent", rich_help_panel="Agent CLI")
app.add_typer(workflow_app, name="workflow", rich_help_panel="Agent CLI")
app.add_typer(device_app, name="device", rich_help_panel="Agent CLI")
app.add_typer(model_app, name="model", rich_help_panel="Agent CLI")
app.add_typer(plugin_app, name="plugin", rich_help_panel="Agent CLI")
app.add_typer(session_app, name="session")
app.add_typer(mcp_app, name="mcp")
app.add_typer(export_app, name="export")
app.add_typer(import_app, name="import")
app.add_typer(stats_app, name="stats")
app.add_typer(task_app, name="task")
app.add_typer(skill_app, name="skills")
app.add_typer(admin_app, name="admin")
# Developer/editor-integration groups: invocable, but kept out of `flocks --help`.
# (`agent` is registered above as part of the Agent CLI panel.)
app.add_typer(acp_app, name="acp", hidden=True)
app.add_typer(debug_app, name="debug", hidden=True)

app.command(name="update")(update_command)
app.command(name="doctor")(doctor_command)
app.command(
    name="browser",
    context_settings=BROWSER_CONTEXT_SETTINGS,
    help="Direct browser control via the built-in CDP runtime",
)(browser_command)

CLI_EXTENSION_ENTRY_POINT_GROUP = "flocks.cli"


def _register_extension_commands(target: typer.Typer) -> None:
    """Add command groups that installed packages declare under ``flocks.cli``.

    The entry point name is the command name and must not shadow a built-in
    one; its object is a ``typer.Typer`` or a callable returning one. A broken
    extension is reported on stderr and never takes the CLI down.
    """
    from importlib.metadata import entry_points

    taken = {group.name for group in target.registered_groups} | {
        command.name for command in target.registered_commands
    }
    for entry_point in entry_points(group=CLI_EXTENSION_ENTRY_POINT_GROUP):
        if entry_point.name in taken:
            continue
        try:
            loaded = entry_point.load()
            sub_app = loaded if isinstance(loaded, typer.Typer) else loaded()
            if not isinstance(sub_app, typer.Typer):
                raise TypeError(f"{entry_point.value} did not provide a typer.Typer")
        except Exception as exc:  # noqa: BLE001 - extensions must not break the CLI
            print(f"flocks: skipped CLI extension {entry_point.name!r}: {exc}", file=sys.stderr)
            continue
        target.add_typer(sub_app, name=entry_point.name)
        taken.add(entry_point.name)


def _should_load_cli_extensions(target: typer.Typer) -> bool:
    """Whether this invocation might need a `flocks.cli` extension.

    Loading an extension imports its whole engine (the code-audit extension
    alone is ~300ms). Built-in commands never need it, so skip loading when the
    invoked command is a built-in; still load for an unknown command (possibly
    an extension), for help, and for shell completion.
    """
    if os.environ.get("_FLOCKS_COMPLETE") or os.environ.get("_TYPER_COMPLETE_ARGS"):
        return True

    # The first positional token is the subcommand. Skip options and the value
    # that follows a value-taking global option in its space-separated form
    # (only `--log-level`), so `flocks --log-level DEBUG status` still resolves
    # to `status` rather than treating "DEBUG" as the command.
    tokens: list[str] = []
    skip_next = False
    for arg in sys.argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg == "--log-level":
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        tokens.append(arg)
    if not tokens:
        return True  # no args → help lists every command, extensions included

    def command_name(info: typer.models.CommandInfo) -> str:
        # `@app.command()` leaves name=None; Typer derives it from the callback
        # (lowercased, underscores become hyphens) only at invocation time.
        if info.name:
            return info.name
        callback = info.callback
        return (getattr(callback, "__name__", "") or "").lower().replace("_", "-")

    builtin = {group.name for group in target.registered_groups} | {
        command_name(command) for command in target.registered_commands
    }
    builtin.discard("")
    return tokens[0] not in builtin


console = Console()

_SERVICE_PREFIX_RE = re.compile(r"\[(flocks|daemon|watchdog)\]")
_LOG_LEVELS = (LogLevel.DEBUG, LogLevel.INFO, LogLevel.WARN, LogLevel.ERROR)
_PORT_OPTION_KWARGS: dict[str, Any] = {"min": 1, "max": 65535}


class _ServiceConsole:
    """Console adapter for service lifecycle output.

    Service messages carry literal ``[flocks]`` / ``[daemon]`` prefixes that Rich
    would otherwise parse as (unknown) markup tags and silently drop.  Escape those
    prefixes so the terminal shows the same text as the daemon log files.
    """

    def __init__(self, base: Console) -> None:
        self._base = base

    def print(self, *objects: Any, **kwargs: Any) -> None:
        rendered = tuple(
            _SERVICE_PREFIX_RE.sub(lambda match: "\\" + match.group(0), item) if isinstance(item, str) else item
            for item in objects
        )
        self._base.print(*rendered, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


def _service_console() -> _ServiceConsole:
    return _ServiceConsole(console)


def _service_error_types() -> tuple[type[BaseException], ...]:
    """Exceptions that service commands report as `[flocks] error:` lines."""
    from flocks.server.static_webui import WebUIDistMissingError

    return (ServiceConfigError, ServiceError, WebUIDistMissingError)


def _ensure_server_api_token() -> bool:
    """Ensure local non-browser clients such as `flocks tui` can authenticate."""
    from flocks.security import get_secret_manager
    from flocks.server.auth import API_TOKEN_SECRET_ID

    secrets = get_secret_manager()
    if secrets.get(API_TOKEN_SECRET_ID):
        return False

    secrets.set(API_TOKEN_SECRET_ID, secrets_lib.token_urlsafe(32))
    return True


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
    normalized_level = (log_level or "").strip().upper()
    if normalized_level not in _LOG_LEVELS:
        raise typer.BadParameter(
            f"{log_level!r} is not a valid log level. Choose from: {', '.join(_LOG_LEVELS)}.",
            param_hint="'--log-level'",
        )

    # Initialize logging
    try:
        asyncio.run(Log.init(print=print_logs, dev=False, level=normalized_level))
    except OSError as error:
        # Read-only / root-owned FLOCKS_ROOT: say so instead of dumping a traceback.
        console.print(
            f"[red]\\[flocks] error: 无法创建日志目录 {rich_escape(str(getattr(error, 'filename', '') or ''))}: "
            f"{rich_escape(str(error.strerror or error))}，请检查 FLOCKS_ROOT 目录属主/权限"
            "（chown 为运行用户，如容器内 1000:1000；SELinux 主机 -v 挂载加 :Z）。[/red]"
        )
        raise typer.Exit(1) from error

    # Outbound proxy policy (flocks.json network.proxy) applies to CLI commands and
    # everything they spawn (server, TUI, git / uv / npm) the same way as the server.
    try:
        from flocks.net import apply_process_environment

        apply_process_environment()
    except Exception as error:  # never block the CLI on a policy problem
        Log.create(service="cli").warn("cli.network.proxy.apply_failed", {"error": str(error)})

    Log.Default.info(
        "flocks.start",
        {
            "version": __version__,
        },
    )


def _service_config(
    no_browser: bool = False,
    skip_webui_build: bool = False,
    host: Optional[str] = None,
    port: Optional[int] = None,
    server_host: Optional[str] = None,
    server_port: Optional[int] = None,
    webui_host: Optional[str] = None,
    webui_port: Optional[int] = None,
    default_server_host: Optional[str] = None,
    default_server_port: Optional[int] = None,
    default_webui_host: Optional[str] = None,
    default_webui_port: Optional[int] = None,
) -> ServiceConfig:
    """Build service config from environment and CLI toggles."""
    try:
        global_config = Config.get_global()
    except OSError as error:
        # Read-only / root-owned FLOCKS_ROOT (e.g. a docker bind mount): report a
        # service error with the permission hint instead of a Python traceback.
        raise ServiceError(
            f"无法访问运行时目录 {getattr(error, 'filename', '') or ''}: {error.strerror or error}；"
            "请检查 FLOCKS_ROOT 目录的属主与权限（Docker 挂载卷需 chown 为容器用户，如 1000:1000）。"
        ) from error
    return build_service_config(
        no_browser=no_browser,
        skip_webui_build=skip_webui_build,
        public_host=host,
        public_port=port,
        server_host=server_host,
        server_port=server_port,
        webui_host=webui_host,
        webui_port=webui_port,
        default_server_host=default_server_host or global_config.server_host,
        default_server_port=default_server_port or global_config.server_port,
        default_webui_host=default_webui_host or "127.0.0.1",
        default_webui_port=default_webui_port or 5173,
    )


def _restart_runtime_defaults() -> dict[str, Any]:
    """Load host/port defaults from the running supervisor when available."""
    try:
        status = read_supervisor_status(paths=runtime_paths(), timeout=1.0)
    except Exception:
        return {}
    return restart_defaults_from_status_payload(getattr(status, "raw", status))


def _restart_service_config(
    no_browser: bool = False,
    skip_webui_build: bool = False,
    host: Optional[str] = None,
    port: Optional[int] = None,
    server_host: Optional[str] = None,
    server_port: Optional[int] = None,
    webui_host: Optional[str] = None,
    webui_port: Optional[int] = None,
) -> ServiceConfig:
    """Build restart config, reusing recorded host/port when CLI/env omit them."""
    return _service_config(
        no_browser=no_browser,
        skip_webui_build=skip_webui_build,
        host=host,
        port=port,
        server_host=server_host,
        server_port=server_port,
        webui_host=webui_host,
        webui_port=webui_port,
        **_restart_runtime_defaults(),
    )


def _handle_service_error(error: Exception) -> None:
    """Print service errors consistently."""
    console.print(f"[red]\\[flocks] error: {rich_escape(str(error))}[/red]")
    raise typer.Exit(1) from error


@app.command()
def start(
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open WebUI in a browser"),
    skip_webui_build: bool = typer.Option(
        False,
        "--skip-webui-build",
        help="Skip WebUI static asset build before starting Flocks service",
    ),
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Public service host"),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="Public service port", **_PORT_OPTION_KWARGS),
    server_host: Optional[str] = typer.Option(None, "--server-host", help="Backend server host"),
    server_port: Optional[int] = typer.Option(None, "--server-port", help="Backend server port", **_PORT_OPTION_KWARGS),
    webui_host: Optional[str] = typer.Option(None, "--webui-host", help="WebUI host"),
    webui_port: Optional[int] = typer.Option(None, "--webui-port", help="WebUI port", **_PORT_OPTION_KWARGS),
):
    """
    Start Flocks service in daemon mode.
    """
    try:
        config = _service_config(
            no_browser=no_browser,
            skip_webui_build=skip_webui_build,
            host=host,
            port=port,
            server_host=server_host,
            server_port=server_port,
            webui_host=webui_host,
            webui_port=webui_port,
        )
        start_all(config, _service_console())
    except _service_error_types() as error:
        _handle_service_error(error)


@app.command()
def stop():
    """
    Stop Flocks service.
    """
    try:
        stop_all(_service_console())
    except _service_error_types() as error:
        _handle_service_error(error)


@app.command()
def restart(
    server_only: bool = typer.Option(
        False,
        "--server-only",
        help="Restart only the backend server without stopping the supervisor daemon",
    ),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open WebUI in a browser"),
    skip_webui_build: bool = typer.Option(
        False,
        "--skip-webui-build",
        help="Skip WebUI static asset build before starting Flocks service",
    ),
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Public service host"),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="Public service port", **_PORT_OPTION_KWARGS),
    server_host: Optional[str] = typer.Option(None, "--server-host", help="Backend server host"),
    server_port: Optional[int] = typer.Option(None, "--server-port", help="Backend server port", **_PORT_OPTION_KWARGS),
    webui_host: Optional[str] = typer.Option(None, "--webui-host", help="WebUI host"),
    webui_port: Optional[int] = typer.Option(None, "--webui-port", help="WebUI port", **_PORT_OPTION_KWARGS),
):
    """
    Restart Flocks service. Agents must use `flocks restart --server-only`;
    bare restart stops the supervisor and terminates the running agent.
    """
    try:
        if server_only:
            restart_server(_service_console())
            return
        restart_all(
            _restart_service_config(
                no_browser=no_browser,
                skip_webui_build=skip_webui_build,
                host=host,
                port=port,
                server_host=server_host,
                server_port=server_port,
                webui_host=webui_host,
                webui_port=webui_port,
            ),
            _service_console(),
        )
    except _service_error_types() as error:
        _handle_service_error(error)


@app.command()
def status():
    """
    Show Flocks service status.
    """
    try:
        show_status(_service_console())
    except _service_error_types() as error:
        _handle_service_error(error)


@app.command()
def logs(
    backend: bool = typer.Option(False, "--backend", help="Only show service logs"),
    webui: bool = typer.Option(False, "--webui", help="Only show service logs"),
    follow: bool = typer.Option(True, "--follow/--no-follow", help="Follow logs in real time"),
    lines: int = typer.Option(50, "--lines", "-n", min=0, help="Number of recent lines to show"),
):
    """
    Show Flocks service logs.
    """
    try:
        show_logs(_service_console(), backend=backend, webui=webui, follow=follow, lines=lines)
    except _service_error_types() as error:
        _handle_service_error(error)


def _uvicorn_log_config() -> dict[str, Any]:
    """Uvicorn logging with timestamps, but without noisy access logs."""
    import copy

    from uvicorn.config import LOGGING_CONFIG

    cfg = copy.deepcopy(LOGGING_CONFIG)
    stamp_fmt = "%Y-%m-%d %H:%M:%S"
    for name in ("default", "access"):
        formatter = cfg["formatters"][name]
        formatter["fmt"] = "%(asctime)s | " + formatter["fmt"]
        formatter["datefmt"] = stamp_fmt
    cfg["loggers"]["uvicorn.access"]["handlers"] = []
    cfg["loggers"]["uvicorn.access"]["propagate"] = False
    return cfg


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

    os.environ["_FLOCKS_SERVER_PORT"] = str(port)
    # One id per service launch, inherited by all uvicorn workers/reload children.
    os.environ["_FLOCKS_SERVICE_BOOT_ID"] = secrets_lib.token_hex(16)

    console.print(Panel(logo(), border_style="cyan"))
    console.print(f"[cyan]Starting server on:[/cyan] http://{host}:{port}")
    console.print(f"[cyan]API docs:[/cyan] http://{host}:{port}/docs")
    console.print()

    uvicorn.run(
        "flocks.server.app:app",
        host=host,
        port=port,
        reload=reload,
        # Session loops, agent inboxes, the config cache and the knowledge engine
        # client live in this process; an explicit 1 stops WEB_CONCURRENCY from
        # forking workers that would each hold a diverging copy.
        workers=1,
        log_level="info",
        log_config=_uvicorn_log_config(),
        access_log=False,
    )


@app.command(name="service-daemon", hidden=True)
def service_daemon(
    server_host: str = typer.Option("127.0.0.1", "--server-host", help="Backend server host"),
    server_port: int = typer.Option(5173, "--server-port", help="Public service port"),
    webui_host: str = typer.Option("127.0.0.1", "--webui-host", help="WebUI host"),
    webui_port: int = typer.Option(5173, "--webui-port", help="WebUI port"),
    legacy_server_host: Optional[str] = typer.Option(None, "--legacy-server-host", help="Legacy backend host"),
    legacy_server_port: Optional[int] = typer.Option(8000, "--legacy-server-port", help="Legacy backend port"),
    server_port_migration_hint: bool = typer.Option(
        False,
        "--server-port-migration-hint",
        help="Print server-port migration hint in parent CLI",
    ),
    skip_webui_build: bool = typer.Option(False, "--skip-webui-build", help="Skip WebUI static asset build"),
):
    """
    Run the Flocks service supervisor daemon.
    """
    run_service_daemon(
        ServiceConfig(
            backend_host=server_host,
            backend_port=server_port,
            frontend_host=webui_host,
            frontend_port=webui_port,
            legacy_backend_host=legacy_server_host,
            legacy_backend_port=legacy_server_port,
            server_port_migration_hint=server_port_migration_hint,
            no_browser=True,
            skip_frontend_build=skip_webui_build,
        ),
    )


@app.command(hidden=True)
def run(
    message: Optional[str] = typer.Argument(None, help="Message to send; omit to start an interactive session"),
    directory: Optional[Path] = typer.Option(None, "--directory", "-d", help="Project directory"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model ID (provider/model)"),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Agent name"),
    session: Optional[str] = typer.Option(None, "--session", "-s", help="Session ID to continue"),
    continue_session: bool = typer.Option(False, "--continue", "-c", help="Continue the most recent session"),
    auto_confirm: bool = typer.Option(False, "--auto-confirm", "-y", help="Auto-confirm permission requests"),
):
    """
    Run an interactive Flocks session in the terminal (no server required).
    """
    from flocks.cli import session_runner

    project_dir = (directory or Path.cwd()).expanduser().resolve()
    asyncio.run(
        session_runner.run_session(
            directory=project_dir,
            message=message,
            model=model,
            agent=agent,
            session_id=session,
            continue_session=continue_session,
            console=console,
            auto_confirm=auto_confirm,
        )
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
        if _ensure_server_api_token():
            console.print("[dim]Initialized local API token for TUI access[/dim]")

        if auto_approve:
            env["FLOCKS_AUTO_APPROVE"] = "true"
            console.print(
                "[dim]Auto-approve enabled: all permissions will be automatically granted[/dim]"
            )

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
                # loopback health probe: never through a proxy
                response = httpx.get(f"{server_url}/api/health", timeout=1.0, trust_env=False)
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
        console.print("  - Press Ctrl+P to open command palette")
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

        # Run TUI (blocking). Bun's fetch follows HTTP_PROXY / NO_PROXY but rejects
        # socks:// URLs outright, so hand it the http-only view of the proxy policy.
        from flocks.net import child_env

        subprocess.run(tui_cmd, cwd=tui_dir, env=child_env(socks_capable=False))

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


# Extension command groups load last, after every built-in is registered, so
# the built-in set is complete when deciding whether an extension is needed.
if _should_load_cli_extensions(app):
    _register_extension_commands(app)


if __name__ == "__main__":
    app()

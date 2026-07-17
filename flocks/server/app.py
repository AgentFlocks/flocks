"""
FastAPI application for Flocks server

Main HTTP API server for AI-Native SecOps Platform
"""

import asyncio
import inspect
import os
import time
from dataclasses import dataclass
from types import SimpleNamespace
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from flocks.utils.log import Log, LogLevel
from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.utils.langfuse import initialize as init_observability, shutdown as shutdown_observability
from flocks.auth.service import AuthService
from flocks.extensions import ExtensionOptions, handler_name, normalize_fail_policy, normalize_timeout
from flocks.server.auth import apply_auth_for_request, clear_auth_context
from flocks.server.static_webui import maybe_serve_static_webui

# Load .env file at startup
try:
    from dotenv import load_dotenv
    # Try to find .env in project root
    current_dir = Path(__file__).parent.parent.parent  # Go up to project root
    env_file = current_dir / ".env"
    if env_file.exists():
        load_dotenv(env_file)
        print(f"[OK] Loaded environment from {env_file}")
    else:
        # Try current working directory
        load_dotenv()
        print("[OK] Loaded environment from current directory")
except ImportError:
    print("[WARN] python-dotenv not installed, skipping .env loading")
except Exception as e:
    print(f"[WARN] Failed to load .env: {e}")


# Lifespan context manager for startup/shutdown
async def _maybe_await(result: Any) -> Any:
    """Await values that are awaitable and return plain values unchanged."""
    if inspect.isawaitable(result):
        return await result
    return result


async def _run_startup_phase(
    log,
    phase: str,
    fn: Callable[[], Any],
) -> Any:
    """Execute one startup phase and emit structured timing logs."""
    started_at = time.perf_counter()
    try:
        result = await _maybe_await(fn())
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        log.warning("server.startup.phase", {
            "phase": phase,
            "status": "failed",
            "duration_ms": duration_ms,
            "error": str(exc),
        })
        raise

    duration_ms = int((time.perf_counter() - started_at) * 1000)
    log.info("server.startup.phase", {
        "phase": phase,
        "status": "completed",
        "duration_ms": duration_ms,
    })
    return result


def _schedule_startup_phase(
    app: FastAPI,
    log,
    phase: str,
    fn: Callable[[], Any],
) -> None:
    """Run a non-critical startup phase in the background after app is ready."""

    async def _runner() -> None:
        try:
            await _run_startup_phase(log, phase, fn)
        except Exception:
            # _run_startup_phase already logged the failure.
            return

    task = asyncio.create_task(_runner(), name=f"startup:{phase}")
    app.state.startup_background_tasks.append(task)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application lifecycle"""
    # Ensure file logging when server is started without CLI (e.g. uvicorn app:app)
    if Log._writer is None:
        await Log.init(print=False, dev=False, level=LogLevel.INFO)

    log = Log.create(service="server")
    if not hasattr(app, "state") or app.state is None:
        app.state = SimpleNamespace()
    app.state.startup_background_tasks = []
    startup_started_at = time.perf_counter()

    # Startup
    log.info("server.startup", {"version": "0.2.0"})
    try:
        from flocks.updater.updater import cleanup_replaced_files

        await _run_startup_phase(
            log,
            "updater.cleanup_leftovers",
            lambda: asyncio.to_thread(cleanup_replaced_files),
        )
        log.info("updater.leftovers.cleaned")
    except Exception as e:
        log.warning("updater.leftovers.cleanup_failed", {"error": str(e)})

    try:
        from flocks.updater.updater import _get_repo_root, _refresh_global_cli_entry

        await _run_startup_phase(
            log,
            "cli.refresh_global_entry",
            lambda: asyncio.to_thread(_refresh_global_cli_entry, _get_repo_root()),
        )
        log.info("cli.global_entry.refreshed")
    except Exception as e:
        log.warning("cli.global_entry.refresh_failed", {"error": str(e)})

    try:
        await _run_startup_phase(
            log,
            "observability.init",
            init_observability,
        )
        log.info("observability.initialized")
    except Exception as e:
        log.warning("observability.init_failed", {"error": str(e)})
    
    # Ensure config files exist (copy from examples if needed)
    try:
        from flocks.config.config_writer import ensure_config_files
        await _run_startup_phase(
            log,
            "config.ensure_files",
            ensure_config_files,
        )
        log.info("config.files.checked")
    except Exception as e:
        log.warning("config.files.check_failed", {"error": str(e)})

    # Migrate ``api_services`` blocks to versioned storage keys. Idempotent:
    # cheap re-run on every startup, copies legacy ``service_id`` entries to
    # ``<service_id>_v<version>`` once the plugin declares a version.
    try:
        from flocks.config.api_versioning import migrate_api_services
        actions = await _run_startup_phase(
            log,
            "config.migrate_api_services",
            migrate_api_services,
        )
        copied = [k for k, v in actions.items() if v == "copied"]
        if copied:
            log.info("config.api_services.migrated", {"copied": copied})
    except Exception as e:
        log.warning("config.api_services.migrate_failed", {"error": str(e)})
    
    # Initialize storage
    await _run_startup_phase(log, "storage.init", Storage.init)
    log.info("storage.initialized")

    async def _recover_orphan_tool_parts() -> None:
        from flocks.session.orphan_tools import abort_all_orphan_running_parts

        repaired = await abort_all_orphan_running_parts()
        if repaired:
            log.info("session.orphan_tools.recovered", {"count": repaired})

    _schedule_startup_phase(
        app,
        log,
        "session.recover_orphan_tools",
        _recover_orphan_tool_parts,
    )

    try:
        from flocks.console.scheduler import ConsoleSyncScheduler

        async def _start_console_sync_phase() -> None:
            await ConsoleSyncScheduler.send_startup_heartbeat()
            await ConsoleSyncScheduler.start()

        _schedule_startup_phase(app, log, "console.sync.start", _start_console_sync_phase)
    except Exception as e:
        log.warning("console.sync.start_failed", {"error": str(e)})

    # Ensure default device room exists, then migrate legacy device API
    # configs from flocks.json → device_integrations table.
    try:
        from flocks.tool.device import device_startup
        await _run_startup_phase(log, "device.startup", device_startup)
        log.info("device.startup.done")
    except Exception as e:
        log.warning("device.startup.failed", {"error": str(e)})

    # Initialize local auth/account tables
    await _run_startup_phase(log, "auth.init", AuthService.init)
    log.info("auth.initialized")

    # Best-effort migration: old sessions default to admin ownership.
    # The migration itself is idempotent (guarded by a persisted marker),
    # but we still skip loading users when the marker is already set
    # to avoid unnecessary DB + session scans on every startup.
    async def _migrate_legacy_sessions_to_admin() -> None:
        # ``Storage.get`` interprets a non-``None`` ``model`` argument as a
        # Pydantic model and calls ``model_validate_json``.  Passing the
        # builtin ``dict`` type therefore raised ``AttributeError``; omit the
        # model so the value is decoded with ``json.loads``.
        marker = await Storage.get("auth:migration:legacy_session_owner_to_admin")
        if isinstance(marker, dict) and marker.get("done"):
            return
        if not await AuthService.has_users():
            return
        users = await AuthService.list_users()
        admin = next((u for u in users if u.role == "admin"), None)
        if admin:
            await AuthService.migrate_legacy_sessions_to_admin(admin.id)

    _schedule_startup_phase(
        app,
        log,
        "auth.migrate_legacy_session_owner",
        _migrate_legacy_sessions_to_admin,
    )
    
    # Setup question handler for real user interaction
    from flocks.tool.question_handler import setup_api_question_handler
    await _run_startup_phase(
        log,
        "question_handler.setup",
        setup_api_question_handler,
    )
    log.info("question_handler.initialized")
    
    # Register built-in hooks if memory is enabled
    try:
        config = await Config.get()
        # ``config.memory`` may be ``None`` when the memory system is not
        # configured at all; in that case there is nothing to register.
        memory_cfg = getattr(config, "memory", None)
        memory_enabled = bool(getattr(memory_cfg, "enabled", False)) if memory_cfg else False
        if memory_enabled:
            from flocks.hooks.builtin import register_builtin_hooks
            await _run_startup_phase(
                log,
                "hooks.register_builtin",
                register_builtin_hooks,
            )
            log.info("hooks.registered")
    except Exception as e:
        # Hook registration failure should not stop server startup
        log.warn("hooks.register_failed", {"error": str(e)})

    # Migrate env-var credentials to .secret.json (idempotent)
    try:
        from flocks.provider.credential import migrate_env_credentials

        def _migrate_env_credentials_phase() -> None:
            migrated = migrate_env_credentials()
            if migrated > 0:
                log.info("credential.env_migration.done", {"migrated": migrated})

        _schedule_startup_phase(
            app,
            log,
            "credential.migrate_env_credentials",
            _migrate_env_credentials_phase,
        )
    except Exception as e:
        log.warning("credential.env_migration.failed", {"error": str(e)})

    # Sync new catalog models into flocks.json for existing providers (idempotent)
    try:
        from flocks.provider.model_catalog import sync_catalog_models_to_config

        def _sync_catalog_models_phase() -> None:
            synced = sync_catalog_models_to_config()
            if synced > 0:
                log.info("catalog.model_sync.done", {"models_added": synced})

        _schedule_startup_phase(
            app,
            log,
            "catalog.sync_models_to_config",
            _sync_catalog_models_phase,
        )
    except Exception as e:
        log.warning("catalog.model_sync.failed", {"error": str(e)})

    # Load custom providers from flocks.json into runtime
    try:
        from flocks.server.routes.custom_provider import load_custom_providers_on_startup
        await _run_startup_phase(
            log,
            "custom_providers.load",
            load_custom_providers_on_startup,
        )
        log.info("custom_providers.loaded")
    except Exception as e:
        log.warning("custom_providers.load.failed", {"error": str(e)})

    # Initialize MCP servers on startup so installed servers reconnect automatically
    # after a service restart, without requiring manual UI reconnection.
    try:
        from flocks.mcp import MCP

        _schedule_startup_phase(app, log, "mcp.init", MCP.init)
    except Exception as e:
        log.warning("mcp.init_failed", {"error": str(e)})

    # Sync workflows from .flocks/workflow/ filesystem into Storage
    try:
        from flocks.server.routes.workflow import (
            reconcile_published_workflow_api_services,
            sync_workflows_from_filesystem,
        )

        async def _sync_workflows_phase() -> None:
            imported = await sync_workflows_from_filesystem()
            log.info("workflow.sync.done", {"imported": imported})
            api_services = await reconcile_published_workflow_api_services()
            log.info("workflow.api_services.reconciled", api_services)

        _schedule_startup_phase(app, log, "workflow.sync_filesystem", _sync_workflows_phase)
    except Exception as e:
        log.warning("workflow.sync.failed", {"error": str(e)})

    # Start Task Center (scheduler + queue executor)
    try:
        from flocks.task.manager import TaskManager
        await _run_startup_phase(
            log,
            "task_manager.start",
            TaskManager.start,
        )
        log.info("task_manager.started")
    except Exception as e:
        from flocks.task.manager import TaskManager
        TaskManager.mark_start_failed(e)
        log.warning("task_manager.start.failed", {"error": str(e)})

    # Seed built-in scheduled tasks from .flocks/plugins/tasks/*.json (idempotent)
    try:
        from flocks.task.plugin import seed_tasks_from_plugin

        async def _seed_tasks_phase() -> None:
            seeded = await seed_tasks_from_plugin()
            if seeded:
                log.info("task.plugin.seeded", {"count": seeded})

        _schedule_startup_phase(app, log, "task.seed_plugin_specs", _seed_tasks_phase)
    except Exception as e:
        log.warning("task.plugin.seed_failed", {"error": str(e)})

    # Start Skill file watcher (auto-invalidate cache on SKILL.md changes)
    try:
        from flocks.skill.skill import Skill

        def _start_skill_watcher() -> None:
            Skill.start_watcher()
            log.info("skill.watcher.initialized")

        _schedule_startup_phase(app, log, "skill.watcher.start", _start_skill_watcher)
    except Exception as e:
        log.warning("skill.watcher.init_failed", {"error": str(e)})

    # Start Agent file watcher (auto-invalidate cache on plugin agent changes)
    try:
        from flocks.agent.registry import Agent

        def _start_agent_watcher() -> None:
            Agent.start_watcher()
            log.info("agent.watcher.initialized")

        _schedule_startup_phase(app, log, "agent.watcher.start", _start_agent_watcher)
    except Exception as e:
        log.warning("agent.watcher.init_failed", {"error": str(e)})

    # Warm ToolRegistry in the background so the first Tool page load usually
    # hits a hot in-memory registry instead of doing plugin discovery.
    try:
        from flocks.tool.registry import ToolRegistry

        def _init_tool_registry() -> None:
            ToolRegistry.init()
            log.info("tool.registry.initialized")

        _schedule_startup_phase(app, log, "tool.registry.init", lambda: asyncio.to_thread(_init_tool_registry))

        # Start Tool file watcher (auto-reload plugin tools on file changes)
        def _start_tool_watcher() -> None:
            ToolRegistry.start_watcher()
            log.info("tool.watcher.initialized")

        _schedule_startup_phase(app, log, "tool.watcher.start", _start_tool_watcher)
    except Exception as e:
        log.warning("tool.registry.init_failed", {"error": str(e)})

    # Start WebUI page watcher (auto-build user custom pages)
    try:
        from flocks.contracts.webui.bootstrap import reconcile_webui_pages
        from flocks.contracts.webui.watcher import set_event_loop, start_watcher

        set_event_loop(asyncio.get_running_loop())

        _schedule_startup_phase(
            app,
            log,
            "webui_pages.bootstrap",
            reconcile_webui_pages,
        )

        def _start_webui_pages_watcher() -> None:
            start_watcher()
            log.info("webui_pages.watcher.initialized")

        _schedule_startup_phase(app, log, "webui_pages.watcher.start", _start_webui_pages_watcher)
    except Exception as e:
        log.warning("webui_pages.watcher.init_failed", {"error": str(e)})

    # Start Channel Gateway (connect enabled IM channels)
    try:
        from flocks.channel.gateway.manager import default_manager

        async def _start_channel_gateway() -> None:
            await default_manager.start_all()
            log.info("channel.gateway.started")

        _schedule_startup_phase(app, log, "channel.gateway.start", _start_channel_gateway)
    except Exception as e:
        log.warning("channel.gateway.start_failed", {"error": str(e)})

    # Start the unified workflow trigger runtime after the server is ready.
    try:
        from flocks.workflow.triggers.runtime import default_runtime as default_trigger_runtime

        async def _delayed_trigger_runtime_start() -> None:
            await asyncio.sleep(3)
            try:
                await default_trigger_runtime.start_all()
                log.info("workflow.trigger_runtime.started")
            except Exception as exc:
                log.warning("workflow.trigger_runtime.start_failed", {"error": str(exc)})

        _schedule_startup_phase(app, log, "workflow.trigger_runtime.start", _delayed_trigger_runtime_start)
    except Exception as e:
        log.warning("workflow.trigger_runtime.start_failed", {"error": str(e)})

    if os.getenv("_FLOCKS_SERVICE_CONFIG"):
        log.info("updater.recovery.skipped", {"reason": "managed_service"})
    else:
        try:
            from flocks.updater.updater import recover_upgrade_state

            await _run_startup_phase(
                log,
                "updater.recover_upgrade_state",
                lambda: asyncio.to_thread(recover_upgrade_state),
            )
            log.info("updater.recovery.checked")
        except Exception as e:
            log.warning("updater.recovery.failed", {"error": str(e)})

    blocking_startup_ms = int((time.perf_counter() - startup_started_at) * 1000)
    log.info("server.startup.ready", {
        "blocking_duration_ms": blocking_startup_ms,
        "background_tasks": len(app.state.startup_background_tasks),
    })

    yield

    background_tasks = list(getattr(app.state, "startup_background_tasks", []))
    for task in background_tasks:
        if not task.done():
            task.cancel()
    if background_tasks:
        await asyncio.gather(*background_tasks, return_exceptions=True)

    try:
        from flocks.console.scheduler import ConsoleSyncScheduler

        await ConsoleSyncScheduler.stop()
    except Exception as exc:
        log.warning("console.sync.stop_failed", {"error": str(exc)})

    # Notify SSE clients before stopping sessions, MCP transports, and other
    # long-lived runtime services so browser listeners see the shutdown event.
    try:
        from flocks.server.routes.event import EventBroadcaster
        broadcaster = EventBroadcaster.get()
        client_count = broadcaster.client_count
        if client_count > 0:
            log.info("server.shutdown.notifying_clients", {"clients": client_count})
            await broadcaster.shutdown()
    except Exception as e:
        log.warning("server.shutdown.notify_failed", {"error": str(e)})

    # Wait briefly for running sessions to finish (best-effort grace period)
    try:
        from flocks.session.core.status import SessionStatus
        grace_seconds = 5
        for i in range(grace_seconds):
            busy = SessionStatus.get_busy_session_ids()
            if not busy:
                break
            log.info("server.shutdown.waiting_sessions", {
                "busy_count": len(busy),
                "remaining_seconds": grace_seconds - i,
            })
            await asyncio.sleep(1)
    except Exception as e:
        log.warning("server.shutdown.wait_sessions_failed", {"error": str(e)})

    # Stop Channel Gateway
    try:
        from flocks.channel.gateway.manager import default_manager
        await default_manager.stop_all()
        log.info("channel.gateway.stopped")
    except Exception as e:
        log.warning("channel.gateway.stop_failed", {"error": str(e)})

    # Stop the unified workflow trigger runtime.
    try:
        from flocks.workflow.triggers.runtime import default_runtime as default_trigger_runtime

        await default_trigger_runtime.stop_all()
        log.info("workflow.trigger_runtime.stopped")
    except Exception as e:
        log.warning("workflow.trigger_runtime.stop_failed", {"error": str(e)})

    # Stop Task Center
    try:
        from flocks.task.manager import TaskManager
        from flocks.task.store import TaskStore
        await TaskManager.stop()
        await TaskStore.close()
        log.info("task_manager.stopped")
    except Exception as e:
        log.warning("task_manager.stop.failed", {"error": str(e)})
    
    # Stop Skill file watcher
    try:
        from flocks.skill.skill import Skill
        Skill.stop_watcher()
    except Exception as e:
        log.warning("skill.watcher.stop_failed", {"error": str(e)})

    # Stop WebUI page watcher
    try:
        from flocks.contracts.webui.watcher import stop_watcher
        stop_watcher()
    except Exception as e:
        log.warning("webui_pages.watcher.stop_failed", {"error": str(e)})

    # Shutdown MCP connections
    try:
        from flocks.mcp import MCP
        await MCP.shutdown()
        log.info("mcp.shutdown")
    except Exception as e:
        log.warning("mcp.shutdown_failed", {"error": str(e)})

    # Dispose all instances
    try:
        from flocks.project.instance import Instance
        await Instance.dispose_all()
        log.info("instances.disposed")
    except Exception as e:
        log.warning("instances.dispose.failed", {"error": str(e)})

    # Final WAL checkpoint — *after* every other subsystem has stopped
    # writing.  This drains any residual frames into the main DB and
    # truncates the ``-wal`` file to zero length, so the next process
    # start does not need a WAL recovery step (which is where a poorly
    # timed power loss can corrupt the main-DB header page).
    try:
        await Storage.shutdown()
    except Exception as e:
        log.warning("storage.shutdown_failed", {"error": str(e)})

    try:
        shutdown_observability()
    except Exception as e:
        log.warning("observability.shutdown_failed", {"error": str(e)})

    log.info("server.shutdown")


# Create FastAPI application
app = FastAPI(
    title="Flocks API",
    description="AI-Native SecOps Platform with multi-agent collaboration",
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Logger
log = Log.create(service="server")


@dataclass
class _HTTPMiddlewareHook:
    hook: Callable[..., Any]
    options: ExtensionOptions


_http_middleware_hooks: list[_HTTPMiddlewareHook] = []


def register_http_middleware(
    hook: Callable[..., Any],
    *,
    name: Optional[str] = None,
    priority: int = 100,
    timeout_seconds: Optional[float] = None,
    fail_policy: Any = None,
    critical: bool = False,
) -> None:
    """Register an extension hook that can inspect HTTP requests."""
    options = ExtensionOptions(
        name=handler_name(hook, name),
        priority=priority,
        timeout_seconds=normalize_timeout(timeout_seconds),
        fail_policy=normalize_fail_policy(fail_policy, critical=critical),
    )
    registration = _HTTPMiddlewareHook(hook=hook, options=options)

    _http_middleware_hooks[:] = [
        existing for existing in _http_middleware_hooks
        if existing.options.name != options.name
    ]
    _http_middleware_hooks.append(registration)
    _http_middleware_hooks.sort(key=lambda item: (item.options.priority, item.options.name))


async def _run_http_middleware_hooks(request: Request, context: dict[str, Any]) -> None:
    for registration in list(_http_middleware_hooks):
        try:
            result = registration.hook(request, context)
            if registration.options.timeout_seconds is not None:
                await asyncio.wait_for(_maybe_await(result), timeout=registration.options.timeout_seconds)
            else:
                await _maybe_await(result)
        except Exception as exc:
            log.warning("http.middleware_hook.failed", {
                "name": registration.options.name,
                "stage": context.get("stage"),
                "fail_policy": registration.options.fail_policy.value,
                "error": repr(exc),
            })
            if registration.options.critical:
                raise


_REQUEST_LOG_SKIP_EXACT = frozenset({
    "/health",
    "/api/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/event",
    "/api/session/status",
})

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "frame-ancestors 'self'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def _is_noisy_request_path(path: str) -> bool:
    """Return True for high-frequency polling endpoints that are noisy on success."""
    if path in _REQUEST_LOG_SKIP_EXACT:
        return True
    if path.startswith("/api/session/") and path.endswith("/message"):
        return True
    if path.startswith("/api/question/session/") and path.endswith("/pending"):
        return True
    return False


def _should_log_request(path: str, status_code: int) -> bool:
    """Keep abnormal responses visible while suppressing successful polling noise."""
    if status_code >= 400:
        return True
    return not _is_noisy_request_path(path)


# CORS Configuration
#
# Priority order:
#   1. Runtime env vars exported by the supervised backend launcher → add the concrete
#      ``_FLOCKS_WEBUI_*`` origin inferred from the current CLI launch.
#   2. Explicit ``server.cors`` in flocks.json → append user-configured
#      origins without discarding the runtime ones.
# We deliberately do NOT auto-whitelist wildcard binds such as ``0.0.0.0``:
# matching ``[^/]+:<port>`` would accept every host on that port, effectively
# disabling CORS.  Remote deployments that bind to wildcard hosts must keep
# using explicit ``server.cors`` entries or start with a concrete IP/hostname.
#
# Config is read lazily on the first request via
# :class:`_DeferredCORSMiddleware` so that importing ``app`` in an async
# context (e.g. pytest fixtures) does not call ``asyncio.run()`` inside a
# running event loop, and so that ``Config.get_global()`` is not invoked at
# import time — which would otherwise cache ``HOME`` before test harnesses
# can monkey-patch it.

_LOOPBACK_ORIGIN_HOSTS = {"127.0.0.1", "localhost", "::1"}
_WILDCARD_HOSTS = {"0.0.0.0", "::"}


def _format_host_for_url(host: str) -> str:
    """Wrap IPv6 literals in brackets before composing origins."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _append_origin(origins: list[str], host: str, port: str) -> None:
    if not host or not port or host in _WILDCARD_HOSTS:
        return
    hosts = sorted(_LOOPBACK_ORIGIN_HOSTS) if host in _LOOPBACK_ORIGIN_HOSTS else [host]
    for candidate_host in hosts:
        origin = f"http://{_format_host_for_url(candidate_host)}:{port}"
        if origin not in origins:
            origins.append(origin)


def _read_cors_config() -> tuple[list[str], Optional[str]]:
    """Return (allow_origins, allow_origin_regex) for CORSMiddleware.

    Reads ``server.cors`` directly from ``flocks.json`` using synchronous
    JSON I/O — this avoids ``asyncio.run()`` inside a running event loop
    and keeps the hot path off the async ``Config.get()`` pipeline.
    """
    import json

    origins: list[str] = []
    _append_origin(
        origins,
        os.environ.get("_FLOCKS_WEBUI_HOST", ""),
        os.environ.get("_FLOCKS_WEBUI_PORT", ""),
    )

    try:
        cfg_file = Config.get_config_file()
        if cfg_file.exists():
            with cfg_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            server_cfg = data.get("server") or {}
            cors = server_cfg.get("cors")
            if isinstance(cors, list):
                for candidate in cors:
                    if isinstance(candidate, str) and candidate and candidate not in origins:
                        origins.append(candidate)
    except Exception:
        pass

    return origins, None


class _DeferredCORSMiddleware:
    """Lazy wrapper around :class:`CORSMiddleware`.

    Starlette builds the middleware stack on the first request, but the
    inner middleware's constructor kwargs are evaluated at
    ``add_middleware`` call time.  We defer one step further: the wrapped
    :class:`CORSMiddleware` is instantiated on the first incoming request,
    after the test harness (or the real runtime) has finished setting up
    ``HOME`` / config paths.
    """

    def __init__(self, app) -> None:
        self.app = app
        self._inner = None

    async def __call__(self, scope, receive, send):
        if self._inner is None:
            allow_origins, allow_origin_regex = _read_cors_config()
            self._inner = CORSMiddleware(
                self.app,
                allow_origins=allow_origins,
                allow_origin_regex=allow_origin_regex,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )
        await self._inner(scope, receive, send)


class _SecurityHeadersMiddleware:
    """Attach baseline browser security headers without spawning request tasks."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


app.add_middleware(_SecurityHeadersMiddleware)


class _InstanceContextMiddleware:
    """Provide the project instance context without buffering the response body."""

    _SKIP_PREFIXES = (
        "/global",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/health",
        "/path",
        "/permission",
        "/question",
        "/tui",
    )

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path.startswith(self._SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return

        from urllib.parse import unquote
        from flocks.project.instance import Instance
        from flocks.project.bootstrap import instance_bootstrap

        request = Request(scope, receive=receive)
        directory = request.query_params.get("directory")
        if not directory:
            directory = request.headers.get("x-flocks-directory")
        if not directory:
            directory = os.getcwd()

        try:
            directory = unquote(directory)
        except Exception:
            pass

        async def handle_request() -> None:
            await self.app(scope, receive, send)

        await Instance.provide(
            directory=directory,
            init=instance_bootstrap,
            fn=handle_request,
        )


app.add_middleware(_InstanceContextMiddleware)


class _RequestLoggingMiddleware:
    """Log response start without retaining an extra task for streaming bodies."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")
        started_at = time.monotonic()
        response_started = False

        async def send_with_request_log(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start" and not response_started:
                response_started = True
                status_code = int(message["status"])
                if _should_log_request(path, status_code):
                    duration_ms = int((time.monotonic() - started_at) * 1000)
                    log.info(
                        "request.complete",
                        {
                            "method": method,
                            "path": path,
                            "status": status_code,
                            "duration": duration_ms,
                        },
                    )
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_log)
        except Exception as exc:
            if not response_started:
                duration_ms = int((time.monotonic() - started_at) * 1000)
                log.error(
                    "request.error",
                    {
                        "method": method,
                        "path": path,
                        "duration": duration_ms,
                        "error": str(exc),
                    },
                )
            raise


app.add_middleware(_RequestLoggingMiddleware)


class _HookRequest(Request):
    """Preserve request-receive semantics while auth hooks inspect the body.

    A body read is replayed once to the endpoint, a fully consumed stream is
    replayed as an empty request, and a partially consumed stream continues
    from upstream. Once a disconnect is observed, downstream reads keep seeing
    the disconnect instead of polling upstream again.
    """

    def __init__(self, scope: Scope, receive: Receive) -> None:
        self._flocks_upstream_receive = receive
        self._flocks_request_complete = False
        self._flocks_disconnected = False
        self._flocks_cached_body: Optional[bytes] = None
        self._flocks_stream_consumed = False
        super().__init__(scope, receive=self._receive_for_hook)

    async def _receive_for_hook(self) -> Message:
        message = await self._flocks_upstream_receive()
        if message["type"] == "http.request":
            if not message.get("more_body", False):
                self._flocks_request_complete = True
        elif message["type"] == "http.disconnect":
            self._flocks_disconnected = True
        return message

    async def body(self) -> bytes:
        body = await super().body()
        self._flocks_cached_body = body
        return body

    async def stream(self) -> AsyncGenerator[bytes, None]:
        async for chunk in super().stream():
            if self._flocks_request_complete:
                self._flocks_stream_consumed = True
            yield chunk
        self._flocks_stream_consumed = True

    def downstream_receive(self) -> Receive:
        """Return the receive callable that preserves the hook's body reads."""
        has_replay = self._flocks_cached_body is not None or self._flocks_stream_consumed
        if not has_replay and not self._flocks_disconnected:
            return self._flocks_upstream_receive

        replay_pending = has_replay
        replay_body = self._flocks_cached_body if self._flocks_cached_body is not None else b""

        async def receive_for_downstream() -> Message:
            nonlocal replay_pending
            if replay_pending:
                replay_pending = False
                return {
                    "type": "http.request",
                    "body": replay_body,
                    "more_body": False,
                }
            if self._flocks_disconnected:
                return {"type": "http.disconnect"}

            message = await self._flocks_upstream_receive()
            if message["type"] == "http.disconnect":
                self._flocks_disconnected = True
                return message
            if has_replay:
                raise RuntimeError(f"Unexpected message received: {message['type']}")
            return message

        return receive_for_downstream


class _AuthGuardMiddleware:
    """Bind request auth context in the same ASGI task as the endpoint."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = _HookRequest(scope, receive)
        try:
            await _run_http_middleware_hooks(request, {"stage": "before_auth"})
            _blocked, token, _user = await apply_auth_for_request(request)
        except StarletteHTTPException as exc:
            response = JSONResponse(
                status_code=exc.status_code,
                content={"error": "AuthError", "message": exc.detail},
            )
            await response(scope, receive, send)
            return
        except Exception as exc:
            log.error(
                "auth.middleware.unexpected",
                {
                    "path": request.url.path,
                    "error": repr(exc),
                },
            )
            response = JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "InternalError", "message": "鉴权处理异常，请稍后重试"},
            )
            await response(scope, receive, send)
            return

        try:
            await self.app(scope, request.downstream_receive(), send)
        finally:
            clear_auth_context(token)


app.add_middleware(_AuthGuardMiddleware)


class _StaticWebUIMiddleware:
    """Serve the SPA shell before auth without a BaseHTTPMiddleware task."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        static_response = await maybe_serve_static_webui(request)
        if static_response is not None:
            await static_response(scope, receive, send)
            return
        await self.app(scope, receive, send)


app.add_middleware(_StaticWebUIMiddleware)


# Error Handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle validation errors"""
    log.warning("validation.error", {
        "path": request.url.path,
        "errors": exc.errors(),
    })
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "ValidationError",
            "message": "Request validation failed",
            "details": exc.errors(),
        }
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handle HTTP exceptions"""
    log.error("http.error", {
        "path": request.url.path,
        "status": exc.status_code,
        "detail": exc.detail,
    })
    
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": "HTTPException",
            "message": exc.detail,
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle all other exceptions"""
    import traceback
    tb = traceback.format_exc()
    log.error("server.error", {
        "path": request.url.path,
        "error": str(exc),
        "type": type(exc).__name__,
        "traceback": tb,
    })
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "InternalServerError",
            "message": "Internal server error",
        }
    )


# Configure CORS (config is read lazily on the first request; see
# _DeferredCORSMiddleware for rationale).
app.add_middleware(_DeferredCORSMiddleware)


# Import and include routers
from flocks.server.routes.health import router as health_router
from flocks.server.routes.session import router as session_router
from flocks.server.routes.provider import router as provider_router
from flocks.server.routes.config import router as config_router
from flocks.server.routes.project import router as project_router
from flocks.server.routes.file import router as file_router
from flocks.server.routes.message import router as message_router
from flocks.server.routes.agent import router as agent_router
from flocks.server.routes.model import router as model_router
# Added in Batch 3
from flocks.server.routes.tool import router as tool_router
from flocks.server.routes.pty import router as pty_router
# Added in Batch 4
from flocks.server.routes.lsp import router as lsp_router
# Added in Batch 5
from flocks.server.routes.mcp import router as mcp_router
# Added for TUI compatibility
from flocks.server.routes.event import router as event_router
from flocks.server.routes.global_ import router as global_router
from flocks.server.routes.path import router as path_router
from flocks.server.routes.vcs import router as vcs_router
from flocks.server.routes.find import router as find_router
from flocks.server.routes.misc import router as misc_router
# P1: Permission and Question routes for Flocks TUI
from flocks.server.routes.permission import router as permission_router
from flocks.server.routes.question import router as question_router
# P3: TUI control routes for remote TUI control
from flocks.server.routes.tui import router as tui_router
# WebUI: Workflow routes
from flocks.server.routes.workflow import router as workflow_router, webhook_router as workflow_webhook_router
# WebUI: Skill & Command routes
from flocks.server.routes.skill import router as skill_router
from flocks.server.routes.hub import router as hub_router
# WebUI: Hook management routes
from flocks.server.routes.hooks import router as hooks_router
# Model management: Default model, Usage routes
from flocks.server.routes.default_model import router as default_model_router
from flocks.server.routes.usage import router as usage_router
from flocks.server.routes.custom_provider import router as custom_provider_router
# Onboarding routes
from flocks.server.routes.onboarding import router as onboarding_router
# Task Center routes
from flocks.server.routes.task_entities import router as task_entities_router
# Background Task routes (agent-spawned async tasks)
from flocks.server.routes.background_task import router as background_task_router
# Channel routes (webhook + status)
from flocks.server.routes.channel import router as channel_router
# Workspace routes (file manager)
from flocks.server.routes.workspace import router as workspace_router
# Update (self-upgrade)
from flocks.server.routes.update import router as update_router
# Log viewing
from flocks.server.routes.logs import router as logs_router
from flocks.server.routes.monitoring import router as monitoring_router
from flocks.server.routes.stats import router as stats_router
from flocks.server.routes.auth import router as auth_router
from flocks.server.routes.admin_users import router as admin_users_router
from flocks.server.routes.notifications import router as notifications_router
from flocks.server.routes.device import router as device_router
from flocks.server.routes.console_upgrade import router as console_upgrade_router
from flocks.server.routes.flockspro_license import router as flockspro_license_router
from flocks.server.routes.webui import router as webui_pages_router
from flocks.server.routes.contracts import router as contracts_router
# Original routes with /api/ prefix
app.include_router(health_router, prefix="/api", tags=["Health"])
app.include_router(session_router, prefix="/api/session", tags=["Session"])
app.include_router(provider_router, prefix="/api/provider", tags=["Provider"])
app.include_router(model_router, prefix="/api/model", tags=["Model"])
app.include_router(config_router, prefix="/api/config", tags=["Config"])
app.include_router(project_router, prefix="/api/project", tags=["Project"])
app.include_router(file_router, prefix="/api/file", tags=["File"])
app.include_router(message_router, prefix="/api/message", tags=["Message"])
app.include_router(agent_router, prefix="/api/agent", tags=["Agent"])
# Added in Batch 3
app.include_router(tool_router, prefix="/api/tools", tags=["Tool"])
app.include_router(pty_router, prefix="/api/pty", tags=["PTY"])
# Added in Batch 4
# Note: LSP status endpoint must be at root level for TUI compatibility
app.include_router(lsp_router, prefix="/api/lsp", tags=["LSP"])
# Added in Batch 5
# Note: MCP status endpoint must be at root level for TUI compatibility
app.include_router(mcp_router, prefix="/api/mcp", tags=["MCP"])
# WebUI: Workflow routes
app.include_router(workflow_router, prefix="/api", tags=["Workflow"])
app.include_router(workflow_webhook_router, tags=["WorkflowWebhook"])
# WebUI: Skill & Command routes
app.include_router(skill_router, prefix="/api", tags=["Skill"])
# WebUI: Hub routes
app.include_router(hub_router, prefix="/api", tags=["Hub"])
# WebUI: Hook management routes
app.include_router(hooks_router, prefix="/api/hooks", tags=["Hooks"])
# Model management: Default model routes
app.include_router(default_model_router, prefix="/api/default-model", tags=["DefaultModel"])
# Model management: Usage tracking routes
app.include_router(usage_router, prefix="/api/usage", tags=["Usage"])
# Custom provider and model management
app.include_router(custom_provider_router, prefix="/api/custom", tags=["CustomProvider"])
# Onboarding orchestration
app.include_router(onboarding_router, prefix="/api/onboarding", tags=["Onboarding"])
# WebUI: Event routes for SSE
app.include_router(event_router, prefix="/api/event", tags=["Event"])
# WebUI: Question reply routes (for production reverse proxies forwarding /api/*)
app.include_router(question_router, prefix="/api/question", tags=["Question"])
# Task Center
app.include_router(task_entities_router, prefix="/api", tags=["TaskV2"])
# Background Tasks (agent-spawned async tasks)
app.include_router(background_task_router, prefix="/api/background-task", tags=["BackgroundTask"])
# Channel (webhook callbacks + status)
app.include_router(channel_router, prefix="/api/channel", tags=["Channel"])
app.include_router(channel_router, prefix="/channel", tags=["Channel"])
# Workspace (file manager)
app.include_router(workspace_router, prefix="/api/workspace", tags=["Workspace"])
# Self-upgrade routes
app.include_router(update_router, prefix="/api/update", tags=["Update"])
# Log viewing routes
app.include_router(logs_router, prefix="/api/logs", tags=["Logs"])
app.include_router(stats_router, prefix="/api/stats", tags=["Stats"])
app.include_router(monitoring_router, prefix="/api/monitoring", tags=["Monitoring"])
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])
app.include_router(admin_users_router, prefix="/api/admin", tags=["Admin"])
app.include_router(notifications_router, prefix="/api/notifications", tags=["Notifications"])
# Device integration (named instances, SQL-backed)
app.include_router(device_router, prefix="/api/devices", tags=["Device"])
app.include_router(console_upgrade_router, prefix="/api/console", tags=["ConsoleUpgrade"])
app.include_router(webui_pages_router, prefix="/api", tags=["WebUI"])
app.include_router(contracts_router, prefix="/api", tags=["AccessContracts"])

# ============================================================
# TUI Compatible Routes (without /api/ prefix)
# These routes are needed for TUI client compatibility
# ============================================================

# Global routes (/global/*)
app.include_router(global_router, prefix="/global", tags=["Global"])

# Event routes (/event)
app.include_router(event_router, prefix="/event", tags=["Event"])

# Session routes (/session/*)
app.include_router(session_router, prefix="/session", tags=["Session"])

# Provider routes (/provider/*)
app.include_router(provider_router, prefix="/provider", tags=["Provider"])

# Config routes (/config/*)
app.include_router(config_router, prefix="/config", tags=["Config"])

# Project routes (/project/*)
app.include_router(project_router, prefix="/project", tags=["Project"])

# File routes (/file/*)
app.include_router(file_router, prefix="/file", tags=["File"])

# MCP routes (/mcp/*)
app.include_router(mcp_router, prefix="/mcp", tags=["MCP"])

# Agent routes (/agent/* and /app/agent for TUI)
app.include_router(agent_router, prefix="/agent", tags=["Agent"])
app.include_router(agent_router, prefix="/app/agent", tags=["App-Agent"])

# PTY routes (/pty/*)
app.include_router(pty_router, prefix="/pty", tags=["PTY"])

# LSP routes (/lsp/*)
app.include_router(lsp_router, prefix="/lsp", tags=["LSP"])

# Path routes (/path)
app.include_router(path_router, prefix="/path", tags=["Path"])

# VCS routes (/vcs)
app.include_router(vcs_router, prefix="/vcs", tags=["VCS"])

# Find routes (/find/*)
app.include_router(find_router, prefix="/find", tags=["Find"])

# Misc routes (various endpoints needed by TUI)
app.include_router(misc_router, tags=["Misc"])

# Permission routes (/permission)
app.include_router(permission_router, prefix="/permission", tags=["Permission"])
app.include_router(permission_router, prefix="/api/permission", tags=["Permission"])

# Question routes (/question)
app.include_router(question_router, prefix="/question", tags=["Question"])

# TUI control routes (/tui/*)
app.include_router(tui_router, prefix="/tui", tags=["TUI"])
app.include_router(auth_router, prefix="/auth", tags=["Auth"])
app.include_router(admin_users_router, prefix="/admin", tags=["Admin"])


def _load_installed_package_plugins() -> None:
    """Load package entry-point plugins before the app starts serving requests."""
    try:
        from flocks.plugin import PluginLoader

        PluginLoader.load_all(project_dir=Path.cwd())
        log.info("plugins.installed.loaded")
    except Exception as e:
        log.warning("plugins.installed.load_failed", {"error": str(e)})


def _route_registered(path: str, method: str) -> bool:
    target_method = method.upper()
    for route in app.routes:
        if getattr(route, "path", None) != path:
            continue
        methods = getattr(route, "methods", None) or set()
        if target_method in methods:
            return True
    return False


def _install_flockspro_license_fallback() -> None:
    status_registered = _route_registered("/api/flockspro/license/status", "GET")
    refresh_registered = _route_registered("/api/flockspro/license/refresh", "POST")
    if status_registered and refresh_registered:
        log.info("flockspro.license.fallback.skipped", {
            "status_registered": status_registered,
            "refresh_registered": refresh_registered,
        })
        return
    app.include_router(flockspro_license_router, prefix="/api/flockspro/license", tags=["FlocksProLicense"])
    log.info("flockspro.license.fallback.installed")


_load_installed_package_plugins()
_install_flockspro_license_fallback()


@app.get("/", tags=["Root"])
async def root():
    """Return basic API information."""
    return {
        "name": "Flocks API",
        "version": "0.2.0",
        "status": "running",
        "docs": "/docs",
    }


# Server information
class ServerInfo:
    """Server information namespace"""
    
    _instance: Optional["ServerInfo"] = None
    
    def __init__(self):
        self.host = "127.0.0.1"
        self.port = 8000
        self.url = f"http://{self.host}:{self.port}"
    
    @classmethod
    def get(cls) -> "ServerInfo":
        """Get server info singleton"""
        if cls._instance is None:
            cls._instance = ServerInfo()
        return cls._instance
    
    def configure(self, host: str, port: int) -> None:
        """Configure server address"""
        self.host = host
        self.port = port
        self.url = f"http://{host}:{port}"

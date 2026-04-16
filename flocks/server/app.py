"""
FastAPI application for Flocks server

Main HTTP API server for AI-Native SecOps Platform
"""

import asyncio
import os
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from flocks.utils.log import Log, LogLevel
from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.utils.langfuse import initialize as init_observability, shutdown as shutdown_observability
from flocks.auth.service import AuthService
from flocks.server.auth import apply_auth_for_request, clear_auth_context

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
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application lifecycle"""
    # Ensure file logging when server is started without CLI (e.g. uvicorn app:app)
    if Log._writer is None:
        await Log.init(print=False, dev=False, level=LogLevel.INFO)

    log = Log.create(service="server")

    # Startup
    log.info("server.startup", {"version": "0.2.0"})
    try:
        from flocks.updater.updater import cleanup_replaced_files

        await asyncio.to_thread(cleanup_replaced_files)
        log.info("updater.leftovers.cleaned")
    except Exception as e:
        log.warning("updater.leftovers.cleanup_failed", {"error": str(e)})

    try:
        from flocks.updater.updater import _get_repo_root, _refresh_global_cli_entry

        await asyncio.to_thread(_refresh_global_cli_entry, _get_repo_root())
        log.info("cli.global_entry.refreshed")
    except Exception as e:
        log.warning("cli.global_entry.refresh_failed", {"error": str(e)})

    try:
        init_observability()
        log.info("observability.initialized")
    except Exception as e:
        log.warning("observability.init_failed", {"error": str(e)})
    
    # Ensure config files exist (copy from examples if needed)
    try:
        from flocks.config.config_writer import ensure_config_files
        ensure_config_files()
        log.info("config.files.checked")
    except Exception as e:
        log.warning("config.files.check_failed", {"error": str(e)})
    
    # Initialize storage
    await Storage.init()
    log.info("storage.initialized")

    # Initialize local auth/account tables
    await AuthService.init()
    log.info("auth.initialized")

    # Best-effort migration: old sessions default to admin ownership.
    try:
        if await AuthService.has_users():
            users = await AuthService.list_users()
            admin = next((u for u in users if u.role == "admin"), None)
            if admin:
                await AuthService.migrate_legacy_sessions_to_admin(admin.id)
    except Exception as e:
        log.warning("auth.legacy_sessions.migration_failed", {"error": str(e)})
    
    # Setup question handler for real user interaction
    from flocks.tool.question_handler import setup_api_question_handler
    setup_api_question_handler()
    log.info("question_handler.initialized")
    
    # Register built-in hooks if memory is enabled
    try:
        config = await Config.get()
        if config.memory.enabled:
            from flocks.hooks.builtin import register_builtin_hooks
            register_builtin_hooks()
            log.info("hooks.registered")
    except Exception as e:
        # Hook registration failure should not stop server startup
        log.warn("hooks.register_failed", {"error": str(e)})

    # Migrate env-var credentials to .secret.json (idempotent)
    try:
        from flocks.provider.credential import migrate_env_credentials
        migrated = migrate_env_credentials()
        if migrated > 0:
            log.info("credential.env_migration.done", {"migrated": migrated})
    except Exception as e:
        log.warning("credential.env_migration.failed", {"error": str(e)})

    # Sync new catalog models into flocks.json for existing providers (idempotent)
    try:
        from flocks.provider.model_catalog import sync_catalog_models_to_config
        synced = sync_catalog_models_to_config()
        if synced > 0:
            log.info("catalog.model_sync.done", {"models_added": synced})
    except Exception as e:
        log.warning("catalog.model_sync.failed", {"error": str(e)})

    # Load custom providers from flocks.json into runtime
    try:
        from flocks.server.routes.custom_provider import load_custom_providers_on_startup
        await load_custom_providers_on_startup()
        log.info("custom_providers.loaded")
    except Exception as e:
        log.warning("custom_providers.load.failed", {"error": str(e)})

    # Initialize MCP servers on startup so installed servers reconnect automatically
    # after a service restart, without requiring manual UI reconnection.
    try:
        from flocks.mcp import MCP
        await MCP.init()
        log.info("mcp.initialized")
    except Exception as e:
        log.warning("mcp.init_failed", {"error": str(e)})

    # Sync workflows from .flocks/workflow/ filesystem into Storage
    try:
        from flocks.server.routes.workflow import sync_workflows_from_filesystem
        imported = await sync_workflows_from_filesystem()
        log.info("workflow.sync.done", {"imported": imported})
    except Exception as e:
        log.warning("workflow.sync.failed", {"error": str(e)})

    # Start Task Center (scheduler + queue executor)
    try:
        from flocks.task.manager import TaskManager
        await TaskManager.start()
        log.info("task_manager.started")
    except Exception as e:
        from flocks.task.manager import TaskManager
        TaskManager.mark_start_failed(e)
        log.warning("task_manager.start.failed", {"error": str(e)})

    # Seed built-in scheduled tasks from .flocks/plugins/tasks/*.json (idempotent)
    try:
        from flocks.task.plugin import seed_tasks_from_plugin
        seeded = await seed_tasks_from_plugin()
        if seeded:
            log.info("task.plugin.seeded", {"count": seeded})
    except Exception as e:
        log.warning("task.plugin.seed_failed", {"error": str(e)})

    # Start Skill file watcher (auto-invalidate cache on SKILL.md changes)
    try:
        from flocks.skill.skill import Skill
        Skill.start_watcher()
        log.info("skill.watcher.initialized")
    except Exception as e:
        log.warning("skill.watcher.init_failed", {"error": str(e)})

    # Start Agent file watcher (auto-invalidate cache on plugin agent changes)
    try:
        from flocks.agent.registry import Agent
        Agent.start_watcher()
        log.info("agent.watcher.initialized")
    except Exception as e:
        log.warning("agent.watcher.init_failed", {"error": str(e)})

    # Start Tool file watcher (auto-reload plugin tools on file changes)
    try:
        from flocks.tool.registry import ToolRegistry
        ToolRegistry.start_watcher()
        log.info("tool.watcher.initialized")
    except Exception as e:
        log.warning("tool.watcher.init_failed", {"error": str(e)})

    # Start Channel Gateway (connect enabled IM channels)
    try:
        from flocks.channel.gateway.manager import default_manager
        await default_manager.start_all()
        log.info("channel.gateway.started")
    except Exception as e:
        log.warning("channel.gateway.start_failed", {"error": str(e)})

    try:
        from flocks.updater.updater import recover_upgrade_state

        await asyncio.to_thread(recover_upgrade_state)
        log.info("updater.recovery.checked")
    except Exception as e:
        log.warning("updater.recovery.failed", {"error": str(e)})

    yield

    # --- Graceful shutdown: notify SSE clients FIRST ---
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


# CORS Configuration
def configure_cors() -> None:
    """Configure CORS middleware"""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # TODO: Make configurable
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# Instance Context Middleware
@app.middleware("http")
async def instance_context_middleware(request: Request, call_next):
    """
    Provide Instance context for all requests (except global routes)
    
    Middleware that wraps all routes with Instance.provide().
    Gets directory from:
    1. Query parameter 'directory'
    2. Header 'x-flocks-directory'
    3. Falls back to current working directory
    """
    import os
    from urllib.parse import unquote
    from flocks.project.instance import Instance
    from flocks.project.bootstrap import instance_bootstrap
    
    # Skip instance context for global routes, static files, and simple endpoints
    skip_prefixes = {
        "/global", "/docs", "/redoc", "/openapi.json", "/health",
        "/path", "/permission", "/question", "/tui",
    }
    
    if any(request.url.path.startswith(prefix) for prefix in skip_prefixes):
        return await call_next(request)
    
    # Get directory from query param, header, or use cwd
    # Support both x-flocks-directory (native) and x-flocks-directory (TUI compatibility)
    directory = request.query_params.get("directory")
    if not directory:
        directory = request.headers.get("x-flocks-directory")
    if not directory:
        directory = request.headers.get("x-flocks-directory")
    if not directory:
        directory = os.getcwd()
    
    # Decode URL-encoded directory
    try:
        directory = unquote(directory)
    except Exception:
        pass  # Use original value if decode fails
    
    # Provide instance context for the request
    async def handle_request():
        return await call_next(request)
    
    return await Instance.provide(
        directory=directory,
        init=instance_bootstrap,
        fn=handle_request
    )


# Request Logging Middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all incoming requests"""
    # Skip logging for certain paths
    skip_paths = {"/health", "/docs", "/redoc", "/openapi.json"}
    
    if request.url.path not in skip_paths:
        log.info("request.start", {
            "method": request.method,
            "path": request.url.path,
            "client": request.client.host if request.client else None,
        })
    
    # Time the request
    timer = log.time("request.complete", {
        "method": request.method,
        "path": request.url.path,
    })
    
    with timer:
        response = await call_next(request)
    
    if request.url.path not in skip_paths:
        log.info("request.complete", {
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
        })
    
    return response


@app.middleware("http")
async def auth_guard_middleware(request: Request, call_next):
    """Guard requests with local account auth, except public endpoints."""
    try:
        _blocked, token, _user = await apply_auth_for_request(request)
    except StarletteHTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": "AuthError", "message": exc.detail},
        )
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "AuthError", "message": str(exc)},
        )

    try:
        return await call_next(request)
    finally:
        clear_auth_context(token)


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
            "error": type(exc).__name__,
            "message": str(exc),
            "traceback": tb,
        }
    )


# Configure CORS
configure_cors()


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
from flocks.server.routes.workflow import router as workflow_router
# WebUI: Skill & Command routes
from flocks.server.routes.skill import router as skill_router
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
from flocks.server.routes.auth import router as auth_router
from flocks.server.routes.admin_users import router as admin_users_router
from flocks.server.routes.cloud_account import router as cloud_account_router
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
# WebUI: Skill & Command routes
app.include_router(skill_router, prefix="/api", tags=["Skill"])
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
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])
app.include_router(admin_users_router, prefix="/api/admin", tags=["Admin"])
app.include_router(cloud_account_router, prefix="/api/cloud-account", tags=["CloudAccount"])

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

# Question routes (/question)
app.include_router(question_router, prefix="/question", tags=["Question"])

# TUI control routes (/tui/*)
app.include_router(tui_router, prefix="/tui", tags=["TUI"])
app.include_router(auth_router, prefix="/auth", tags=["Auth"])
app.include_router(admin_users_router, prefix="/admin", tags=["Admin"])
app.include_router(cloud_account_router, prefix="/cloud-account", tags=["CloudAccount"])


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

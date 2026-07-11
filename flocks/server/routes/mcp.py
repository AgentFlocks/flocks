"""
MCP (Model Context Protocol) routes

Routes for MCP server management, authentication, and tool access

All routes run within Instance context provided by the middleware.
MCP state is instance-scoped for project isolation.
"""

import asyncio
import hashlib
import json
from typing import Dict, Optional, List, Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from flocks.mcp import (
    MCP,
    get_manager,
    McpStatus,
    McpStatusInfo,
    McpToolDef,
    McpResource,
    McpServerInfo,
)
from flocks.mcp.catalog import McpCatalog
from flocks.mcp.auth import McpAuth
from flocks.mcp.installer import preflight_install, preflight_uninstall
from flocks.mcp.utils import (
    LOCAL_MCP_TYPES,
    REMOTE_MCP_TYPES,
    extract_api_key_from_mcp_url,
    extract_auth_value_from_mcp_config,
    extract_sensitive_headers_from_mcp_config,
    get_connect_block_reason,
    mask_sensitive_mcp_config_for_frontend,
    normalize_mcp_config,
    normalize_mcp_config_aliases,
    restore_masked_mcp_config_secrets,
    should_allow_unconnected_add,
    should_skip_connect_on_add,
)
from flocks.config.config import Config
from flocks.config.config_writer import ConfigWriter
from flocks.security.action_gateway import (
    ActionDecisionError,
    SecurityAction,
    execute_action,
    enforce_action_decision,
    run_before_action,
)
from flocks.utils.log import Log

router = APIRouter()
log = Log.create(service="routes.mcp")


def _mcp_action_input(name: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build policy context without forwarding credential values to action hooks."""
    normalized = dict(config or {})
    command = normalized.get("command")
    if isinstance(command, list):
        command_name = str(command[0]) if command else ""
    else:
        command_name = str(command or "")
    try:
        encoded = json.dumps(normalized, sort_keys=True, default=str).encode("utf-8")
        config_hash = hashlib.sha256(encoded).hexdigest()
    except (TypeError, ValueError):
        config_hash = None
    return {
        "name": name,
        "transport": str(normalized.get("type") or normalized.get("transport") or ""),
        "command": command_name,
        "config_hash": config_hash,
    }


async def _execute_mcp_action(
    *,
    action: str,
    name: str,
    config: Optional[Dict[str, Any]],
    effect,
):
    """Run an MCP side effect through the OSS-neutral ActionGateway."""
    try:
        return await execute_action(
            SecurityAction(
                action=action,
                resource={"type": "mcp_server", "id": name},
                canonical_input=_mcp_action_input(name, config),
                execution_domain="control_plane",
                metadata={"entry": "api"},
            ),
            effect,
        )
    except ActionDecisionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _to_frontend_mcp_config(server_config: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize backend transport names for the frontend form."""
    server_config = mask_sensitive_mcp_config_for_frontend(server_config)
    transport = str(server_config.get("type", "sse")).strip().lower()
    if transport in LOCAL_MCP_TYPES:
        transport = "stdio"
    elif transport in REMOTE_MCP_TYPES:
        transport = "sse"
    command_value = server_config.get("command")
    args_value = server_config.get("args")
    if transport == "stdio":
        command_parts: List[str]
        if isinstance(command_value, list):
            command_parts = [str(item).strip() for item in command_value if str(item).strip()]
        elif isinstance(command_value, str):
            command_parts = [command_value.strip()] if command_value.strip() else []
        else:
            command_parts = []

        extra_args: List[str]
        if isinstance(args_value, list):
            extra_args = [str(item).strip() for item in args_value if str(item).strip()]
        elif isinstance(args_value, str):
            extra_args = [line.strip() for line in args_value.splitlines() if line.strip()]
        else:
            extra_args = []

        command = command_parts[0] if command_parts else ""
        args = [*command_parts[1:], *extra_args]
    else:
        command = command_value
        args = args_value
    return {
        "type": transport,
        "url": server_config.get("url"),
        "command": command,
        "args": args,
        "transport": server_config.get("transport", "auto"),
        "headers": server_config.get("headers"),
        "auth": server_config.get("auth"),
        "oauth": server_config.get("oauth"),
    }

async def _load_mcp_server_config(name: str) -> Optional[Dict[str, Any]]:
    """Load a server config with secrets resolved for runtime connect/test paths."""
    config = await Config.get()
    mcp_config = getattr(config, "mcp", None) or {}

    if isinstance(mcp_config, dict):
        server_config = mcp_config.get(name)
    else:
        server_config = (
            getattr(mcp_config, name, None)
            if hasattr(mcp_config, name)
            else mcp_config.get(name)
            if hasattr(mcp_config, "get")
            else None
        )

    if server_config is None:
        server_config = ConfigWriter.get_mcp_server(name)

    if hasattr(server_config, "model_dump"):
        server_config = server_config.model_dump()
    elif hasattr(server_config, "dict"):
        server_config = server_config.dict()
    elif server_config is not None and not isinstance(server_config, dict):
        server_config = dict(server_config)

    if not isinstance(server_config, dict):
        return None
    return normalize_mcp_config(server_config)


def _load_raw_mcp_server_config(name: str) -> Optional[Dict[str, Any]]:
    """Load a server config without resolving secret placeholders."""
    server_config = ConfigWriter.get_mcp_server(name)
    if hasattr(server_config, "model_dump"):
        server_config = server_config.model_dump()
    elif hasattr(server_config, "dict"):
        server_config = server_config.dict()
    elif server_config is not None and not isinstance(server_config, dict):
        server_config = dict(server_config)

    if not isinstance(server_config, dict):
        return None
    return normalize_mcp_config(server_config)


async def _build_mcp_status_response() -> Dict[str, Any]:
    """Merge runtime state with configured-but-not-connected MCP servers."""
    status = await MCP.status()
    result = {name: info.model_dump() for name, info in status.items()}

    configured = ConfigWriter.list_mcp_servers()
    for name, server_config in configured.items():
        if not isinstance(server_config, dict):
            continue
        if name in result:
            continue
        if server_config.get("enabled", True):
            result[name] = McpStatusInfo(status=McpStatus.DISCONNECTED).model_dump()
        else:
            result[name] = McpStatusInfo(status=McpStatus.DISABLED).model_dump()

    return result


def _persist_mcp_server_config(name: str, config: Dict[str, Any]) -> None:
    """Persist MCP config to both runtime config and canonical YAML."""
    ConfigWriter.add_mcp_server(name, config)

    from flocks.tool.tool_loader import save_mcp_config
    save_mcp_config(name, config)


def _prepare_mcp_config_for_save(name: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize config and move any plain-text remote secrets into SecretManager."""
    clean_config = extract_api_key_from_mcp_url(name, normalize_mcp_config(config))
    clean_config = extract_auth_value_from_mcp_config(name, clean_config)
    clean_config = extract_sensitive_headers_from_mcp_config(name, clean_config)
    return clean_config


# Request/Response models

class McpAddRequest(BaseModel):
    """Request to add an MCP server"""
    name: str = Field(..., description="Server name")
    config: Dict[str, Any] = Field(..., description="Server configuration (McpLocalConfig or McpRemoteConfig)")


class McpAuthCallbackRequest(BaseModel):
    """OAuth callback request"""
    code: str = Field(..., description="Authorization code from OAuth callback")


class McpAuthResponse(BaseModel):
    """OAuth auth start response"""
    authorizationUrl: str = Field(..., description="URL to open in browser for authorization", alias="authorizationUrl")


# Status endpoints

@router.get(
    "",
    response_model=Dict[str, McpStatusInfo],
    summary="Get MCP status",
    description="Get the status of all Model Context Protocol (MCP) servers.",
    operation_id="mcp.status"
)
async def get_mcp_status():
    """
    Get status of all MCP servers.

    Merges in-memory runtime status with servers configured in flocks.json
    that have not been connected yet (shown as DISCONNECTED). This ensures
    that servers added by Rex via ConfigWriter appear in the UI immediately.

    Returns dictionary of server names to status info.
    Runs within Instance context provided by middleware.
    """
    try:
        return await _build_mcp_status_response()
    except Exception as e:
        log.error("mcp.status.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "",
    response_model=Dict[str, McpStatusInfo],
    summary="Add MCP server",
    description="Dynamically add a new Model Context Protocol (MCP) server to the system.",
    operation_id="mcp.add"
)
async def add_mcp_server(request: McpAddRequest):
    """
    Add a new MCP server and persist to both flocks.json and
    ``~/.flocks/plugins/tools/mcp/``.

    Returns the updated status of ALL MCP servers (not just the new one).
    """
    try:
        decision = await run_before_action(
            SecurityAction(
                action="configure",
                resource={"type": "mcp_server", "id": request.name},
                canonical_input=request.config,
                execution_domain="control_plane",
                metadata={"entry": "api"},
            )
        )
        enforce_action_decision(decision)

        # Extract any API key embedded in the URL and move it to .secret.json.
        # The URL is rewritten to use a {secret:...} reference so that the
        # plain-text credential is never written to flocks.json.
        clean_config = _prepare_mcp_config_for_save(request.name, request.config)

        if should_skip_connect_on_add(clean_config):
            _persist_mcp_server_config(request.name, clean_config)
            log.info("mcp.add.deferred", {
                "name": request.name,
                "reason": "credentials_not_configured",
            })
            return await _build_mcp_status_response()

        # Connect to server
        success = await MCP.connect(request.name, clean_config)
        if not success:
            # Check status for error detail
            status = await MCP.status()
            server_status = status.get(request.name)
            error_detail = getattr(server_status, 'error', None) if server_status else None
            if should_allow_unconnected_add(clean_config, error_detail):
                # Drop the transient FAILED runtime state so the new entry appears
                # as a persisted-but-disconnected server until credentials are added.
                await MCP.remove(request.name)
                _persist_mcp_server_config(request.name, clean_config)
                log.info("mcp.add.deferred", {
                    "name": request.name,
                    "reason": error_detail or "auth_pending",
                })
                return await _build_mcp_status_response()

            raise ValueError(
                error_detail or f"Failed to connect to server: {request.name}"
            )

        # Persist to flocks.json (runtime config) — use clean_config so that
        # any extracted API key is stored as a {secret:...} reference.
        _persist_mcp_server_config(request.name, clean_config)

        log.info("mcp.add.persisted", {"name": request.name})

        # Return updated status, including configured-but-not-connected entries.
        return await _build_mcp_status_response()
    except ActionDecisionError:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("mcp.add.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


class McpTestRequest(BaseModel):
    """Request to test an MCP connection without saving"""
    name: str = Field(..., description="Server name (used as temp identifier)")
    config: Dict[str, Any] = Field(..., description="Server configuration to test")


class McpUpdateRequest(BaseModel):
    """Request to update an existing MCP server configuration."""
    config: Dict[str, Any] = Field(..., description="Partial or full MCP server configuration")


@router.post(
    "/test",
    response_model=Dict[str, Any],
    summary="Test MCP connection",
    description="Test an MCP server connection without saving to configuration.",
    operation_id="mcp.test"
)
async def test_mcp_connection(request: McpTestRequest):
    """
    Test MCP server connectivity without persisting configuration.

    Connects, refreshes tools, then disconnects. Returns success/failure with tool count.
    """
    import time
    start = time.time()
    temp_name = f"{request.name}__test__"
    try:
        normalized_config = normalize_mcp_config(request.config)
        success = await _execute_mcp_action(
            action="mcp_test",
            name=request.name,
            config=normalized_config,
            effect=lambda: MCP.connect(temp_name, normalized_config),
        )
        if not success:
            status = await MCP.status()
            server_status = status.get(temp_name)
            error_detail = getattr(server_status, "error", None) if server_status else None
            return {
                "success": False,
                "message": error_detail or f"无法连接到服务 '{request.name}'",
            }

        count = await MCP.refresh_tools(temp_name)
        latency = int((time.time() - start) * 1000)

        return {
            "success": True,
            "message": f"连接成功，找到 {count} 个工具。",
            "latency_ms": latency,
            "tools_count": count,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"连接测试失败：{str(e)}",
            "error": str(e),
        }
    finally:
        # Fully remove from memory after test so the server doesn't appear in listings
        try:
            await MCP.remove(temp_name)
        except Exception as e:
            log.warn("mcp.test.cleanup_failed", {"server": temp_name, "error": str(e)})


@router.post(
    "/{name}/test",
    response_model=Dict[str, Any],
    summary="Test existing MCP config with overrides",
    description="Test an existing MCP server using saved config merged with temporary overrides.",
    operation_id="mcp.test_existing"
)
async def test_existing_mcp_connection(name: str, request: McpUpdateRequest):
    """Test a configured MCP server after merging temporary config overrides."""
    temp_name = f"{name}__test__"
    import time
    start = time.time()
    try:
        base_config = await _load_mcp_server_config(name)
        if not base_config:
            raise HTTPException(status_code=404, detail=f"MCP server not found: {name}")

        merged_config = dict(base_config)
        merged_config.update(normalize_mcp_config(request.config))
        merged_config = restore_masked_mcp_config_secrets(base_config, merged_config)

        success = await _execute_mcp_action(
            action="mcp_test",
            name=name,
            config=merged_config,
            effect=lambda: MCP.connect(temp_name, merged_config),
        )
        if not success:
            status = await MCP.status()
            server_status = status.get(temp_name)
            error_detail = getattr(server_status, "error", None) if server_status else None
            return {
                "success": False,
                "message": error_detail or f"无法连接到服务 '{name}'",
            }

        count = await MCP.refresh_tools(temp_name)
        latency = int((time.time() - start) * 1000)
        return {
            "success": True,
            "message": f"连接成功，找到 {count} 个工具。",
            "latency_ms": latency,
            "tools_count": count,
        }
    except HTTPException:
        raise
    except Exception as e:
        return {
            "success": False,
            "message": f"连接测试失败：{str(e)}",
            "error": str(e),
        }
    finally:
        try:
            await MCP.remove(temp_name)
        except Exception as e:
            log.warn("mcp.test_existing.cleanup_failed", {"server": temp_name, "error": str(e)})


@router.delete(
    "/{name}",
    response_model=Dict[str, bool],
    summary="Remove MCP server",
    description="Disconnect and remove an MCP server from the system and configuration.",
    operation_id="mcp.remove"
)
async def remove_mcp_server(name: str):
    """
    Remove an MCP server.

    Purges all in-memory state (tools, status, client) and deletes the
    server from flocks.json so it never reappears without a restart.
    Returns {"success": true} on success.
    """
    try:
        decision = await run_before_action(
            SecurityAction(
                action="delete",
                resource={"type": "mcp_server", "id": name},
                canonical_input={},
                execution_domain="control_plane",
                metadata={"entry": "api"},
            )
        )
        enforce_action_decision(decision)

        # Try to remove from persistent config (may not exist if server was only in memory)
        removed_from_config = ConfigWriter.remove_mcp_server(name)

        # Always purge in-memory state regardless of config presence
        status = await MCP.status()
        in_memory = name in status
        if in_memory:
            await MCP.remove(name)

        if not removed_from_config and not in_memory:
            raise HTTPException(
                status_code=404,
                detail=f"MCP server '{name}' not found"
            )

        # Clean up installed npm packages under ~/.flocks/mcp if this is a catalog entry
        catalog = McpCatalog.get()
        entry = catalog.get_entry(name)
        if entry:
            try:
                await preflight_uninstall(entry)
            except Exception as uninstall_err:
                log.warn("mcp.remove.uninstall_failed", {"name": name, "error": str(uninstall_err)})

        # Clean up canonical YAML config if present
        try:
            from flocks.tool.tool_loader import delete_mcp_config
            delete_mcp_config(name)
        except Exception as yaml_err:
            log.warn("mcp.remove.yaml_delete_failed", {"name": name, "error": str(yaml_err)})

        log.info("mcp.remove.success", {"name": name, "from_config": removed_from_config, "from_memory": in_memory})
        return {"success": True}
    except ActionDecisionError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        log.error("mcp.remove.error", {"name": name, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.put(
    "/{name}",
    response_model=Dict[str, Any],
    summary="Update MCP server config",
    description="Update and persist an existing MCP server configuration.",
    operation_id="mcp.update"
)
async def update_mcp_server(name: str, request: McpUpdateRequest):
    """Update an existing MCP server configuration and clear stale runtime state."""
    try:
        existing_config = _load_raw_mcp_server_config(name)
        if not existing_config:
            raise HTTPException(status_code=404, detail=f"MCP server not found: {name}")

        updated_config = dict(existing_config)
        updated_config.update(normalize_mcp_config(request.config))
        updated_config = restore_masked_mcp_config_secrets(
            existing_config, updated_config
        )
        decision = await run_before_action(
            SecurityAction(
                action="configure",
                resource={"type": "mcp_server", "id": name},
                canonical_input=updated_config,
                execution_domain="control_plane",
                metadata={"entry": "api"},
            )
        )
        enforce_action_decision(decision)
        clean_config = _prepare_mcp_config_for_save(name, updated_config)
        _persist_mcp_server_config(name, clean_config)

        status = await MCP.status()
        previous_status = status.get(name)
        was_connected = (
            previous_status is not None
            and previous_status.status == McpStatus.CONNECTED
        )
        # Reconnect whenever the user just saved a config that asks the
        # server to be enabled AND the config is complete enough to try.
        # This covers three real flows:
        #
        #   * config change while already connected — pre-existing case;
        #   * first enable from a previously-disabled/never-seen server —
        #     the runtime ``status`` dict is empty for that name;
        #   * fixing credentials after a failed connect — runtime state
        #     was FAILED/DISCONNECTED and the user just saved the corrected
        #     config; they expect a re-try without an extra click.
        #
        # ``get_connect_block_reason`` short-circuits when the config is
        # still incomplete (e.g. ``{secret:...}`` placeholder with no value
        # in the secret store) so we never spin up doomed connect attempts.
        becoming_enabled = clean_config.get("enabled", True) is not False
        should_reconnect = (
            becoming_enabled
            and not get_connect_block_reason(clean_config)
        )

        if previous_status is not None:
            await MCP.remove(name)

        reconnected = False
        reconnect_error: Optional[str] = None
        if should_reconnect:
            reconnect_timeout_seconds = max(float(clean_config.get("timeout", 30.0) or 30.0), 1.0) + 2.0
            try:
                reconnected = await asyncio.wait_for(
                    MCP.connect(name, clean_config),
                    timeout=reconnect_timeout_seconds,
                )
            except asyncio.TimeoutError:
                reconnect_error = (
                    f"Connection timed out while reconnecting MCP server: {name}"
                )
            except Exception as exc:
                reconnect_error = str(exc)
            if not reconnected and reconnect_error is None:
                reconnect_status = (await MCP.status()).get(name)
                reconnect_error = (
                    getattr(reconnect_status, "error", None)
                    if reconnect_status is not None
                    else None
                ) or f"Failed to reconnect MCP server: {name}"

        message = f"MCP server '{name}' updated successfully."
        if should_reconnect and reconnected:
            message = f"MCP server '{name}' updated and reconnected successfully."
        elif should_reconnect and reconnect_error:
            message = (
                f"MCP server '{name}' updated successfully, but reconnect failed: "
                f"{reconnect_error}"
            )

        return {
            "success": True,
            "message": message,
            "config": _to_frontend_mcp_config(clean_config),
            "reconnected": reconnected,
            "reconnect_error": reconnect_error,
        }
    except ActionDecisionError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        log.error("mcp.update.error", {"name": name, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{name}",
    summary="Get MCP server info",
    description="Get detailed information about a specific MCP server, including config if present.",
    operation_id="mcp.info"
)
async def get_mcp_server_info(name: str):
    """Get info for a specific MCP server, with config from flocks.json when available."""
    try:
        info = await MCP.get_server_info(name)
        server_config = _load_raw_mcp_server_config(name)
        if not info and not server_config:
            raise HTTPException(status_code=404, detail=f"MCP server not found: {name}")
        if not info:
            info = McpServerInfo(
                name=name,
                status=McpStatusInfo(status=McpStatus.DISCONNECTED),
                tools=[],
                resources=[],
            )
        result = info.model_dump()
        if isinstance(server_config, dict):
            result["config"] = _to_frontend_mcp_config(server_config)
        else:
            result["config"] = None
        return result
    except HTTPException:
        raise
    except Exception as e:
        log.error("mcp.info.error", {"name": name, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


# Connection endpoints

@router.post(
    "/{name}/connect",
    response_model=bool,
    summary="Connect MCP server",
    description="Connect to an MCP server.",
    operation_id="mcp.connect"
)
async def connect_mcp_server(name: str):
    """Connect to an MCP server - returns true on success"""
    try:
        server_config = await _load_mcp_server_config(name)
        if not server_config:
            raise ValueError(f"Server not found in config: {name}")
        if server_config.get("enabled", True) is False:
            raise HTTPException(
                status_code=400,
                detail=f"MCP server '{name}' is disabled. Enable it before connecting.",
            )

        blocked_reason = get_connect_block_reason(server_config)
        if blocked_reason:
            raise HTTPException(status_code=400, detail=blocked_reason)

        timeout_seconds = max(float(server_config.get("timeout", 30.0) or 30.0), 1.0) + 2.0
        success = await _execute_mcp_action(
            action="mcp_connect",
            name=name,
            config=server_config,
            effect=lambda: asyncio.wait_for(
                MCP.connect(name, server_config),
                timeout=timeout_seconds,
            ),
        )
        if not success:
            status_info = get_manager()._status.get(name)
            error_msg = (status_info.error if status_info else None) or f"Failed to connect to server: {name}"
            raise HTTPException(status_code=500, detail=error_msg)
        return success
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Connection timed out while connecting to MCP server: {name}",
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("mcp.connect.error", {"name": name, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{name}/disconnect",
    response_model=bool,
    summary="Disconnect MCP server",
    description="Disconnect from an MCP server.",
    operation_id="mcp.disconnect"
)
async def disconnect_mcp_server(name: str):
    """Disconnect from an MCP server - returns true on success"""
    try:
        success = await MCP.disconnect(name)
        return success
    except Exception as e:
        log.error("mcp.disconnect.error", {"name": name, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


# OAuth endpoints (P1 feature - placeholders for now)

@router.post(
    "/{name}/auth",
    response_model=McpAuthResponse,
    summary="Start MCP OAuth",
    description="Start OAuth authentication flow for a Model Context Protocol (MCP) server.",
    operation_id="mcp.auth.start"
)
async def start_mcp_auth(name: str):
    """
    Start OAuth authentication flow
    
    Note: P1 feature - not yet implemented
    """
    return JSONResponse(
        status_code=501,
        content={"error": "OAuth authentication not yet implemented (P1 feature)"}
    )


@router.delete(
    "/{name}/auth",
    response_model=Dict[str, bool],
    summary="Remove MCP OAuth",
    description="Remove OAuth credentials for an MCP server.",
    operation_id="mcp.auth.remove"
)
async def remove_mcp_auth(name: str):
    """Remove OAuth credentials - returns {"success": true}"""
    try:
        await McpAuth.remove(name)
        return {"success": True}
    except Exception as e:
        log.error("mcp.auth.remove.error", {"name": name, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


# Tools endpoints

@router.get(
    "/tools",
    response_model=List[str],
    summary="Get MCP tools",
    description="Get all available tools from connected MCP servers.",
    operation_id="mcp.tools"
)
async def get_mcp_tools():
    """Get all available MCP tools (returns tool names)"""
    try:
        from flocks.mcp import McpToolRegistry
        
        tool_names = []
        for server_name in McpToolRegistry.get_all_servers():
            tool_names.extend(McpToolRegistry.get_server_tools(server_name))
        
        return sorted(tool_names)
    except Exception as e:
        log.error("mcp.tools.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{name}/tools",
    response_model=List[McpToolDef],
    summary="Get server tools",
    description="Get tools from a specific MCP server."
)
async def get_server_tools(name: str):
    """Get tools from a specific server"""
    try:
        info = await MCP.get_server_info(name)
        if not info:
            raise HTTPException(status_code=404, detail=f"MCP server not found: {name}")
        return info.tools
    except HTTPException:
        raise
    except Exception as e:
        log.error("mcp.server.tools.error", {"name": name, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


# Resources endpoints

@router.get(
    "/resources",
    response_model=Dict[str, McpResource],
    summary="Get MCP resources",
    description="Get all available resources from connected MCP servers.",
    operation_id="mcp.resources"
)
async def get_mcp_resources():
    """Get all available MCP resources"""
    try:
        resources = await MCP.resources()
        return {name: resource.model_dump() for name, resource in resources.items()}
    except Exception as e:
        log.error("mcp.resources.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{name}/resources",
    response_model=List[McpResource],
    summary="Get server resources",
    description="Get resources from a specific MCP server."
)
async def get_server_resources(name: str):
    """Get resources from a specific server"""
    try:
        info = await MCP.get_server_info(name)
        if not info:
            raise HTTPException(status_code=404, detail=f"MCP server not found: {name}")
        return info.resources
    except HTTPException:
        raise
    except Exception as e:
        log.error("mcp.server.resources.error", {"name": name, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


# Refresh endpoint

@router.post(
    "/{name}/refresh",
    response_model=int,
    summary="Refresh MCP tools",
    description="Refresh tools from an MCP server.",
    operation_id="mcp.refresh"
)
async def refresh_mcp_tools(name: str):
    """Refresh tools from a server - returns count of tools registered"""
    try:
        count = await _execute_mcp_action(
            action="mcp_refresh",
            name=name,
            config=None,
            effect=lambda: MCP.refresh_tools(name),
        )
        return count
    except Exception as e:
        log.error("mcp.refresh.error", {"name": name, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


# Credentials endpoints

class McpCredentialRequest(BaseModel):
    """Request to set MCP server credentials.

    secret_id: The key used in .secret.json (e.g., "threatbook_mcp_key").
               If not provided, auto-generated as "{name}_mcp_key".
    api_key:   The secret value to store.
    """
    secret_id: Optional[str] = Field(None, description="Secret ID in .secret.json")
    api_key: Optional[str] = Field(None, description="API key / credential value")


class McpCredentialResponse(BaseModel):
    """Response with masked credential info"""
    secret_id: Optional[str] = None
    api_key_masked: Optional[str] = None
    has_credential: bool


@router.get(
    "/{name}/credentials",
    response_model=McpCredentialResponse,
    summary="Get MCP server credentials (masked)",
    description="Get masked credential information for an MCP server."
)
async def get_mcp_credentials(name: str):
    """Get masked credential info for a server.

    Looks up the convention-based secret_id '{name}_mcp_key' in .secret.json.
    Falls back to legacy '{name}_api_key' for backward compatibility.
    """
    from flocks.security import get_secret_manager
    from flocks.security.secrets import SecretManager

    try:
        secrets = get_secret_manager()

        # Convention-based secret_id: _mcp_key first, fall back to legacy _api_key
        secret_id = f"{name}_mcp_key"
        api_key = secrets.get(secret_id)
        if not api_key:
            legacy_id = f"{name}_api_key"
            api_key = secrets.get(legacy_id)
            if api_key:
                secret_id = legacy_id

        return McpCredentialResponse(
            secret_id=secret_id if api_key else None,
            api_key_masked=SecretManager.mask(api_key) if api_key else None,
            has_credential=bool(api_key),
        )
    except Exception as e:
        log.error("mcp.credentials.get.error", {"name": name, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{name}/credentials",
    response_model=Dict[str, Any],
    summary="Set MCP server credentials",
    description="Set authentication credentials for an MCP server."
)
async def set_mcp_credentials(name: str, request: McpCredentialRequest):
    """Set credentials for a server.

    Stores in .secret.json with flat KV format.
    """
    from flocks.security import get_secret_manager

    try:
        if not request.api_key:
            raise HTTPException(status_code=400, detail="API key required")

        # Use provided secret_id or convention-based default (_mcp_key for MCP servers)
        secret_id = request.secret_id or f"{name}_mcp_key"
        decision = await run_before_action(
            SecurityAction(
                action="use_secret",
                resource={"type": "secret", "id": secret_id},
                canonical_input={"secret_id": secret_id, "operation": "set"},
                execution_domain="control_plane",
                metadata={"entry": "api", "mcp_server": name},
            )
        )
        enforce_action_decision(decision)

        secrets = get_secret_manager()
        secrets.set(secret_id, request.api_key)

        log.info("mcp.credentials.set", {"name": name, "secret_id": secret_id})

        return {
            "success": True,
            "message": f"Credentials saved as '{secret_id}'",
            "secret_id": secret_id,
        }

    except ActionDecisionError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        log.error("mcp.credentials.set.error", {"name": name, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/{name}/credentials",
    response_model=Dict[str, bool],
    summary="Delete MCP server credentials",
    description="Delete stored credentials for an MCP server."
)
async def delete_mcp_credentials(name: str):
    """Delete credentials for a server."""
    from flocks.security import get_secret_manager

    try:
        # Delete both current (_mcp_key) and legacy (_api_key) entries
        secret_id = f"{name}_mcp_key"
        legacy_id = f"{name}_api_key"
        decision = await run_before_action(
            SecurityAction(
                action="delete",
                resource={"type": "secret", "id": secret_id},
                canonical_input={"secret_ids": [secret_id, legacy_id]},
                execution_domain="control_plane",
                metadata={"entry": "api", "mcp_server": name},
            )
        )
        enforce_action_decision(decision)

        secrets = get_secret_manager()
        deleted = secrets.delete(secret_id)
        deleted = secrets.delete(legacy_id) or deleted

        if deleted:
            log.info("mcp.credentials.deleted", {"name": name, "secret_id": secret_id})
            return {"success": True}
        else:
            raise HTTPException(status_code=404, detail="No credentials found for this server")

    except ActionDecisionError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        log.error("mcp.credentials.delete.error", {"name": name, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{name}/test-credentials",
    response_model=Dict[str, Any],
    summary="Test MCP server credentials",
    description="Test if the stored credentials are valid by attempting connection."
)
async def test_mcp_credentials(name: str):
    """Test credentials by attempting connection"""
    try:
        import time
        start = time.time()
        
        server_config = await _load_mcp_server_config(name)
        if not server_config:
            return {
                "success": False,
                "message": f"Server '{name}' not found in configuration",
                "error": "Server not configured"
            }

        # Get current status
        status = await MCP.status()
        server_status = status.get(name)
        
        # If not connected, try to connect first
        if not server_status or server_status.status != McpStatus.CONNECTED:
            log.info("test_credentials.connecting", {"server": name})
            success = await _execute_mcp_action(
                action="mcp_test_credentials",
                name=name,
                config=server_config,
                effect=lambda: MCP.connect(name, server_config),
            )
            if not success:
                return {
                    "success": False,
                    "message": f"Failed to connect to server '{name}'. Check credentials and server URL.",
                    "error": "Connection failed"
                }
        
        # Try to refresh tools (validates the connection and credentials)
        count = await _execute_mcp_action(
            action="mcp_test_credentials",
            name=name,
            config=server_config,
            effect=lambda: MCP.refresh_tools(name),
        )
        latency = int((time.time() - start) * 1000)
        
        return {
            "success": True,
            "message": f"Credentials valid. Connected successfully and found {count} tools.",
            "latency_ms": latency,
            "tools_count": count
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Credentials test failed: {str(e)}",
            "error": str(e)
        }


# ==================== Catalog endpoints ====================


class CatalogSearchRequest(BaseModel):
    """Request to search the MCP catalog"""
    query: Optional[str] = Field(None, description="Free-text search")
    category: Optional[str] = Field(None, description="Filter by category")
    language: Optional[str] = Field(None, description="Filter by programming language")
    tags: Optional[List[str]] = Field(None, description="Filter by tags")
    official_only: bool = Field(False, description="Only official servers")


class CatalogInstallRequest(BaseModel):
    """Request to install a server from catalog"""
    server_id: str = Field(..., description="Catalog server ID")
    enabled: bool = Field(False, description="Enable immediately after adding")
    env_overrides: Optional[Dict[str, str]] = Field(None, description="Environment variable overrides")
    credentials: Optional[Dict[str, str]] = Field(None, description="Secret credentials (env_var_name -> value), saved to .secret.json")
    args: Optional[Dict[str, str]] = Field(None, description="Positional parameter overrides for {param:xxx} placeholders in local_command")
    skip_package_install: bool = Field(False, description="Skip automatic package installation (for advanced users)")


@router.get(
    "/catalog/entries",
    response_model=List[Dict[str, Any]],
    summary="Get MCP catalog",
    description="List all available MCP servers from the built-in catalog.",
    operation_id="mcp.catalog.list"
)
async def get_mcp_catalog():
    """List all available MCP servers from catalog"""
    try:
        catalog = McpCatalog.get()
        return [entry.to_dict() for entry in catalog.entries]
    except Exception as e:
        log.error("mcp.catalog.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/catalog/categories",
    response_model=Dict[str, Any],
    summary="Get catalog categories",
    description="Get all MCP catalog categories with metadata.",
    operation_id="mcp.catalog.categories"
)
async def get_catalog_categories():
    """Get catalog categories"""
    try:
        catalog = McpCatalog.get()
        return {cid: cat.model_dump() for cid, cat in catalog.categories.items()}
    except Exception as e:
        log.error("mcp.catalog.categories.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/catalog/stats",
    response_model=Dict[str, Any],
    summary="Get catalog statistics",
    description="Get statistics about the MCP catalog.",
    operation_id="mcp.catalog.stats"
)
async def get_catalog_stats():
    """Get catalog statistics"""
    try:
        catalog = McpCatalog.get()
        return catalog.get_stats()
    except Exception as e:
        log.error("mcp.catalog.stats.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/catalog/search",
    response_model=List[Dict[str, Any]],
    summary="Search MCP catalog",
    description="Search the MCP catalog with filters.",
    operation_id="mcp.catalog.search"
)
async def search_mcp_catalog(request: CatalogSearchRequest):
    """Search catalog with filters"""
    try:
        catalog = McpCatalog.get()
        results = catalog.search(
            query=request.query,
            category=request.category,
            language=request.language,
            tags=request.tags,
            official_only=request.official_only,
        )
        return [entry.to_dict() for entry in results]
    except Exception as e:
        log.error("mcp.catalog.search.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/catalog/entries/{server_id}",
    response_model=Dict[str, Any],
    summary="Get catalog entry",
    description="Get a specific MCP server entry from the catalog.",
    operation_id="mcp.catalog.get"
)
async def get_catalog_entry(server_id: str):
    """Get a specific catalog entry"""
    try:
        catalog = McpCatalog.get()
        entry = catalog.get_entry(server_id)
        if not entry:
            raise HTTPException(status_code=404, detail=f"Catalog entry not found: {server_id}")
        return entry.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        log.error("mcp.catalog.get.error", {"server_id": server_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/catalog/configured",
    response_model=List[str],
    summary="Get configured catalog server IDs",
    description="Return IDs of catalog servers already present in flocks.json.",
    operation_id="mcp.catalog.configured"
)
async def get_catalog_configured():
    """Return server IDs already configured in flocks.json mcp section."""
    try:
        existing = ConfigWriter.list_mcp_servers()
        return list(existing.keys())
    except Exception as e:
        log.error("mcp.catalog.configured.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/catalog/auto-setup",
    response_model=Dict[str, Any],
    summary="Auto-configure no-secret catalog entries",
    description="Batch-configure all catalog entries that don't require API keys.",
    operation_id="mcp.catalog.auto_setup"
)
async def auto_setup_catalog():
    """Batch write all no-secret catalog entries to flocks.json with enabled=false."""
    try:
        async def _setup() -> Dict[str, Any]:
            catalog = McpCatalog.get()
            existing = ConfigWriter.list_mcp_servers()
            configured: List[str] = []
            skipped: List[str] = []

            for entry in catalog.entries:
                if entry.id in existing:
                    skipped.append(entry.id)
                    continue
                if entry.requires_auth:
                    continue
                config = entry.to_mcp_config()
                if not config:
                    continue
                config["enabled"] = False
                ConfigWriter.add_mcp_server(entry.id, config)
                configured.append(entry.id)

            log.info("mcp.catalog.auto_setup", {
                "configured": len(configured),
                "skipped": len(skipped),
            })
            all_configured = list(ConfigWriter.list_mcp_servers().keys())
            return {
                "newly_configured": configured,
                "skipped": skipped,
                "all_configured_ids": all_configured,
            }

        return await _execute_mcp_action(
            action="mcp_catalog_auto_setup",
            name="catalog",
            config=None,
            effect=_setup,
        )
    except Exception as e:
        log.error("mcp.catalog.auto_setup.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/catalog/install",
    response_model=Dict[str, Any],
    summary="Install MCP server from catalog",
    description="Add an MCP server from the catalog to your configuration.",
    operation_id="mcp.catalog.install"
)
async def install_from_catalog(request: CatalogInstallRequest):
    """Install an MCP server from catalog into flocks.json.

    If credentials are provided, they are saved to .secret.json and the config
    uses {secret:key} references so that actual values stay out of flocks.json.
    """
    async def _install() -> Dict[str, Any]:
        catalog = McpCatalog.get()
        entry = catalog.get_entry(request.server_id)
        if not entry:
            raise HTTPException(status_code=404, detail=f"Catalog entry not found: {request.server_id}")

        # Save credentials to .secret.json if provided
        if request.credentials:
            from flocks.security import get_secret_manager
            secrets = get_secret_manager()
            for var_name, var_value in request.credentials.items():
                if var_value:
                    secret_key = var_name.lower()
                    secrets.set(secret_key, var_value)
                    log.info("mcp.catalog.credential_saved", {"server_id": request.server_id, "key": secret_key})

        # Run package installation before writing config.
        # On failure return 400 immediately — do NOT persist a broken config.
        if not request.skip_package_install:
            try:
                await preflight_install(entry)
            except RuntimeError as e:
                log.warn("mcp.catalog.preflight_failed", {"server_id": request.server_id, "error": str(e)})
                raise HTTPException(status_code=400, detail=str(e))

        config = entry.to_mcp_config(request.env_overrides, args=request.args)
        if not config:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot generate config for '{request.server_id}': no install command available"
            )

        config["enabled"] = bool(request.enabled)

        if request.enabled:
            # Connect immediately only when the caller explicitly enables the
            # server during installation.
            success = await MCP.connect(request.server_id, config)
            if not success:
                status = await MCP.status()
                server_status = status.get(request.server_id)
                error_detail = getattr(server_status, "error", None) if server_status else None
                raise HTTPException(
                    status_code=400,
                    detail=error_detail or f"Failed to connect to server: {request.server_id}",
                )

        try:
            ConfigWriter.add_mcp_server(request.server_id, config)

            from flocks.tool.tool_loader import save_mcp_config
            save_mcp_config(request.server_id, config)
        except Exception:
            # Roll back in-memory connection if persistence fails, to avoid a
            # connected-but-not-configured server disappearing after restart.
            if request.enabled:
                await MCP.remove(request.server_id)
            raise

        log.info("mcp.catalog.installed", {"server_id": request.server_id})

        return {
            "success": True,
            "server_id": request.server_id,
            "name": entry.name,
            "config": config,
            "message": (
                f"Added {entry.name} to configuration and enabled it"
                if request.enabled
                else f"Added {entry.name} to configuration; it is currently disabled. Enable it before connecting"
            ),
            "requires_env": [
                {"name": k, "description": v.description, "secret": v.secret}
                for k, v in entry.required_env_vars.items()
            ],
        }

    try:
        # Keep every stateful step — secret persistence, package preflight,
        # runtime connection and config write — inside the action effect.  A
        # no-op preflight gate is not sufficient for outcome auditing.
        return await _execute_mcp_action(
            action="mcp_catalog_install",
            name=request.server_id,
            config={
                "enabled": request.enabled,
                "credentials_present": bool(request.credentials),
                "skip_package_install": request.skip_package_install,
            },
            effect=_install,
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error("mcp.catalog.install.error", {"server_id": request.server_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))

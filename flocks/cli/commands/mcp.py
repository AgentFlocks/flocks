"""
MCP CLI commands

Provides MCP server management commands: list, add, auth, logout, debug
Ported from original cli/cmd/mcp.ts
"""

import asyncio
import json
import os
import webbrowser
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm

from flocks.mcp import MCP, get_manager, McpStatus
from flocks.mcp.auth import McpAuth
from flocks.config.config import Config


mcp_app = typer.Typer(
    name="mcp",
    help="Manage MCP (Model Context Protocol) servers",
    no_args_is_help=True,
)

console = Console()


def _get_status_icon(status: McpStatus) -> str:
    """Get status icon"""
    icons = {
        McpStatus.CONNECTED: "[green]✓[/green]",
        McpStatus.DISABLED: "[dim]○[/dim]",
        McpStatus.FAILED: "[red]✗[/red]",
        McpStatus.NEEDS_AUTH: "[yellow]⚠[/yellow]",
        McpStatus.NEEDS_CLIENT_REGISTRATION: "[red]✗[/red]",
    }
    return icons.get(status, "[dim]?[/dim]")


def _get_status_text(status: McpStatus) -> str:
    """Get status text"""
    texts = {
        McpStatus.CONNECTED: "connected",
        McpStatus.DISABLED: "disabled",
        McpStatus.FAILED: "failed",
        McpStatus.NEEDS_AUTH: "needs authentication",
        McpStatus.NEEDS_CLIENT_REGISTRATION: "needs client registration",
    }
    return texts.get(status, "unknown")


def _get_auth_status_icon(status: str) -> str:
    """Get auth status icon"""
    icons = {
        "authenticated": "[green]✓[/green]",
        "expired": "[yellow]⚠[/yellow]",
        "not_authenticated": "[red]✗[/red]",
    }
    return icons.get(status, "[dim]?[/dim]")


@mcp_app.command("list")
def mcp_list(
    format: str = typer.Option(
        "table", "--format",
        help="Output format: table or json"
    ),
):
    """
    List MCP servers and their status
    """
    asyncio.run(_list_servers(format))


async def _list_servers(format: str):
    """Internal list implementation"""
    config = await Config.get()
    mcp_config = getattr(config, 'mcp', None) or {}
    
    manager = get_manager()
    statuses = await manager.status()
    
    # Filter to only configured servers
    servers = []
    for name, server_config in mcp_config.items():
        if not isinstance(server_config, dict) or 'type' not in server_config:
            continue
        
        status = statuses.get(name)
        # OAuth support is determined by config, not manager method
        oauth_config = server_config.get('oauth')
        supports_oauth = server_config.get('type') == 'remote' and oauth_config is not False
        has_tokens = bool(await McpAuth.get(name))
        
        servers.append({
            "name": name,
            "config": server_config,
            "status": status,
            "has_tokens": has_tokens,
            "supports_oauth": supports_oauth,
        })
    
    if not servers:
        console.print("[dim]No MCP servers configured[/dim]")
        console.print("\nAdd servers with: [cyan]flocks mcp add[/cyan]")
        return
    
    if format == "json":
        json_data = [
            {
                "name": s["name"],
                "type": s["config"].get("type"),
                "status": s["status"].status.value if s["status"] else "not_initialized",
                "error": s["status"].error if s["status"] else None,
                "url": s["config"].get("url"),
                "command": s["config"].get("command"),
            }
            for s in servers
        ]
        console.print(json.dumps(json_data, indent=2))
    else:
        console.print()
        console.print("[bold cyan]MCP Servers[/bold cyan]")
        console.print()
        
        for server in servers:
            name = server["name"]
            config = server["config"]
            status = server["status"]
            
            # Status
            if not status:
                icon = "[dim]○[/dim]"
                status_text = "not initialized"
            else:
                icon = _get_status_icon(status.status)
                status_text = _get_status_text(status.status)
            
            # Hint
            hint = ""
            if server["supports_oauth"] and server["has_tokens"]:
                hint = " [dim](OAuth)[/dim]"
            
            if status and status.error:
                hint += f"\n    [red]{status.error}[/red]"
            
            # Type hint
            type_hint = ""
            if config.get("type") == "remote":
                type_hint = config.get("url", "")
            else:
                cmd = config.get("command", [])
                type_hint = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            
            console.print(f"{icon} [bold]{name}[/bold] [dim]{status_text}[/dim]{hint}")
            console.print(f"    [dim]{type_hint}[/dim]")
            console.print()
        
        console.print(f"[dim]{len(servers)} server(s)[/dim]")


@mcp_app.command("add")
def mcp_add(
    name: Optional[str] = typer.Option(None, "-n", "--name", help="Server name"),
    server_type: Optional[str] = typer.Option(None, "-t", "--type", help="Server type: local or remote"),
    url: Optional[str] = typer.Option(None, "--url", help="Server URL (for remote servers)"),
    command: Optional[str] = typer.Option(None, "-c", "--command", help="Command to run (for local servers)"),
    global_: bool = typer.Option(False, "-g", "--global", help="Add to global config"),
):
    """
    Add an MCP server
    
    Interactive mode if no options provided.
    """
    asyncio.run(_add_server(name, server_type, url, command, global_))


async def _add_server(
    name: Optional[str],
    server_type: Optional[str],
    url: Optional[str],
    command: Optional[str],
    global_: bool,
):
    """Internal add implementation"""
    console.print()
    console.print("[bold cyan]Add MCP Server[/bold cyan]")
    console.print()
    
    # Get name
    if not name:
        name = Prompt.ask("Enter server name")
        if not name:
            console.print("[red]Name is required[/red]")
            raise typer.Exit(1)
    
    # Get type
    if not server_type:
        server_type = Prompt.ask(
            "Server type",
            choices=["local", "remote"],
            default="local"
        )
    
    # Build config
    if server_type == "local":
        if not command:
            command = Prompt.ask("Enter command to run")
        
        if not command:
            console.print("[red]Command is required for local servers[/red]")
            raise typer.Exit(1)
        
        mcp_config = {
            "type": "local",
            "command": command.split(),
        }
    else:
        if not url:
            url = Prompt.ask("Enter server URL")
        
        if not url:
            console.print("[red]URL is required for remote servers[/red]")
            raise typer.Exit(1)
        
        use_oauth = Confirm.ask("Does this server require OAuth authentication?", default=False)
        
        if use_oauth:
            has_client_id = Confirm.ask("Do you have a pre-registered client ID?", default=False)
            
            if has_client_id:
                client_id = Prompt.ask("Enter client ID")
                has_secret = Confirm.ask("Do you have a client secret?", default=False)
                
                oauth_config = {"clientId": client_id}
                if has_secret:
                    client_secret = Prompt.ask("Enter client secret", password=True)
                    oauth_config["clientSecret"] = client_secret
                
                mcp_config = {
                    "type": "remote",
                    "url": url,
                    "oauth": oauth_config,
                }
            else:
                mcp_config = {
                    "type": "remote",
                    "url": url,
                    "oauth": {},
                }
        else:
            mcp_config = {
                "type": "remote",
                "url": url,
            }
    
    # MCP server config is always stored in the unified user config directory.
    config_path = Config.get_config_file()
    
    # Load existing config
    import json
    if config_path.exists():
        with open(config_path) as f:
            existing_config = json.load(f)
    else:
        existing_config = {}
    
    # Add MCP server
    if "mcp" not in existing_config:
        existing_config["mcp"] = {}
    
    existing_config["mcp"][name] = mcp_config
    
    # Save config
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(existing_config, f, indent=2)
    
    console.print(f"[green]MCP server \"{name}\" added to {config_path}[/green]")


@mcp_app.command("auth")
def mcp_auth(
    name: Optional[str] = typer.Argument(None, help="MCP server name"),
):
    """
    Authenticate with an OAuth-enabled MCP server
    """
    asyncio.run(_auth_server(name))


async def _auth_server(name: Optional[str]):
    """Internal auth implementation"""
    config = await Config.get()
    mcp_config = getattr(config, 'mcp', None) or {}
    
    # Get OAuth-capable servers
    oauth_servers = []
    for server_name, server_config in mcp_config.items():
        if not isinstance(server_config, dict):
            continue
        if server_config.get("type") != "remote":
            continue
        oauth_config = server_config.get("oauth")
        if oauth_config is False:
            continue
        oauth_servers.append((server_name, server_config))
    
    if not oauth_servers:
        console.print("[yellow]No OAuth-capable MCP servers configured[/yellow]")
        console.print("\nRemote MCP servers support OAuth by default.")
        return
    
    console.print()
    console.print("[bold cyan]MCP OAuth Authentication[/bold cyan]")
    console.print()
    
    # Select server
    if not name:
        console.print("Available OAuth servers:")
        for i, (server_name, server_config) in enumerate(oauth_servers, 1):
            # Check auth status
            entry = await McpAuth.get(server_name)
            if entry and entry.tokens:
                expired = await McpAuth.is_token_expired(server_name)
                auth_status = "expired" if expired else "authenticated"
            else:
                auth_status = "not_authenticated"
            icon = _get_auth_status_icon(auth_status)
            console.print(f"  {i}. {icon} {server_name} ({auth_status})")
        
        choice = Prompt.ask("Select server number")
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(oauth_servers):
                name = oauth_servers[idx][0]
            else:
                console.print("[red]Invalid selection[/red]")
                raise typer.Exit(1)
        except ValueError:
            console.print("[red]Invalid selection[/red]")
            raise typer.Exit(1)
    
    # Check if server exists
    server_config = mcp_config.get(name)
    if not server_config:
        console.print(f"[red]MCP server not found: {name}[/red]")
        raise typer.Exit(1)
    
    if server_config.get("type") != "remote":
        console.print(f"[red]MCP server {name} is not a remote server[/red]")
        raise typer.Exit(1)
    
    if server_config.get("oauth") is False:
        console.print(f"[red]MCP server {name} has OAuth explicitly disabled[/red]")
        raise typer.Exit(1)
    
    # Check current auth status
    entry = await McpAuth.get(name)
    if entry and entry.tokens:
        expired = await McpAuth.is_token_expired(name)
        auth_status = "expired" if expired else "authenticated"
    else:
        auth_status = "not_authenticated"
    
    if auth_status == "authenticated":
        reauth = Confirm.ask(f"{name} already has valid credentials. Re-authenticate?", default=False)
        if not reauth:
            console.print("[dim]Cancelled[/dim]")
            return
    elif auth_status == "expired":
        console.print(f"[yellow]{name} has expired credentials. Re-authenticating...[/yellow]")
    
    # Start auth flow
    console.print("[dim]Starting OAuth flow...[/dim]")
    console.print("[yellow]Note: Full OAuth flow not yet implemented (P1 feature)[/yellow]")
    console.print("[dim]For now, you can configure API Key authentication in config file[/dim]")
    
    # TODO: Implement OAuth flow (P1)
    # try:
    #     auth_info = await MCP.start_auth(name)
    #     auth_url = auth_info.get("authorization_url")
    #     ...
    # except Exception as e:
    #     console.print(f"[red]Authentication failed: {e}[/red]")
    #     raise typer.Exit(1)


@mcp_app.command("logout")
def mcp_logout(
    name: Optional[str] = typer.Argument(None, help="MCP server name"),
):
    """
    Remove OAuth credentials for an MCP server
    """
    asyncio.run(_logout_server(name))


async def _logout_server(name: Optional[str]):
    """Internal logout implementation"""
    credentials = await McpAuth.all()
    
    if not credentials:
        console.print("[dim]No MCP OAuth credentials stored[/dim]")
        return
    
    console.print()
    console.print("[bold cyan]MCP OAuth Logout[/bold cyan]")
    console.print()
    
    # Select server
    if not name:
        server_names = list(credentials.keys())
        console.print("Servers with stored credentials:")
        for i, server_name in enumerate(server_names, 1):
            entry = credentials[server_name]
            has_tokens = bool(entry.get("tokens"))
            has_client = bool(entry.get("clientInfo"))
            hint = ""
            if has_tokens and has_client:
                hint = " (tokens + client)"
            elif has_tokens:
                hint = " (tokens)"
            elif has_client:
                hint = " (client registration)"
            console.print(f"  {i}. {server_name}{hint}")
        
        choice = Prompt.ask("Select server number")
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(server_names):
                name = server_names[idx]
            else:
                console.print("[red]Invalid selection[/red]")
                raise typer.Exit(1)
        except ValueError:
            console.print("[red]Invalid selection[/red]")
            raise typer.Exit(1)
    
    if name not in credentials:
        console.print(f"[red]No credentials found for: {name}[/red]")
        raise typer.Exit(1)
    
    # Remove credentials
    await McpAuth.remove(name)
    
    console.print(f"[green]Removed OAuth credentials for {name}[/green]")


@mcp_app.command("debug")
def mcp_debug(
    name: str = typer.Argument(..., help="MCP server name"),
):
    """
    Debug OAuth connection for an MCP server
    """
    asyncio.run(_debug_server(name))


async def _debug_server(name: str):
    """Internal debug implementation"""
    config = await Config.get()
    mcp_config = getattr(config, 'mcp', None) or {}
    
    server_config = mcp_config.get(name)
    if not server_config:
        console.print(f"[red]MCP server not found: {name}[/red]")
        raise typer.Exit(1)
    
    if server_config.get("type") != "remote":
        console.print(f"[red]MCP server {name} is not a remote server[/red]")
        raise typer.Exit(1)
    
    console.print()
    console.print("[bold cyan]MCP OAuth Debug[/bold cyan]")
    console.print()
    
    console.print(f"Server: {name}")
    console.print(f"URL: {server_config.get('url')}")
    
    # Check OAuth config
    oauth_config = server_config.get("oauth")
    if oauth_config is False:
        console.print("[yellow]OAuth explicitly disabled[/yellow]")
        return
    
    # Check auth status
    entry = await McpAuth.get(name)
    if entry and entry.tokens:
        expired = await McpAuth.is_token_expired(name)
        auth_status = "expired" if expired else "authenticated"
    else:
        auth_status = "not_authenticated"
    icon = _get_auth_status_icon(auth_status)
    console.print(f"Auth status: {icon} {auth_status}")
    
    # Check stored credentials
    entry = await McpAuth.get(name)
    if entry:
        if entry.tokens:
            console.print(f"  Access token: {entry.tokens.access_token[:20]}...")
            if entry.tokens.expires_at:
                from datetime import datetime
                expires_dt = datetime.fromtimestamp(entry.tokens.expires_at)
                is_expired = entry.tokens.expires_at < datetime.now().timestamp()
                console.print(f"  Expires: {expires_dt.isoformat()} {'(EXPIRED)' if is_expired else ''}")
            if entry.tokens.refresh_token:
                console.print("  Refresh token: present")
        
        if entry.client_info:
            console.print(f"  Client ID: {entry.client_info.client_id}")
    else:
        console.print("  No stored credentials")
    
    console.print()
    console.print("[dim]Debug complete[/dim]")


@mcp_app.command("connect")
def mcp_connect(
    name: str = typer.Argument(..., help="MCP server name"),
):
    """
    Connect to an MCP server
    """
    asyncio.run(_connect_server(name))


async def _connect_server(name: str):
    """Internal connect implementation"""
    config = await Config.get()
    mcp_config = getattr(config, 'mcp', None) or {}
    
    server_config = mcp_config.get(name)
    if not server_config:
        console.print(f"[red]Server not found: {name}[/red]")
        raise typer.Exit(1)
    
    # Convert to dict if needed
    if hasattr(server_config, 'model_dump'):
        server_config = server_config.model_dump()
    elif hasattr(server_config, 'dict'):
        server_config = server_config.dict()
    elif not isinstance(server_config, dict):
        server_config = dict(server_config)
    
    manager = get_manager()
    success = await manager.connect(name, server_config)
    
    if success:
        console.print(f"[green]Connected to {name}[/green]")
        
        # Show tools
        info = await manager.get_server_info(name)
        if info and info.tools:
            console.print(f"[dim]Available tools: {len(info.tools)}[/dim]")
    else:
        console.print(f"[red]Failed to connect to {name}[/red]")
        raise typer.Exit(1)


@mcp_app.command("disconnect")
def mcp_disconnect(
    name: str = typer.Argument(..., help="MCP server name"),
):
    """
    Disconnect from an MCP server
    """
    asyncio.run(_disconnect_server(name))


async def _disconnect_server(name: str):
    """Internal disconnect implementation"""
    manager = get_manager()
    success = await manager.disconnect(name)
    
    if success:
        console.print(f"[green]Disconnected from {name}[/green]")
    else:
        console.print(f"[red]Failed to disconnect from {name}[/red]")
        raise typer.Exit(1)


@mcp_app.command("tools")
def mcp_tools(
    server: Optional[str] = typer.Argument(None, help="Server name (optional)"),
):
    """
    List MCP tools
    """
    asyncio.run(_list_tools(server))


async def _list_tools(server: Optional[str]):
    """Internal tools list implementation"""
    from flocks.mcp import McpToolRegistry
    from flocks.tool import ToolRegistry
    
    console.print()
    console.print("[bold cyan]MCP Tools[/bold cyan]")
    console.print()
    
    # Get MCP tools
    if server:
        tool_names = McpToolRegistry.get_server_tools(server)
        if not tool_names:
            console.print(f"[dim]No tools from server: {server}[/dim]")
            return
    else:
        # All MCP tools
        tool_names = []
        for server_name in McpToolRegistry.get_all_servers():
            tool_names.extend(McpToolRegistry.get_server_tools(server_name))
    
    if not tool_names:
        console.print("[dim]No MCP tools registered[/dim]")
        return
    
    # Display tools
    for tool_name in sorted(tool_names):
        tool = ToolRegistry.get(tool_name)
        if tool:
            source = McpToolRegistry.get_source(tool_name)
            server_name = source.mcp_server if source else "unknown"
            console.print(f"[bold]{tool_name}[/bold] [dim]({server_name})[/dim]")
            if tool.info.description:
                # Only show first line of description
                desc = tool.info.description.split('\n')[0]
                console.print(f"  {desc}")
            console.print()
    
    console.print(f"[dim]{len(tool_names)} tool(s)[/dim]")


@mcp_app.command("refresh")
def mcp_refresh(
    server: Optional[str] = typer.Argument(None, help="Server name (optional, refresh all if omitted)"),
):
    """
    Refresh MCP tools
    """
    asyncio.run(_refresh_tools(server))


async def _refresh_tools(server: Optional[str]):
    """Internal refresh implementation"""
    from flocks.mcp import McpToolRegistry
    
    console.print()
    
    if server:
        # Refresh specific server
        console.print(f"[dim]Refreshing tools from {server}...[/dim]")
        try:
            count = await MCP.refresh_tools(server)
            console.print(f"[green]Refreshed {count} tools from {server}[/green]")
        except Exception as e:
            console.print(f"[red]Failed to refresh {server}: {e}[/red]")
            raise typer.Exit(1)
    else:
        # Refresh all servers
        servers = McpToolRegistry.get_all_servers()
        if not servers:
            console.print("[dim]No MCP servers with tools[/dim]")
            return
        
        console.print(f"[dim]Refreshing {len(servers)} server(s)...[/dim]")
        
        for server_name in servers:
            try:
                count = await MCP.refresh_tools(server_name)
                console.print(f"[green]✓[/green] {server_name}: {count} tools")
            except Exception as e:
                console.print(f"[red]✗[/red] {server_name}: {e}")
        
        console.print()
        console.print("[green]Refresh complete[/green]")


@mcp_app.command("serve")
def mcp_serve(
    directory: str = typer.Option(".", "-C", "--cd"),
    permission_mode: str = typer.Option("default", "--permission-mode"),
    allowed_tools: list[str] = typer.Option([], "--allowed-tools"),
    disallowed_tools: list[str] = typer.Option([], "--disallowed-tools"),
    sandbox: Optional[str] = typer.Option(None, "--sandbox"),
    max_budget: Optional[float] = typer.Option(None, "--max-budget"),
):
    """Expose Flocks as an MCP server over stdin/stdout (no HTTP service needed)."""
    import math
    from pathlib import Path
    from flocks.cli.commands.exec import PermissionMode, SandboxMode, split_tools
    from flocks.cli.mcp_server import create_server, serve_stdio

    path = Path(directory).expanduser().resolve()
    if not path.is_dir():
        raise typer.BadParameter("--cd must be an existing directory")
    try:
        PermissionMode(permission_mode)
        if sandbox is not None:
            SandboxMode(sandbox)
        if max_budget is not None and (not math.isfinite(max_budget) or max_budget < 0):
            raise ValueError("--max-budget must be finite and non-negative")
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    server = create_server(path, permission_mode, split_tools(allowed_tools), split_tools(disallowed_tools), sandbox, max_budget)
    asyncio.run(serve_stdio(server))

"""
Miscellaneous routes for Flocks TUI compatibility

Provides endpoints that Flocks SDK expects but are not core functionality.
Includes: auth, skill, command endpoints.
"""

from typing import Dict, List, Any, Optional
from fastapi import APIRouter, Request
from pydantic import BaseModel

from flocks.utils.log import Log
from flocks.provider.provider import Provider, ProviderConfig
from flocks.tool.system.skill import get_all_skills, get_skill
from flocks.command.command import Command


router = APIRouter()
log = Log.create(service="misc-routes")


# Auth models
class AuthData(BaseModel):
    """Auth data for provider"""
    type: str = "api"
    key: Optional[str] = None


class AuthSetRequest(BaseModel):
    """Request to set provider auth"""
    providerID: str
    auth: AuthData


# Auth routes - Specific routes MUST come before generic routes

@router.put(
    "/auth/flocks",
    summary="Flocks auth",
    description="Flocks authentication endpoint"
)
async def flocks_auth(request: Request) -> Dict[str, Any]:
    """Flocks authentication"""
    # Return success - auth not implemented
    return {"success": True}


# Generic auth endpoint for all providers (must be after specific routes)
@router.put(
    "/auth/{provider_id}",
    summary="Set provider auth",
    description="Set authentication for a provider"
)
async def set_provider_auth(provider_id: str, request: Request) -> Dict[str, Any]:
    """Set provider authentication"""
    try:
        body = await request.json()
        auth = body.get("auth", {})
        api_key = auth.get("key")
        
        if api_key:
            # Initialize providers if needed
            Provider._ensure_initialized()
            
            # Get provider and configure it
            provider = Provider.get(provider_id)
            if provider:
                config = ProviderConfig(
                    provider_id=provider_id,
                    api_key=api_key,
                )
                provider.configure(config)
                log.info("provider.auth.set", {"provider_id": provider_id})
        
        return {"success": True}
    except Exception as e:
        log.error("provider.auth.error", {"error": str(e), "provider_id": provider_id})
        return {"success": False, "error": str(e)}


# Instance routes
@router.post(
    "/instance/dispose",
    summary="Dispose instance",
    description="Dispose Flocks instance"
)
async def dispose_instance() -> Dict[str, bool]:
    """Dispose instance"""
    return {"success": True}


# Skill routes
@router.get(
    "/skill",
    summary="List skills",
    description="Get all available agent skills"
)
async def list_skills() -> List[Dict[str, Any]]:
    """
    List all available skills
    
    Returns a list of skill definitions with name, description, and location.
    Flocks compatible endpoint.
    """
    skills = await get_all_skills()
    return skills


@router.get(
    "/skill/{name}",
    summary="Get skill",
    description="Get a specific skill by name"
)
async def get_skill_by_name(name: str) -> Dict[str, Any]:
    """
    Get a specific skill
    
    Returns skill definition including content.
    Flocks compatible endpoint.
    """
    try:
        skill = await get_skill(name)
        if not skill:
            return {"error": f"Skill '{name}' not found"}
        return skill
    except Exception as e:
        log.error("skill.get.error", {"error": str(e), "name": name})
        return {"error": str(e)}


# Command routes
@router.get(
    "/command",
    summary="List commands",
    description="Get list of available commands"
)
async def list_commands() -> List[Dict[str, Any]]:
    """
    List all available commands
    
    Returns a list of command definitions (slash commands).
    Flocks compatible endpoint.
    """
    try:
        commands = Command.list_for_surfaces(("webui", "tui", "acp"))
        return [
            {
                "name": cmd.name,
                "canonical_name": cmd.canonical_name,
                "description": cmd.description,
                "template": cmd.template,
                "agent": cmd.agent,
                "model": cmd.model,
                "subtask": cmd.subtask,
                "hidden": cmd.hidden,
                "aliases": list(cmd.aliases),
                "visible_surfaces": list(cmd.visible_surfaces),
                "execution_kind": cmd.execution_kind,
                "allow_attachments": cmd.allow_attachments,
                "requires_existing_session": cmd.requires_existing_session,
                "channel_safe": cmd.channel_safe,
            }
            for cmd in commands
            if not cmd.hidden
        ]
    except Exception as e:
        log.error("command.list.error", {"error": str(e)})
        return []


@router.get(
    "/command/{name}",
    summary="Get command",
    description="Get a specific command by name"
)
async def get_command(name: str) -> Dict[str, Any]:
    """
    Get a specific command
    
    Returns command definition.
    Flocks compatible endpoint.
    """
    try:
        cmd = Command.get(name)
        if not cmd:
            return {"error": f"Command '{name}' not found"}
        
        return {
            "name": cmd.name,
            "canonical_name": cmd.canonical_name,
            "description": cmd.description,
            "template": cmd.template,
            "agent": cmd.agent,
            "model": cmd.model,
            "subtask": cmd.subtask,
            "hidden": cmd.hidden,
            "aliases": list(cmd.aliases),
            "visible_surfaces": list(cmd.visible_surfaces),
            "execution_kind": cmd.execution_kind,
            "allow_attachments": cmd.allow_attachments,
            "requires_existing_session": cmd.requires_existing_session,
            "channel_safe": cmd.channel_safe,
        }
    except Exception as e:
        log.error("command.get.error", {"error": str(e), "name": name})
        return {"error": str(e)}


# Formatter routes
@router.get(
    "/formatter",
    summary="Get formatter status",
    description="Get status of code formatters"
)
async def get_formatter_status() -> List[Dict[str, Any]]:
    """Get formatter status"""
    # Return empty list - formatters are not implemented yet
    return []


# LSP status route (without /lsp prefix - mounted at root)
@router.get(
    "/lsp",
    summary="Get LSP status",
    description="Get status of LSP servers"
)
async def get_lsp_status() -> List[Dict[str, Any]]:
    """Get LSP server status"""
    # Return empty list - LSP status endpoint
    return []


# Experimental resource routes
@router.get(
    "/experimental/resource",
    summary="List experimental resources",
    description="Get list of experimental resources"
)
async def list_experimental_resources() -> Dict[str, Any]:
    """Get experimental resources"""
    # Return empty dict - resources are not implemented yet
    return {}



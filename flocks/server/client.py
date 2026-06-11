"""
Flocks internal SDK client

Provides a client interface for internal use (e.g., ACP server).
This wraps the internal modules to provide an SDK-like interface.
"""

import os
from typing import Optional, Dict, List, Any

from flocks.session.session import Session, SessionInfo
from flocks.session.message import Message
from flocks.agent.registry import Agent
from flocks.project.project import Project
from flocks.config.config import Config
from flocks.mcp import get_manager
from flocks.storage.storage import Storage
from flocks.utils.log import Log


log = Log.create(service="flocks.client")


class SessionClient:
    """Session API client"""
    
    def __init__(self, base_url: str):
        self._base_url = base_url
    
    async def create(self, title: str, directory: str) -> SessionInfo:
        """Create a new session"""
        await Storage.init()
        
        result = await Project.from_directory(directory)
        project = result["project"]
        
        return await Session.create(
            project_id=project.id,
            directory=directory,
            title=title,
        )
    
    async def get(self, session_id: str, directory: str) -> Optional[SessionInfo]:
        """Get a session"""
        await Storage.init()
        
        result = await Project.from_directory(directory)
        project = result["project"]
        
        return await Session.get(project.id, session_id)
    
    async def messages(self, session_id: str, directory: str) -> List[Dict[str, Any]]:
        """Get session messages"""
        messages = await Message.list_with_parts(session_id)
        return [
            {
                "info": msg.info.model_dump(by_alias=True),
                "parts": [part.model_dump() for part in msg.parts],
            }
            for msg in messages
        ]
    
    async def prompt(
        self,
        session_id: str,
        model: Dict[str, str],
        parts: List[Dict[str, Any]],
        agent: str,
        directory: str,
    ) -> None:
        """Send a prompt to the session"""
        # This would invoke the actual LLM session
        # For now, this is a placeholder
        log.info("session.prompt", {
            "session_id": session_id,
            "model": model,
            "agent": agent,
        })
    
    async def command(
        self,
        session_id: str,
        command: str,
        arguments: str,
        model: str,
        agent: str,
        directory: str,
        arguments_json: Optional[Any] = None,
    ) -> None:
        """Execute a command in the session"""
        log.info("session.command", {
            "session_id": session_id,
            "command": command,
            "arguments_json": arguments_json is not None,
        })
    
    async def summarize(
        self,
        session_id: str,
        directory: str,
        provider_id: str,
        model_id: str,
    ) -> None:
        """Summarize/compact the session"""
        log.info("session.summarize", {"session_id": session_id})
    
    async def abort(self, session_id: str, directory: str) -> None:
        """Abort the current session operation"""
        log.info("session.abort", {"session_id": session_id})


class ConfigClient:
    """Config API client"""
    
    def __init__(self, base_url: str):
        self._base_url = base_url
    
    async def get(self, directory: str) -> Dict[str, Any]:
        """Get configuration"""
        config = await Config.get()
        return config.model_dump(by_alias=True, exclude_none=True)
    
    async def providers(self, directory: str) -> List[Dict[str, Any]]:
        """Get available providers"""
        from flocks.provider.provider import Provider
        
        providers = []
        for provider_type in Provider.list_providers():
            provider = Provider.get(provider_type)
            if provider:
                models = {}
                for model in provider.list_models():
                    models[model.id] = {
                        "id": model.id,
                        "name": model.name,
                        "providerID": provider_type,
                    }
                
                providers.append({
                    "id": provider_type,
                    "name": provider.name,
                    "models": models,
                })
        
        return providers


class AppClient:
    """App API client"""
    
    def __init__(self, base_url: str):
        self._base_url = base_url
    
    async def agents(self, directory: str = "") -> List[Dict[str, Any]]:
        """Get available agents"""
        agents = await Agent.list()
        return [
            {
                "name": agent.name,
                "description": agent.description,
                "mode": agent.mode,
                "hidden": agent.hidden,
                "native": agent.native,
            }
            for agent in agents
        ]


class CommandClient:
    """Command API client"""
    
    def __init__(self, base_url: str):
        self._base_url = base_url
    
    async def list(self, directory: str) -> List[Dict[str, Any]]:
        """Get available commands"""
        # Return built-in commands
        return [
            {"name": "compact", "description": "Compact the session"},
        ]


class PermissionClient:
    """Permission API client"""
    
    def __init__(self, base_url: str):
        self._base_url = base_url
    
    async def reply(
        self,
        request_id: str,
        reply: str,
        directory: str,
    ) -> None:
        """Reply to a permission request"""
        log.info("permission.reply", {
            "request_id": request_id,
            "reply": reply,
        })


class McpClient:
    """MCP API client"""
    
    def __init__(self, base_url: str):
        self._base_url = base_url
    
    async def add(
        self,
        directory: str,
        name: str,
        config: Dict[str, Any],
    ) -> None:
        """Add an MCP server"""
        manager = get_manager()
        await manager.add(name, config)


class GlobalClient:
    """Global API client"""
    
    def __init__(self, base_url: str):
        self._base_url = base_url
    
    async def events(self):
        """Subscribe to global events"""
        # This would be an async generator yielding events
        # For now, return empty generator
        return
        yield  # Make it a generator


class FlocksClient:
    """
    Flocks SDK client
    
    Provides an interface similar to the TypeScript FlocksClient.
    """
    
    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        """
        Initialize client
        
        Args:
            base_url: Base URL of the Flocks server
        """
        self._base_url = base_url
        
        # Initialize sub-clients
        self.session = SessionClient(base_url)
        self.config = ConfigClient(base_url)
        self.app = AppClient(base_url)
        self.command = CommandClient(base_url)
        self.permission = PermissionClient(base_url)
        self.mcp = McpClient(base_url)
        self.global_ = GlobalClient(base_url)

"""
ACP Agent implementation

Implements the Agent Client Protocol for editor integration (e.g., Zed).
Matches Flocks' ported src/acp/agent.ts
"""

import asyncio
import json
from typing import Optional, Dict, List, Any, Callable, Awaitable

from flocks.acp.types import (
    ACPConfig,
    ACPSessionState,
    McpServer,
    McpServerLocal,
    McpServerRemote,
    AuthMethod,
    AgentCapabilities,
    AgentInfo,
    InitializeResponse,
    ModelInfo,
    ModeInfo,
    ModelsResponse,
    ModesResponse,
    CommandInfo,
    PermissionOption,
    PlanEntry,
    ToolKind,
    ToolCallLocation,
    ToolCallContent,
)
from flocks.acp.session import ACPSessionManager, RequestError
from flocks.utils.log import Log
from flocks import __version__


log = Log.create(service="acp.agent")


def to_tool_kind(tool_name: str) -> ToolKind:
    """
    Convert tool name to ACP tool kind
    
    Matches TypeScript toToolKind function.
    
    Args:
        tool_name: Tool name
        
    Returns:
        Tool kind string
    """
    tool = tool_name.lower()
    
    if tool == "bash":
        return "execute"
    elif tool == "webfetch":
        return "fetch"
    elif tool in ("edit", "patch", "write"):
        return "edit"
    elif tool in ("grep", "glob", "context7_resolve_library_id", "context7_get_library_docs"):
        return "search"
    elif tool in ("list", "read"):
        return "read"
    else:
        return "other"


def to_locations(tool_name: str, input_data: Dict[str, Any]) -> List[ToolCallLocation]:
    """
    Extract locations from tool input
    
    Matches TypeScript toLocations function.
    
    Args:
        tool_name: Tool name
        input_data: Tool input data
        
    Returns:
        List of locations
    """
    tool = tool_name.lower()
    
    if tool in ("read", "edit", "write"):
        file_path = input_data.get("filePath")
        return [ToolCallLocation(path=file_path)] if file_path else []
    elif tool in ("glob", "grep"):
        path = input_data.get("path")
        return [ToolCallLocation(path=path)] if path else []
    elif tool == "list":
        path = input_data.get("path")
        return [ToolCallLocation(path=path)] if path else []
    else:
        return []


def parse_uri(uri: str) -> Dict[str, Any]:
    """
    Parse URI into file or text content
    
    Matches TypeScript parseUri function.
    
    Args:
        uri: URI string
        
    Returns:
        Parsed result {"type": "file"|"text", ...}
    """
    try:
        if uri.startswith("file://"):
            path = uri[7:]
            name = path.split("/")[-1] or path
            return {
                "type": "file",
                "url": uri,
                "filename": name,
                "mime": "text/plain",
            }
        
        if uri.startswith("zed://"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(uri)
            params = parse_qs(parsed.query)
            path = params.get("path", [""])[0]
            if path:
                name = path.split("/")[-1] or path
                return {
                    "type": "file",
                    "url": f"file://{path}",
                    "filename": name,
                    "mime": "text/plain",
                }
        
        return {"type": "text", "text": uri}
    except Exception:
        return {"type": "text", "text": uri}


class ACPConnection:
    """
    ACP connection abstraction
    
    Handles communication with the ACP client.
    """
    
    def __init__(
        self,
        send_message: Callable[[Dict[str, Any]], Awaitable[None]],
    ):
        """
        Initialize connection
        
        Args:
            send_message: Callback to send messages to client
        """
        self._send_message = send_message
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._next_id = 1
    
    async def send_notification(self, method: str, params: Dict[str, Any]) -> None:
        """Send a notification (no response expected)"""
        await self._send_message({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        })
    
    async def send_request(self, method: str, params: Dict[str, Any]) -> Any:
        """Send a request and wait for response"""
        request_id = self._next_id
        self._next_id += 1
        
        future = asyncio.Future()
        self._pending_requests[request_id] = future
        
        await self._send_message({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        })
        
        try:
            return await future
        finally:
            self._pending_requests.pop(request_id, None)
    
    def handle_response(self, request_id: int, result: Any = None, error: Any = None) -> None:
        """Handle a response from client"""
        future = self._pending_requests.get(request_id)
        if future and not future.done():
            if error:
                future.set_exception(RequestError(error.get("code", -1), error.get("message", "Unknown error")))
            else:
                future.set_result(result)
    
    async def session_update(self, session_id: str, update: Dict[str, Any]) -> None:
        """
        Send session update notification
        
        Args:
            session_id: Session ID
            update: Update data
        """
        await self.send_notification("session/update", {
            "sessionId": session_id,
            "update": update,
        })
    
    async def request_permission(
        self,
        session_id: str,
        tool_call: Dict[str, Any],
        options: List[PermissionOption],
    ) -> Dict[str, Any]:
        """
        Request permission from client
        
        Args:
            session_id: Session ID
            tool_call: Tool call information
            options: Permission options
            
        Returns:
            Permission response
        """
        return await self.send_request("permission/request", {
            "sessionId": session_id,
            "toolCall": tool_call,
            "options": [
                {
                    "optionId": opt.option_id,
                    "kind": opt.kind,
                    "name": opt.name,
                }
                for opt in options
            ],
        })
    
    async def write_text_file(self, session_id: str, path: str, content: str) -> None:
        """
        Write text file via client
        
        Args:
            session_id: Session ID
            path: File path
            content: File content
        """
        await self.send_notification("file/write", {
            "sessionId": session_id,
            "path": path,
            "content": content,
        })


class ACPAgent:
    """
    ACP Agent implementation
    
    Implements the Agent Client Protocol for editor integration.
    Matches TypeScript ACP.Agent class.
    """
    
    def __init__(self, connection: ACPConnection, config: ACPConfig):
        """
        Initialize ACP agent
        
        Args:
            connection: ACP connection
            config: ACP configuration
        """
        self._connection = connection
        self._config = config
        self._sdk = config.sdk
        self._session_manager = ACPSessionManager(self._sdk)
        self._event_abort = False
        self._event_started = False
        self._permission_queues: Dict[str, asyncio.Task] = {}
        self._permission_options = [
            PermissionOption(option_id="once", kind="allow_once", name="Allow once"),
            PermissionOption(option_id="always", kind="allow_always", name="Always allow"),
            PermissionOption(option_id="reject", kind="reject_once", name="Reject"),
        ]
    
    def start_event_subscription(self) -> None:
        """Start event subscription in background"""
        if self._event_started:
            return
        self._event_started = True
        asyncio.create_task(self._run_event_subscription())
    
    async def _run_event_subscription(self) -> None:
        """Run event subscription loop"""
        while not self._event_abort:
            try:
                # Subscribe to events from SDK
                async for event in self._sdk.global_.events():
                    if self._event_abort:
                        return
                    
                    payload = event.get("payload")
                    if not payload:
                        continue
                    
                    try:
                        await self._handle_event(payload)
                    except Exception as e:
                        log.error("event.handle.error", {
                            "error": str(e),
                            "type": payload.get("type"),
                        })
            except Exception as e:
                if self._event_abort:
                    return
                log.error("event.subscription.error", {"error": str(e)})
                await asyncio.sleep(1)  # Retry after delay
    
    async def _handle_event(self, event: Dict[str, Any]) -> None:
        """
        Handle an event from the SDK
        
        Matches TypeScript handleEvent method.
        
        Args:
            event: Event data
        """
        event_type = event.get("type")
        properties = event.get("properties", {})
        
        if event_type == "permission.asked":
            await self._handle_permission_asked(properties)
        elif event_type == "message.part.updated":
            await self._handle_message_part_updated(properties)
    
    async def _handle_permission_asked(self, permission: Dict[str, Any]) -> None:
        """Handle permission.asked event"""
        session_id = permission.get("sessionID")
        session = self._session_manager.try_get(session_id)
        if not session:
            return
        
        directory = session.cwd
        permission_id = permission.get("id")
        permission_name = permission.get("permission")
        tool = permission.get("tool", {})
        metadata = permission.get("metadata", {})
        
        async def process_permission():
            try:
                # Request permission from client
                response = await self._connection.request_permission(
                    session_id=session_id,
                    tool_call={
                        "toolCallId": tool.get("callID", permission_id),
                        "status": "pending",
                        "title": permission_name,
                        "rawInput": metadata,
                        "kind": to_tool_kind(permission_name),
                        "locations": [loc.__dict__ for loc in to_locations(permission_name, metadata)],
                    },
                    options=self._permission_options,
                )
            except Exception as e:
                log.error("permission.request.error", {
                    "error": str(e),
                    "permission_id": permission_id,
                })
                # Reject on error
                await self._sdk.permission.reply(
                    request_id=permission_id,
                    reply="reject",
                    directory=directory,
                )
                return
            
            outcome = response.get("outcome", {})
            if outcome.get("outcome") != "selected":
                await self._sdk.permission.reply(
                    request_id=permission_id,
                    reply="reject",
                    directory=directory,
                )
                return
            
            option_id = outcome.get("optionId")
            
            # Handle edit permission - write file via client
            if option_id != "reject" and permission_name == "edit":
                filepath = metadata.get("filepath", "")
                diff = metadata.get("diff", "")
                
                if filepath and diff:
                    try:
                        with open(filepath, "r") as f:
                            content = f.read()
                        
                        # Apply diff to get new content
                        new_content = self._apply_patch(content, diff)
                        if new_content:
                            await self._connection.write_text_file(
                                session_id=session_id,
                                path=filepath,
                                content=new_content,
                            )
                    except Exception as e:
                        log.error("file.write.error", {"error": str(e)})
            
            # Reply to permission request
            await self._sdk.permission.reply(
                request_id=permission_id,
                reply=option_id,
                directory=directory,
            )
        
        # Queue permission processing to avoid concurrent handling
        prev_task = self._permission_queues.get(session_id)
        if prev_task:
            await prev_task
        
        task = asyncio.create_task(process_permission())
        self._permission_queues[session_id] = task
        
        try:
            await task
        finally:
            if self._permission_queues.get(session_id) == task:
                del self._permission_queues[session_id]
    
    async def _handle_message_part_updated(self, props: Dict[str, Any]) -> None:
        """Handle message.part.updated event"""
        part = props.get("part", {})
        delta = props.get("delta")
        
        session_id = part.get("sessionID")
        session = self._session_manager.try_get(session_id)
        if not session:
            return
        
        part_type = part.get("type")
        
        if part_type == "tool":
            await self._handle_tool_part_update(session_id, part)
        elif part_type == "text":
            if delta and not part.get("ignored"):
                await self._connection.session_update(
                    session_id=session_id,
                    update={
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": delta},
                    },
                )
        elif part_type == "reasoning":
            if delta:
                await self._connection.session_update(
                    session_id=session_id,
                    update={
                        "sessionUpdate": "agent_thought_chunk",
                        "content": {"type": "text", "text": delta},
                    },
                )
    
    async def _handle_tool_part_update(self, session_id: str, part: Dict[str, Any]) -> None:
        """Handle tool part update"""
        state = part.get("state", {})
        status = state.get("status")
        tool_name = part.get("tool", "")
        call_id = part.get("callID", "")
        
        if status == "pending":
            await self._connection.session_update(
                session_id=session_id,
                update={
                    "sessionUpdate": "tool_call",
                    "toolCallId": call_id,
                    "title": tool_name,
                    "kind": to_tool_kind(tool_name),
                    "status": "pending",
                    "locations": [],
                    "rawInput": {},
                },
            )
        elif status == "running":
            input_data = state.get("input", {})
            await self._connection.session_update(
                session_id=session_id,
                update={
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": call_id,
                    "status": "in_progress",
                    "kind": to_tool_kind(tool_name),
                    "title": tool_name,
                    "locations": [loc.__dict__ for loc in to_locations(tool_name, input_data)],
                    "rawInput": input_data,
                },
            )
        elif status == "completed":
            input_data = state.get("input", {})
            output = state.get("output", "")
            kind = to_tool_kind(tool_name)
            
            content = [{"type": "content", "content": {"type": "text", "text": output}}]
            
            # Add diff content for edit tools
            if kind == "edit":
                file_path = input_data.get("filePath", "")
                old_text = input_data.get("oldString", "")
                new_text = input_data.get("newString", input_data.get("content", ""))
                content.append({
                    "type": "diff",
                    "path": file_path,
                    "oldText": old_text,
                    "newText": new_text,
                })
            
            # Handle todowrite - send plan update
            if tool_name == "todowrite":
                try:
                    todos = json.loads(output)
                    if isinstance(todos, list):
                        entries = []
                        for todo in todos:
                            status_map = {"cancelled": "completed"}
                            entries.append({
                                "priority": "medium",
                                "status": status_map.get(todo.get("status"), todo.get("status", "pending")),
                                "content": todo.get("content", ""),
                            })
                        
                        await self._connection.session_update(
                            session_id=session_id,
                            update={
                                "sessionUpdate": "plan",
                                "entries": entries,
                            },
                        )
                except Exception as e:
                    log.error("todo.parse.error", {"error": str(e)})
            
            await self._connection.session_update(
                session_id=session_id,
                update={
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": call_id,
                    "status": "completed",
                    "kind": kind,
                    "content": content,
                    "title": state.get("title", tool_name),
                    "rawInput": input_data,
                    "rawOutput": {
                        "output": output,
                        "metadata": state.get("metadata"),
                    },
                },
            )
        elif status == "error":
            input_data = state.get("input", {})
            error_msg = state.get("error", "Unknown error")
            raw_output = {"error": error_msg}
            if "output" in state:
                raw_output["output"] = state.get("output")
            if "metadata" in state:
                raw_output["metadata"] = state.get("metadata")
            
            await self._connection.session_update(
                session_id=session_id,
                update={
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": call_id,
                    "status": "failed",
                    "kind": to_tool_kind(tool_name),
                    "title": tool_name,
                    "rawInput": input_data,
                    "content": [{"type": "content", "content": {"type": "text", "text": error_msg}}],
                    "rawOutput": raw_output,
                },
            )
    
    def _apply_patch(self, content: str, diff: str) -> Optional[str]:
        """
        Apply unified diff to content
        
        Args:
            content: Original content
            diff: Unified diff
            
        Returns:
            Patched content or None if failed
        """
        try:
            # Simple implementation - for full support would need a proper diff library
            import difflib
            
            # This is a simplified implementation
            # A full implementation would use a proper patch library
            return None  # Placeholder - patch application would go here
        except Exception as e:
            log.error("patch.apply.error", {"error": str(e)})
            return None
    
    async def initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle initialize request
        
        Matches TypeScript initialize method.
        
        Args:
            params: Initialize parameters
            
        Returns:
            Initialize response
        """
        protocol_version = params.get("protocolVersion", 1)
        client_capabilities = params.get("clientCapabilities", {})
        
        log.info("initialize", {"protocolVersion": protocol_version})
        
        auth_method = {
            "id": "flocks-login",
            "name": "Login with Flocks",
            "description": "Run `flocks auth login` in the terminal",
        }
        
        # Support terminal-auth capability
        if client_capabilities.get("_meta", {}).get("terminal-auth"):
            auth_method["_meta"] = {
                "terminal-auth": {
                    "command": "flocks",
                    "args": ["auth", "login"],
                    "label": "Flocks Login",
                },
            }
        
        return {
            "protocolVersion": 1,
            "agentCapabilities": {
                "loadSession": True,
                "mcpCapabilities": {
                    "http": True,
                    "sse": True,
                },
                "promptCapabilities": {
                    "embeddedContext": True,
                    "image": True,
                },
            },
            "authMethods": [auth_method],
            "agentInfo": {
                "name": "Flocks",
                "version": __version__,
            },
        }
    
    async def authenticate(self, params: Dict[str, Any]) -> None:
        """Handle authenticate request"""
        raise NotImplementedError("Authentication not implemented")
    
    async def new_session(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle session/new request
        
        Matches TypeScript newSession method.
        
        Args:
            params: New session parameters
            
        Returns:
            New session response
        """
        cwd = params.get("cwd", ".")
        mcp_servers = self._parse_mcp_servers(params.get("mcpServers", []))
        
        try:
            # Trigger command:new hook if there's a previous session
            previous_session_id = self._session_manager.get_current_session_id()
            if previous_session_id:
                try:
                    from flocks.hooks import trigger_hook, create_command_event
                    from flocks.config import Config
                    
                    config = await Config.get()
                    
                    # Create hook event
                    event = create_command_event(
                        action="new",
                        session_id=previous_session_id,
                        context={
                            "previous_session_id": previous_session_id,
                            "project_id": config.project_id,
                            "workspace_dir": cwd,
                        },
                    )
                    
                    # Trigger hook (non-blocking, errors are caught)
                    await trigger_hook(event)
                    
                except Exception as e:
                    # Hook failure should not block session creation
                    log.warn("session.new.hook_failed", {
                        "error": str(e),
                        "previous_session_id": previous_session_id,
                    })
            
            model = await self._get_default_model(cwd)
            
            # Create session
            state = await self._session_manager.create(cwd, mcp_servers, model)
            session_id = state.id
            
            log.info("session.creating", {
                "session_id": session_id,
                "mcp_servers": len(mcp_servers),
            })
            
            # Load session mode info
            load_result = await self._load_session_mode(cwd, mcp_servers, session_id)
            
            return {
                "sessionId": session_id,
                "models": load_result["models"],
                "modes": load_result["modes"],
                "_meta": {},
            }
        except Exception as e:
            log.error("session.create.error", {"error": str(e)})
            raise
    
    async def load_session(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle session/load request
        
        Matches TypeScript loadSession method.
        
        Args:
            params: Load session parameters
            
        Returns:
            Load session response
        """
        cwd = params.get("cwd", ".")
        session_id = params.get("sessionId")
        mcp_servers = self._parse_mcp_servers(params.get("mcpServers", []))
        
        try:
            model = await self._get_default_model(cwd)
            
            # Load session
            await self._session_manager.load(session_id, cwd, mcp_servers, model)
            
            log.info("session.loading", {
                "session_id": session_id,
                "mcp_servers": len(mcp_servers),
            })
            
            # Load session mode info
            result = await self._load_session_mode(cwd, mcp_servers, session_id)
            
            # Replay session history
            messages = await self._sdk.session.messages(
                session_id=session_id,
                directory=cwd,
            )
            
            # Find last user message to determine current model/mode
            for msg in reversed(messages):
                if msg.get("info", {}).get("role") == "user":
                    user_info = msg.get("info", {})
                    user_model = user_info.get("model", {})
                    if user_model:
                        result["models"]["currentModelId"] = f"{user_model.get('providerID')}/{user_model.get('modelID')}"
                    
                    agent = user_info.get("agent")
                    if agent and any(m["id"] == agent for m in result["modes"]["availableModes"]):
                        result["modes"]["currentModeId"] = agent
                    break
            
            # Replay messages
            for msg in messages:
                await self._process_message(msg)
            
            return result
        except Exception as e:
            log.error("session.load.error", {"error": str(e)})
            raise
    
    async def _process_message(self, message: Dict[str, Any]) -> None:
        """
        Process a message for replay
        
        Matches TypeScript processMessage method.
        
        Args:
            message: Message data
        """
        info = message.get("info", {})
        parts = message.get("parts", [])
        role = info.get("role")
        session_id = info.get("sessionID")
        
        if role not in ("assistant", "user"):
            return
        
        for part in parts:
            part_type = part.get("type")
            
            if part_type == "tool":
                state = part.get("state", {})
                status = state.get("status")
                tool_name = part.get("tool", "")
                call_id = part.get("callID", "")
                
                if status == "pending":
                    await self._connection.session_update(
                        session_id=session_id,
                        update={
                            "sessionUpdate": "tool_call",
                            "toolCallId": call_id,
                            "title": tool_name,
                            "kind": to_tool_kind(tool_name),
                            "status": "pending",
                            "locations": [],
                            "rawInput": {},
                        },
                    )
                elif status == "running":
                    input_data = state.get("input", {})
                    await self._connection.session_update(
                        session_id=session_id,
                        update={
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": call_id,
                            "status": "in_progress",
                            "kind": to_tool_kind(tool_name),
                            "title": tool_name,
                            "locations": [loc.__dict__ for loc in to_locations(tool_name, input_data)],
                            "rawInput": input_data,
                        },
                    )
                elif status == "completed":
                    input_data = state.get("input", {})
                    output = state.get("output", "")
                    kind = to_tool_kind(tool_name)
                    
                    content = [{"type": "content", "content": {"type": "text", "text": output}}]
                    
                    if kind == "edit":
                        file_path = input_data.get("filePath", "")
                        old_text = input_data.get("oldString", "")
                        new_text = input_data.get("newString", input_data.get("content", ""))
                        content.append({
                            "type": "diff",
                            "path": file_path,
                            "oldText": old_text,
                            "newText": new_text,
                        })
                    
                    await self._connection.session_update(
                        session_id=session_id,
                        update={
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": call_id,
                            "status": "completed",
                            "kind": kind,
                            "content": content,
                            "title": state.get("title", tool_name),
                            "rawInput": input_data,
                            "rawOutput": {
                                "output": output,
                                "metadata": state.get("metadata"),
                            },
                        },
                    )
                elif status == "error":
                    input_data = state.get("input", {})
                    error_msg = state.get("error", "Unknown error")
                    raw_output = {"error": error_msg}
                    if "output" in state:
                        raw_output["output"] = state.get("output")
                    if "metadata" in state:
                        raw_output["metadata"] = state.get("metadata")
                    
                    await self._connection.session_update(
                        session_id=session_id,
                        update={
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": call_id,
                            "status": "failed",
                            "kind": to_tool_kind(tool_name),
                            "title": tool_name,
                            "rawInput": input_data,
                            "content": [{"type": "content", "content": {"type": "text", "text": error_msg}}],
                            "rawOutput": raw_output,
                        },
                    )
            
            elif part_type == "text":
                text = part.get("text", "")
                if text and not part.get("ignored"):
                    message_type = "user_message_chunk" if role == "user" else "agent_message_chunk"
                    await self._connection.session_update(
                        session_id=session_id,
                        update={
                            "sessionUpdate": message_type,
                            "content": {"type": "text", "text": text},
                        },
                    )
            
            elif part_type == "file":
                # Handle file parts - image, resource_link, resource
                url = part.get("url", "")
                filename = part.get("filename", "file")
                mime = part.get("mime", "application/octet-stream")
                message_type = "user_message_chunk" if role == "user" else "agent_message_chunk"
                
                if url.startswith("file://"):
                    # Local file - send as resource_link
                    await self._connection.session_update(
                        session_id=session_id,
                        update={
                            "sessionUpdate": message_type,
                            "content": {
                                "type": "resource_link",
                                "uri": url,
                                "name": filename,
                                "mimeType": mime,
                            },
                        },
                    )
                elif url.startswith("data:"):
                    # Embedded content
                    import re
                    match = re.match(r"^data:([^;]+);base64,(.*)$", url)
                    if match:
                        data_mime = match.group(1)
                        base64_data = match.group(2)
                        effective_mime = data_mime or mime
                        
                        if effective_mime.startswith("image/"):
                            await self._connection.session_update(
                                session_id=session_id,
                                update={
                                    "sessionUpdate": message_type,
                                    "content": {
                                        "type": "image",
                                        "mimeType": effective_mime,
                                        "data": base64_data,
                                        "uri": f"file://{filename}",
                                    },
                                },
                            )
                        else:
                            # Text or binary resource
                            is_text = effective_mime.startswith("text/") or effective_mime == "application/json"
                            
                            if is_text:
                                import base64
                                text_content = base64.b64decode(base64_data).decode("utf-8")
                                resource = {
                                    "uri": f"file://{filename}",
                                    "mimeType": effective_mime,
                                    "text": text_content,
                                }
                            else:
                                resource = {
                                    "uri": f"file://{filename}",
                                    "mimeType": effective_mime,
                                    "blob": base64_data,
                                }
                            
                            await self._connection.session_update(
                                session_id=session_id,
                                update={
                                    "sessionUpdate": message_type,
                                    "content": {"type": "resource", "resource": resource},
                                },
                            )
            
            elif part_type == "reasoning":
                text = part.get("text", "")
                if text:
                    await self._connection.session_update(
                        session_id=session_id,
                        update={
                            "sessionUpdate": "agent_thought_chunk",
                            "content": {"type": "text", "text": text},
                        },
                    )
    
    async def _load_session_mode(
        self,
        cwd: str,
        mcp_servers: List[McpServer],
        session_id: str,
    ) -> Dict[str, Any]:
        """
        Load session mode information
        
        Matches TypeScript loadSessionMode method.
        
        Args:
            cwd: Working directory
            mcp_servers: MCP servers
            session_id: Session ID
            
        Returns:
            Dict with models, modes, etc.
        """
        model = await self._get_default_model(cwd)
        
        # Get available providers/models
        providers = await self._sdk.config.providers(directory=cwd)
        
        # Sort providers by name
        sorted_providers = sorted(providers, key=lambda p: p.get("name", "").lower())
        
        available_models = []
        for provider in sorted_providers:
            provider_id = provider.get("id")
            provider_name = provider.get("name")
            models = provider.get("models", {})
            
            for model_id, model_info in models.items():
                available_models.append({
                    "modelId": f"{provider_id}/{model_id}",
                    "name": f"{provider_name}/{model_info.get('name', model_id)}",
                })
        
        # Get available agents
        agents = await self._sdk.app.agents(directory=cwd)
        
        available_modes = []
        for agent in agents:
            if agent.get("mode") != "subagent" and not agent.get("hidden"):
                available_modes.append({
                    "id": agent.get("name"),
                    "name": agent.get("name"),
                    "description": agent.get("description"),
                })
        
        # Get default agent
        from flocks.agent.registry import Agent
        default_agent_name = Agent.default_agent()
        
        current_mode_id = default_agent_name
        for mode in available_modes:
            if mode["name"] == default_agent_name:
                current_mode_id = mode["id"]
                break
        
        if not current_mode_id and available_modes:
            current_mode_id = available_modes[0]["id"]
        
        # Persist default mode
        self._session_manager.set_mode(session_id, current_mode_id)
        
        # Get available commands
        commands = await self._sdk.command.list(directory=cwd)
        
        available_commands = [
            {"name": cmd.get("name"), "description": cmd.get("description", "")}
            for cmd in commands
        ]
        
        # Add built-in commands
        command_names = {cmd["name"] for cmd in available_commands}
        if "compact" not in command_names:
            available_commands.append({
                "name": "compact",
                "description": "compact the session",
            })
        
        # Add MCP servers
        mcp_configs = {}
        for server in mcp_servers:
            if isinstance(server, McpServerRemote):
                headers = {}
                for h in server.headers:
                    headers[h.get("name", "")] = h.get("value", "")
                
                mcp_configs[server.name] = {
                    "type": "remote",
                    "url": server.url,
                    "headers": headers,
                }
            elif isinstance(server, McpServerLocal):
                env = {}
                for e in server.env:
                    env[e.get("name", "")] = e.get("value", "")
                
                mcp_configs[server.name] = {
                    "type": "local",
                    "command": [server.command] + server.args,
                    "environment": env,
                }
        
        # Register MCP servers
        for name, config in mcp_configs.items():
            try:
                await self._sdk.mcp.add(
                    directory=cwd,
                    name=name,
                    config=config,
                )
            except Exception as e:
                log.error("mcp.add.error", {"name": name, "error": str(e)})
        
        # Send available commands update asynchronously
        asyncio.create_task(self._connection.session_update(
            session_id=session_id,
            update={
                "sessionUpdate": "available_commands_update",
                "availableCommands": available_commands,
            },
        ))
        
        return {
            "sessionId": session_id,
            "models": {
                "currentModelId": f"{model['providerID']}/{model['modelID']}",
                "availableModels": available_models,
            },
            "modes": {
                "availableModes": available_modes,
                "currentModeId": current_mode_id,
            },
            "_meta": {},
        }
    
    async def set_session_model(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle session/setModel request
        
        Args:
            params: Set model parameters
            
        Returns:
            Response
        """
        session_id = params.get("sessionId")
        model_id = params.get("modelId", "")
        
        session = self._session_manager.get(session_id)
        
        # Parse model ID
        parts = model_id.split("/", 1)
        if len(parts) == 2:
            provider_id, model_name = parts
        else:
            provider_id = parts[0]
            model_name = parts[0]
        
        self._session_manager.set_model(session.id, {
            "providerID": provider_id,
            "modelID": model_name,
        })
        
        return {"_meta": {}}
    
    async def set_session_mode(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle session/setMode request
        
        Args:
            params: Set mode parameters
            
        Returns:
            Response
        """
        session_id = params.get("sessionId")
        mode_id = params.get("modeId")
        
        self._session_manager.get(session_id)  # Validate session exists
        
        # Validate agent exists
        agents = await self._sdk.app.agents()
        agent_exists = any(a.get("name") == mode_id for a in agents)
        if not agent_exists:
            raise RequestError.invalid_params(f"Agent not found: {mode_id}")
        
        self._session_manager.set_mode(session_id, mode_id)
        
        return {"_meta": {}}
    
    async def prompt(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle session/prompt request
        
        Matches TypeScript prompt method.
        
        Args:
            params: Prompt parameters
            
        Returns:
            Prompt response
        """
        session_id = params.get("sessionId")
        prompt_parts = params.get("prompt", [])
        
        session = self._session_manager.get(session_id)
        directory = session.cwd
        
        # Get model
        current_model = session.model
        model = current_model or await self._get_default_model(directory)
        if not current_model:
            self._session_manager.set_model(session.id, model)
        
        # Get agent
        from flocks.agent.registry import Agent
        agent = session.mode_id or await Agent.default_agent()
        
        # Build parts
        parts = []
        for part in prompt_parts:
            part_type = part.get("type")
            
            if part_type == "text":
                parts.append({
                    "type": "text",
                    "text": part.get("text", ""),
                })
            elif part_type == "image":
                uri = part.get("uri", "")
                parsed = parse_uri(uri)
                filename = parsed.get("filename", "image") if parsed.get("type") == "file" else "image"
                
                data = part.get("data")
                if data:
                    parts.append({
                        "type": "file",
                        "url": f"data:{part.get('mimeType')};base64,{data}",
                        "filename": filename,
                        "mime": part.get("mimeType"),
                    })
                elif uri and uri.startswith("http"):
                    parts.append({
                        "type": "file",
                        "url": uri,
                        "filename": filename,
                        "mime": part.get("mimeType"),
                    })
            elif part_type == "resource_link":
                uri = part.get("uri", "")
                parsed = parse_uri(uri)
                name = part.get("name")
                if name and parsed.get("type") == "file":
                    parsed["filename"] = name
                parts.append(parsed)
            elif part_type == "resource":
                resource = part.get("resource", {})
                text = resource.get("text")
                blob = resource.get("blob")
                
                if text:
                    parts.append({"type": "text", "text": text})
                elif blob and resource.get("mimeType"):
                    uri = resource.get("uri", "")
                    parsed = parse_uri(uri)
                    filename = parsed.get("filename", "file") if parsed.get("type") == "file" else "file"
                    
                    parts.append({
                        "type": "file",
                        "url": f"data:{resource['mimeType']};base64,{blob}",
                        "filename": filename,
                        "mime": resource["mimeType"],
                    })
        
        log.info("prompt.parts", {"parts": len(parts)})
        
        # Check for command
        text_content = "".join(
            p.get("text", "") for p in parts if p.get("type") == "text"
        ).strip()
        
        cmd = None
        if text_content.startswith("/"):
            cmd_parts = text_content[1:].split(None, 1)
            if cmd_parts:
                cmd = {
                    "name": cmd_parts[0],
                    "args": cmd_parts[1] if len(cmd_parts) > 1 else "",
                }
        
        done_response = {
            "stopReason": "end_turn",
            "_meta": {},
        }
        
        if not cmd:
            # Regular prompt
            await self._sdk.session.prompt(
                session_id=session_id,
                model={
                    "providerID": model["providerID"],
                    "modelID": model["modelID"],
                },
                parts=parts,
                agent=agent,
                directory=directory,
            )
            return done_response

        await self._sdk.session.command(
            session_id=session_id,
            command=cmd["name"],
            arguments=cmd["args"],
            model=f"{model['providerID']}/{model['modelID']}",
            agent=agent,
            directory=directory,
        )
        return done_response

    async def _restart_session_state(self, session_id: str) -> None:
        from flocks.session.message import Message
        from flocks.session.features.todo import Todo
        from flocks.session.core.status import SessionStatus

        await Message.clear(session_id)
        await Todo.clear(session_id)
        SessionStatus.clear(session_id)

        session = self._session_manager.get(session_id)
        session.model = None
        session.mode_id = None
        self._session_manager._sessions[session_id] = session
    
    async def cancel(self, params: Dict[str, Any]) -> None:
        """
        Handle cancel notification
        
        Args:
            params: Cancel parameters
        """
        session_id = params.get("sessionId")
        session = self._session_manager.get(session_id)
        
        await self._sdk.session.abort(
            session_id=session_id,
            directory=session.cwd,
        )
    
    def _parse_mcp_servers(self, servers: List[Dict[str, Any]]) -> List[McpServer]:
        """Parse MCP server configurations"""
        result = []
        for server in servers:
            if "type" in server:
                # Remote server
                result.append(McpServerRemote(
                    name=server.get("name", ""),
                    type=server.get("type", "http"),
                    url=server.get("url", ""),
                    headers=server.get("headers", []),
                ))
            else:
                # Local server
                result.append(McpServerLocal(
                    name=server.get("name", ""),
                    command=server.get("command", ""),
                    args=server.get("args", []),
                    env=server.get("env", []),
                ))
        return result
    
    async def _get_default_model(self, cwd: str) -> Dict[str, str]:
        """
        Get default model
        
        Matches TypeScript defaultModel function.
        
        Priority:
        1. ACP config default_model (passed in at init)
        2. default_models.llm in flocks.json (structured)
        3. config.model in flocks.json (legacy string)
        4. Available providers (flocks -> first available)
        5. Fallback to env vars / hardcoded default
        
        Args:
            cwd: Working directory
            
        Returns:
            Model dict {"providerID": str, "modelID": str}
        """
        # Priority 1: ACP config default
        if self._config.default_model:
            return self._config.default_model
        
        # Priority 2 & 3: default_models.llm -> config.model (via unified helper)
        try:
            from flocks.config.config import Config
            default_llm = await Config.resolve_default_llm()
            if default_llm:
                return {
                    "providerID": default_llm["provider_id"],
                    "modelID": default_llm["model_id"],
                }
        except Exception as e:
            log.error("config.resolve_default_llm.error", {"error": str(e)})
        
        # Priority 4: Get from available providers
        try:
            providers = await self._sdk.config.providers(directory=cwd)
            
            # Prefer flocks provider
            flocks_provider = next((p for p in providers if p.get("id") == "flocks"), None)
            if flocks_provider:
                models = flocks_provider.get("models", {})
                if "big-pickle" in models:
                    return {"providerID": "flocks", "modelID": "big-pickle"}
                
                if models:
                    model_id = next(iter(models.keys()))
                    return {"providerID": "flocks", "modelID": model_id}
            
            # Use first available provider
            for provider in providers:
                models = provider.get("models", {})
                if models:
                    model_id = next(iter(models.keys()))
                    return {"providerID": provider.get("id"), "modelID": model_id}
        except Exception as e:
            log.error("providers.get.error", {"error": str(e)})
        
        # Priority 5: Fallback
        return {"providerID": "anthropic", "modelID": "claude-sonnet-4-20250514"}
    
    def shutdown(self) -> None:
        """Shutdown the agent"""
        self._event_abort = True


class ACP:
    """
    ACP namespace
    
    Factory for creating ACP agents.
    Matches TypeScript ACP namespace.
    """
    
    @staticmethod
    async def init(sdk: Any) -> Dict[str, Any]:
        """
        Initialize ACP
        
        Args:
            sdk: Flocks SDK client
            
        Returns:
            Factory with create method
        """
        return {
            "create": lambda connection, config: ACPAgent(connection, config),
        }

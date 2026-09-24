"""
LSP Client implementation

Manages connections to Language Server Protocol servers.
Based on Flocks' ported src/lsp/client.ts
"""

from typing import Dict, List, Optional, Any, Callable, Awaitable
from dataclasses import dataclass, field
from pathlib import Path
import asyncio
import json
import os
import subprocess
from urllib.parse import quote

from flocks.utils.log import Log
from flocks.lsp.language import get_language_for_file, DiagnosticSeverity

log = Log.create(service="lsp.client")


# Timeout for LSP operations
INITIALIZE_TIMEOUT = 45.0  # seconds
DIAGNOSTICS_DEBOUNCE_MS = 150


@dataclass
class Range:
    """LSP Range"""
    start_line: int
    start_character: int
    end_line: int
    end_character: int
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Range":
        return cls(
            start_line=data["start"]["line"],
            start_character=data["start"]["character"],
            end_line=data["end"]["line"],
            end_character=data["end"]["character"],
        )
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": {"line": self.start_line, "character": self.start_character},
            "end": {"line": self.end_line, "character": self.end_character},
        }


@dataclass
class Diagnostic:
    """LSP Diagnostic"""
    range: Range
    message: str
    severity: int = 1  # DiagnosticSeverity
    source: Optional[str] = None
    code: Optional[str] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Diagnostic":
        return cls(
            range=Range.from_dict(data["range"]),
            message=data.get("message", ""),
            severity=data.get("severity", 1),
            source=data.get("source"),
            code=str(data.get("code")) if data.get("code") else None,
        )
    
    def pretty(self) -> str:
        """Format diagnostic for display"""
        severity = DiagnosticSeverity.to_string(self.severity)
        line = self.range.start_line + 1
        col = self.range.start_character + 1
        return f"{severity} [{line}:{col}] {self.message}"


@dataclass
class LSPServerHandle:
    """Handle to a spawned LSP server process"""
    process: subprocess.Popen
    initialization: Optional[Dict[str, Any]] = None


@dataclass
class LSPClientInfo:
    """LSP Client state"""
    server_id: str
    root: str
    _process: subprocess.Popen
    _diagnostics: Dict[str, List[Diagnostic]] = field(default_factory=dict)
    _file_versions: Dict[str, int] = field(default_factory=dict)
    _request_id: int = 0
    _pending_requests: Dict[int, asyncio.Future] = field(default_factory=dict)
    _reader_task: Optional[asyncio.Task] = None
    
    @property
    def diagnostics(self) -> Dict[str, List[Diagnostic]]:
        return self._diagnostics


class LSPClient:
    """
    LSP Client namespace
    
    Manages communication with Language Server Protocol servers
    """
    
    @classmethod
    async def create(
        cls,
        server_id: str,
        server: LSPServerHandle,
        root: str,
    ) -> Optional[LSPClientInfo]:
        """
        Create and initialize an LSP client
        
        Args:
            server_id: Server identifier
            server: Server process handle
            root: Workspace root path
            
        Returns:
            LSPClientInfo or None if initialization failed
        """
        log.info("lsp.client.creating", {"server_id": server_id, "root": root})
        
        client = LSPClientInfo(
            server_id=server_id,
            root=root,
            _process=server.process,
        )
        
        try:
            # Start reader task
            client._reader_task = asyncio.create_task(cls._read_loop(client))
            
            # Initialize server
            root_uri = f"file://{quote(root)}"
            
            init_result = await cls._request(client, "initialize", {
                "rootUri": root_uri,
                "processId": os.getpid(),
                "workspaceFolders": [
                    {"name": "workspace", "uri": root_uri}
                ],
                "initializationOptions": server.initialization or {},
                "capabilities": {
                    "window": {"workDoneProgress": True},
                    "workspace": {
                        "configuration": True,
                        "didChangeWatchedFiles": {"dynamicRegistration": True},
                    },
                    "textDocument": {
                        "synchronization": {"didOpen": True, "didChange": True},
                        "publishDiagnostics": {"versionSupport": True},
                    },
                },
            }, timeout=INITIALIZE_TIMEOUT)
            
            if not init_result:
                raise Exception("Initialize returned empty result")
            
            # Send initialized notification
            await cls._notify(client, "initialized", {})
            
            # Send configuration if provided
            if server.initialization:
                await cls._notify(client, "workspace/didChangeConfiguration", {
                    "settings": server.initialization,
                })
            
            log.info("lsp.client.initialized", {"server_id": server_id})
            return client
            
        except Exception as e:
            log.error("lsp.client.init_failed", {"server_id": server_id, "error": str(e)})
            await cls.shutdown(client)
            return None
    
    @classmethod
    async def _read_loop(cls, client: LSPClientInfo) -> None:
        """Read and process messages from LSP server"""
        try:
            stdout = client._process.stdout
            if not stdout:
                return
            
            while True:
                # Read header
                header = b""
                while b"\r\n\r\n" not in header:
                    chunk = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: stdout.read(1)
                    )
                    if not chunk:
                        return
                    header += chunk
                
                # Parse content length
                content_length = 0
                for line in header.decode().split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        content_length = int(line.split(":")[1].strip())
                        break
                
                if content_length == 0:
                    continue
                
                # Read content
                content = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: stdout.read(content_length)
                )
                
                if not content:
                    return
                
                # Parse JSON
                try:
                    message = json.loads(content.decode())
                    await cls._handle_message(client, message)
                except json.JSONDecodeError as e:
                    log.warn("lsp.client.parse_error", {"error": str(e)})
                    
        except Exception as e:
            log.error("lsp.client.read_error", {"error": str(e)})
    
    @classmethod
    async def _handle_message(cls, client: LSPClientInfo, message: Dict[str, Any]) -> None:
        """Handle incoming LSP message"""
        # Response to our request
        if "id" in message and "result" in message:
            request_id = message["id"]
            if request_id in client._pending_requests:
                client._pending_requests[request_id].set_result(message.get("result"))
                del client._pending_requests[request_id]
            return
        
        # Error response
        if "id" in message and "error" in message:
            request_id = message["id"]
            if request_id in client._pending_requests:
                error = message["error"]
                client._pending_requests[request_id].set_exception(
                    Exception(f"LSP error: {error.get('message', 'Unknown error')}")
                )
                del client._pending_requests[request_id]
            return
        
        # Server -> client request: it carries an id and the server blocks on
        # the reply. pyright, for one, sends workspace/configuration right
        # after initialize and will not answer textDocument/hover until it
        # gets a response, so silently dropping these hangs every later call.
        if "method" in message and "id" in message:
            await cls._handle_server_request(client, message)
            return
        
        # Notification
        if "method" in message:
            method = message["method"]
            params = message.get("params", {})
            
            if method == "textDocument/publishDiagnostics":
                await cls._handle_diagnostics(client, params)
    
    @classmethod
    async def _handle_server_request(cls, client: LSPClientInfo, message: Dict[str, Any]) -> None:
        """Answer a request the server made of us (JSON-RPC requires a reply)."""
        method = message["method"]
        params = message.get("params") or {}
        request_id = message["id"]
        
        if method == "workspace/configuration":
            # One entry per requested item; null means "no client-side setting,
            # use your defaults". Anything we do want the server to know went
            # out as initializationOptions / didChangeConfiguration already.
            items = params.get("items") or []
            result: Any = [None] * len(items)
        elif method in ("client/registerCapability", "client/unregisterCapability",
                        "window/workDoneProgress/create", "window/showMessageRequest"):
            result = None
        elif method == "workspace/workspaceFolders":
            result = [{"name": "workspace", "uri": f"file://{quote(client.root)}"}]
        elif method == "workspace/applyEdit":
            result = {"applied": False}
        else:
            log.warn("lsp.client.unhandled_request", {"server_id": client.server_id, "method": method})
            await cls._send(client, {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })
            return
        
        await cls._send(client, {"jsonrpc": "2.0", "id": request_id, "result": result})
    
    @classmethod
    async def _handle_diagnostics(cls, client: LSPClientInfo, params: Dict[str, Any]) -> None:
        """Handle diagnostics notification"""
        uri = params.get("uri", "")
        diagnostics_data = params.get("diagnostics", [])
        
        # Convert file URI to path
        if uri.startswith("file://"):
            file_path = uri[7:]
            # Handle URL encoding
            from urllib.parse import unquote
            file_path = unquote(file_path)
        else:
            file_path = uri
        
        # Normalize path
        file_path = os.path.normpath(file_path)
        
        # Parse diagnostics
        diagnostics = [Diagnostic.from_dict(d) for d in diagnostics_data]
        client._diagnostics[file_path] = diagnostics
        
        log.info("lsp.client.diagnostics", {
            "path": file_path,
            "count": len(diagnostics),
        })
    
    @classmethod
    async def _request(
        cls,
        client: LSPClientInfo,
        method: str,
        params: Dict[str, Any],
        timeout: float = 30.0,
    ) -> Any:
        """Send request and wait for response"""
        client._request_id += 1
        request_id = client._request_id
        
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        
        # Create future for response
        future = asyncio.get_event_loop().create_future()
        client._pending_requests[request_id] = future
        
        # Send message
        await cls._send(client, message)
        
        # Wait for response with timeout
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            if request_id in client._pending_requests:
                del client._pending_requests[request_id]
            raise
    
    @classmethod
    async def _notify(cls, client: LSPClientInfo, method: str, params: Dict[str, Any]) -> None:
        """Send notification (no response expected)"""
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        await cls._send(client, message)
    
    @classmethod
    async def _send(cls, client: LSPClientInfo, message: Dict[str, Any]) -> None:
        """Send message to LSP server"""
        content = json.dumps(message)
        data = f"Content-Length: {len(content)}\r\n\r\n{content}"
        
        stdin = client._process.stdin
        if stdin:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: stdin.write(data.encode())
            )
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: stdin.flush()
            )
    
    @classmethod
    async def open_file(cls, client: LSPClientInfo, path: str) -> None:
        """
        Notify server that a file was opened
        
        Args:
            client: LSP client
            path: File path
        """
        # Normalize path
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        
        # Read file content
        try:
            content = Path(path).read_text(encoding="utf-8")
        except Exception as e:
            log.warn("lsp.client.read_error", {"path": path, "error": str(e)})
            return
        
        language_id = get_language_for_file(path)
        uri = f"file://{quote(path)}"
        
        version = client._file_versions.get(path, -1)
        
        if version >= 0:
            # File already open, send change notification
            version += 1
            client._file_versions[path] = version
            
            await cls._notify(client, "workspace/didChangeWatchedFiles", {
                "changes": [{"uri": uri, "type": 2}]  # Changed
            })
            
            await cls._notify(client, "textDocument/didChange", {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": content}],
            })
        else:
            # New file, send open notification
            client._file_versions[path] = 0
            client._diagnostics.pop(path, None)
            
            await cls._notify(client, "workspace/didChangeWatchedFiles", {
                "changes": [{"uri": uri, "type": 1}]  # Created
            })
            
            await cls._notify(client, "textDocument/didOpen", {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": 0,
                    "text": content,
                },
            })
        
        log.info("lsp.client.file_opened", {"path": path})
    
    @classmethod
    async def wait_for_diagnostics(
        cls,
        client: LSPClientInfo,
        path: str,
        timeout: float = 3.0,
    ) -> List[Diagnostic]:
        """
        Wait for diagnostics for a file
        
        Args:
            client: LSP client
            path: File path
            timeout: Timeout in seconds
            
        Returns:
            List of diagnostics
        """
        # Normalize path
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        path = os.path.normpath(path)
        
        log.info("lsp.client.waiting_diagnostics", {"path": path})
        
        start_time = asyncio.get_event_loop().time()
        last_count = len(client._diagnostics.get(path, []))
        
        while (asyncio.get_event_loop().time() - start_time) < timeout:
            await asyncio.sleep(DIAGNOSTICS_DEBOUNCE_MS / 1000.0)
            
            current_count = len(client._diagnostics.get(path, []))
            if current_count != last_count:
                # Diagnostics changed, reset debounce
                last_count = current_count
                continue
            
            # Diagnostics stable
            if path in client._diagnostics:
                break
        
        return client._diagnostics.get(path, [])
    
    @classmethod
    async def hover(
        cls,
        client: LSPClientInfo,
        path: str,
        line: int,
        character: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Get hover information at position
        
        Args:
            client: LSP client
            path: File path
            line: Line number (0-based)
            character: Character offset (0-based)
            
        Returns:
            Hover information or None
        """
        uri = f"file://{quote(path)}"
        
        try:
            return await cls._request(client, "textDocument/hover", {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
            })
        except Exception:
            return None
    
    @classmethod
    async def definition(
        cls,
        client: LSPClientInfo,
        path: str,
        line: int,
        character: int,
    ) -> List[Dict[str, Any]]:
        """
        Get definition locations
        
        Args:
            client: LSP client
            path: File path
            line: Line number (0-based)
            character: Character offset (0-based)
            
        Returns:
            List of location objects
        """
        uri = f"file://{quote(path)}"
        
        try:
            result = await cls._request(client, "textDocument/definition", {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
            })
            
            if result is None:
                return []
            if isinstance(result, list):
                return result
            return [result]
        except Exception:
            return []
    
    @classmethod
    async def references(
        cls,
        client: LSPClientInfo,
        path: str,
        line: int,
        character: int,
        include_declaration: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Get reference locations
        
        Args:
            client: LSP client
            path: File path
            line: Line number (0-based)
            character: Character offset (0-based)
            include_declaration: Include declaration in results
            
        Returns:
            List of location objects
        """
        uri = f"file://{quote(path)}"
        
        try:
            result = await cls._request(client, "textDocument/references", {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": include_declaration},
            })
            return result or []
        except Exception:
            return []
    
    @classmethod
    async def workspace_symbol(cls, client: LSPClientInfo, query: str) -> List[Dict[str, Any]]:
        """
        Search for symbols in workspace
        
        Args:
            client: LSP client
            query: Search query
            
        Returns:
            List of symbol objects
        """
        try:
            result = await cls._request(client, "workspace/symbol", {"query": query})
            return result or []
        except Exception:
            return []
    
    @classmethod
    async def document_symbol(cls, client: LSPClientInfo, path: str) -> List[Dict[str, Any]]:
        """
        Get symbols in document
        
        Args:
            client: LSP client
            path: File path
            
        Returns:
            List of symbol objects
        """
        uri = f"file://{quote(path)}"
        
        try:
            result = await cls._request(client, "textDocument/documentSymbol", {
                "textDocument": {"uri": uri},
            })
            return result or []
        except Exception:
            return []
    
    @classmethod
    async def shutdown(cls, client: LSPClientInfo) -> None:
        """
        Shutdown the LSP client
        
        Args:
            client: LSP client
        """
        log.info("lsp.client.shutdown", {"server_id": client.server_id})
        
        # Cancel reader task
        if client._reader_task:
            client._reader_task.cancel()
            try:
                await client._reader_task
            except asyncio.CancelledError:
                pass
        
        # Try to send shutdown request
        try:
            await asyncio.wait_for(cls._request(client, "shutdown", {}), timeout=5.0)
            await cls._notify(client, "exit", {})
        except Exception:
            pass
        
        # Kill process
        try:
            client._process.terminate()
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: client._process.wait(timeout=5.0)
            )
        except Exception:
            client._process.kill()
        
        log.info("lsp.client.shutdown_complete", {"server_id": client.server_id})

"""
PTY (Pseudo-Terminal) management module

Manages pseudo-terminal sessions for running shell commands.
Similar to Flocks' ported src/pty/index.ts
"""

from typing import Dict, List, Optional, Any, Callable, Set
from pydantic import BaseModel, Field
from enum import Enum
import asyncio
import os
import uuid
import subprocess

from flocks.utils.log import Log
from flocks.utils.id import Identifier


log = Log.create(service="pty")


# Buffer configuration matching Flocks
BUFFER_LIMIT = 1024 * 1024 * 2  # 2MB
BUFFER_CHUNK = 64 * 1024  # 64KB
_LOGIN_CAPABLE_SHELL_NAMES = {"bash", "fish", "ksh", "ksh93", "mksh", "sh", "zsh"}


class PtyStatus(str, Enum):
    """PTY session status"""
    RUNNING = "running"
    EXITED = "exited"


class PtyInfo(BaseModel):
    """PTY session information - matches Flocks's Pty.Info"""
    id: str = Field(..., description="PTY session ID")
    title: str = Field(..., description="Session title")
    command: str = Field(..., description="Shell command")
    args: List[str] = Field(default_factory=list, description="Command arguments")
    cwd: str = Field(..., description="Working directory")
    status: PtyStatus = Field(PtyStatus.RUNNING, description="Session status")
    pid: int = Field(..., description="Process ID")


class CreateInput(BaseModel):
    """Input for creating a PTY session - matches Flocks's Pty.CreateInput"""
    command: Optional[str] = Field(None, description="Shell command (defaults to user's shell)")
    args: Optional[List[str]] = Field(None, description="Command arguments")
    cwd: Optional[str] = Field(None, description="Working directory")
    title: Optional[str] = Field(None, description="Session title")
    env: Optional[Dict[str, str]] = Field(None, description="Environment variables")


class UpdateInput(BaseModel):
    """Input for updating a PTY session - matches Flocks's Pty.UpdateInput"""
    title: Optional[str] = Field(None, description="New session title")
    size: Optional[Dict[str, int]] = Field(None, description="Terminal size {rows, cols}")


class ActiveSession:
    """Active PTY session with process and buffer"""
    
    def __init__(
        self,
        info: PtyInfo,
        process: Any,
        reader: Optional[Any] = None,
    ):
        self.info = info
        self.process = process
        self.reader = reader
        self.buffer: str = ""
        self.subscribers: Set[Any] = set()  # WebSocket connections
        self._read_task: Optional[asyncio.Task] = None


class Pty:
    """
    PTY (Pseudo-Terminal) management namespace
    
    Similar to Flocks's Pty namespace in src/pty/index.ts
    """
    
    _sessions: Dict[str, ActiveSession] = {}
    _initialized: bool = False
    
    @classmethod
    def _get_shell(cls) -> str:
        """Get preferred shell"""
        # Try user's shell first
        shell = os.environ.get("SHELL")
        if shell and os.path.exists(shell):
            return shell
        
        # Fallback to common shells
        for shell_path in ["/bin/zsh", "/bin/bash", "/bin/sh"]:
            if os.path.exists(shell_path):
                return shell_path
        
        return "sh"

    @classmethod
    def _validate_process_arguments(cls, command: str, args: List[str]) -> None:
        """Reject values that cannot be passed safely to process creation."""
        if not isinstance(command, str) or not command or "\x00" in command:
            raise ValueError("Invalid PTY command")
        for arg in args:
            if not isinstance(arg, str) or "\x00" in arg:
                raise ValueError("Invalid PTY command argument")

    @classmethod
    def _prepare_environment(cls, input_env: Optional[Dict[str, str]]) -> Dict[str, str]:
        """Build an environment that the platform can pass to the process."""
        env = dict(os.environ)

        if input_env:
            for key, value in input_env.items():
                if not isinstance(key, str) or not key or "\x00" in key:
                    raise ValueError("Invalid PTY environment variable name")
                if not isinstance(value, str) or "\x00" in value:
                    raise ValueError(f"Invalid PTY environment variable value: {key}")
                env[key] = value

        env["TERM"] = "xterm-256color"
        return env
    
    @classmethod
    def list(cls) -> List[PtyInfo]:
        """List all PTY sessions - matches Flocks's Pty.list()"""
        return [session.info for session in cls._sessions.values()]
    
    @classmethod
    def get(cls, pty_id: str) -> Optional[PtyInfo]:
        """Get PTY session info - matches Flocks's Pty.get()"""
        session = cls._sessions.get(pty_id)
        return session.info if session else None
    
    @classmethod
    async def create(cls, input_data: CreateInput) -> PtyInfo:
        """Create a PTY session through the neutral action lifecycle."""
        from flocks.hooks.execution import execute_with_hooks

        return await execute_with_hooks(
            {
                "operation": "pty.open",
                "action": "pty.open",
                "resource": {"type": "pty"},
                "action_input": input_data.model_dump(),
            },
            lambda: cls._create(input_data),
        )

    @classmethod
    async def _create(cls, input_data: CreateInput) -> PtyInfo:
        """Create the process after the public lifecycle adapter allows it."""
        pty_id = Identifier.create("pty")
        command = input_data.command or cls._get_shell()
        args = list(input_data.args) if input_data.args else []
        cls._validate_process_arguments(command, args)
        
        # Add the login flag only for shells known to accept it. Some
        # POSIX-compatible shells (for example dash/ash) reject ``-l``.
        shell_name = os.path.basename(command).lower()
        if (
            shell_name in _LOGIN_CAPABLE_SHELL_NAMES
            and "-l" not in args
            and "--login" not in args
        ):
            args.append("-l")
        
        cwd = input_data.cwd or os.getcwd()
        
        # Prepare environment
        env = cls._prepare_environment(input_data.env)
        
        log.info("pty.creating", {
            "id": pty_id,
            "command": command,
            "args": args,
            "cwd": cwd,
        })
        
        try:
            # Try to use ptyprocess for real PTY
            from ptyprocess import PtyProcess
            
            # Combine command and args for ptyprocess
            cmd = [command] + args
            process = PtyProcess.spawn(
                cmd,
                cwd=cwd,
                env=env,
                dimensions=(24, 80),  # Default rows, cols
            )
            pid = process.pid
            
        except ImportError:
            # Fallback to subprocess if ptyprocess not available
            log.warn("pty.fallback", {"reason": "ptyprocess not installed"})
            
            process = await asyncio.create_subprocess_exec(
                command,
                *args,
                cwd=cwd,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            pid = process.pid
        
        # Create session info
        info = PtyInfo(
            id=pty_id,
            title=input_data.title or f"Terminal {pty_id[-4:]}",
            command=command,
            args=args,
            cwd=cwd,
            status=PtyStatus.RUNNING,
            pid=pid,
        )
        
        # Create active session
        session = ActiveSession(info=info, process=process)
        cls._sessions[pty_id] = session
        
        # Start reading output in background
        session._read_task = asyncio.create_task(cls._read_output(session))
        
        log.info("pty.created", {"id": pty_id, "pid": pid})
        return info
    
    @classmethod
    async def _read_output(cls, session: ActiveSession) -> None:
        """Read process output and buffer/distribute it"""
        try:
            while session.info.status == PtyStatus.RUNNING:
                try:
                    # Check if using ptyprocess or subprocess
                    if hasattr(session.process, 'read'):
                        # ptyprocess
                        try:
                            data = session.process.read(4096)
                            if not data:
                                break
                        except EOFError:
                            break
                    else:
                        # subprocess
                        data = await session.process.stdout.read(4096)
                        if not data:
                            break
                        data = data.decode('utf-8', errors='replace')
                    
                    # Distribute to subscribers
                    has_open = False
                    for ws in list(session.subscribers):
                        try:
                            await ws.send_text(data)
                            has_open = True
                        except Exception:
                            session.subscribers.discard(ws)
                    
                    # Buffer if no subscribers
                    if not has_open:
                        session.buffer += data
                        if len(session.buffer) > BUFFER_LIMIT:
                            session.buffer = session.buffer[-BUFFER_LIMIT:]
                            
                except Exception as e:
                    log.error("pty.read.error", {"id": session.info.id, "error": str(e)})
                    break
                    
                await asyncio.sleep(0.01)  # Small delay to prevent CPU spinning
                
        except Exception as e:
            log.error("pty.read.task.error", {"id": session.info.id, "error": str(e)})
        finally:
            session.info.status = PtyStatus.EXITED
            log.info("pty.exited", {"id": session.info.id})
    
    @classmethod
    async def update(cls, pty_id: str, input_data: UpdateInput) -> Optional[PtyInfo]:
        """
        Update PTY session - matches Flocks's Pty.update()
        
        Args:
            pty_id: Session ID
            input_data: Update parameters
            
        Returns:
            Updated session info or None if not found
        """
        session = cls._sessions.get(pty_id)
        if not session:
            return None
        
        if input_data.title:
            session.info.title = input_data.title
        
        if input_data.size and hasattr(session.process, 'setwinsize'):
            rows = input_data.size.get("rows", 24)
            cols = input_data.size.get("cols", 80)
            session.process.setwinsize(rows, cols)
        
        log.info("pty.updated", {"id": pty_id})
        return session.info
    
    @classmethod
    async def remove(cls, pty_id: str) -> None:
        """
        Remove/terminate PTY session - matches Flocks's Pty.remove()
        
        Args:
            pty_id: Session ID
        """
        session = cls._sessions.get(pty_id)
        if not session:
            return
        
        log.info("pty.removing", {"id": pty_id})
        
        # Cancel read task
        if session._read_task:
            session._read_task.cancel()
            try:
                await session._read_task
            except asyncio.CancelledError:
                pass
        
        # Kill process
        try:
            if hasattr(session.process, 'terminate'):
                session.process.terminate(force=True)
            elif hasattr(session.process, 'kill'):
                session.process.kill()
        except Exception:
            pass
        
        # Close WebSocket connections
        for ws in list(session.subscribers):
            try:
                await ws.close()
            except Exception:
                pass
        
        # Remove from sessions
        del cls._sessions[pty_id]
        log.info("pty.removed", {"id": pty_id})
    
    @classmethod
    def resize(cls, pty_id: str, cols: int, rows: int) -> None:
        """
        Resize PTY terminal - matches Flocks's Pty.resize()
        
        Args:
            pty_id: Session ID
            cols: Number of columns
            rows: Number of rows
        """
        session = cls._sessions.get(pty_id)
        if session and session.info.status == PtyStatus.RUNNING:
            if hasattr(session.process, 'setwinsize'):
                session.process.setwinsize(rows, cols)
    
    @classmethod
    async def write(cls, pty_id: str, data: str) -> None:
        """Write terminal bytes through the neutral action lifecycle."""
        from flocks.hooks.execution import execute_with_hooks

        await execute_with_hooks(
            {
                "operation": "pty.input",
                "action": "pty.input",
                "resource": {"type": "pty", "id": pty_id},
                "action_input": {"data": data},
            },
            lambda: cls._write_async(pty_id, data),
        )

    @classmethod
    async def _write_async(cls, pty_id: str, data: str) -> None:
        """Adapt the synchronous process write to the async lifecycle API."""
        cls._write(pty_id, data)

    @classmethod
    def _write(cls, pty_id: str, data: str) -> None:
        """Write raw data only after the public lifecycle adapter allows it."""
        session = cls._sessions.get(pty_id)
        if session and session.info.status == PtyStatus.RUNNING:
            try:
                if hasattr(session.process, 'write'):
                    # ptyprocess
                    session.process.write(data)
                elif hasattr(session.process, 'stdin'):
                    # subprocess
                    session.process.stdin.write(data.encode())
            except Exception as e:
                log.error("pty.write.error", {"id": pty_id, "error": str(e)})
    
    @classmethod
    async def connect(cls, pty_id: str, ws: Any) -> Optional[Dict[str, Callable]]:
        """
        Connect WebSocket to PTY session - matches Flocks's Pty.connect()
        
        Args:
            pty_id: Session ID
            ws: WebSocket connection
            
        Returns:
            Dictionary with onMessage and onClose handlers, or None if not found
        """
        session = cls._sessions.get(pty_id)
        if not session:
            await ws.close()
            return None
        
        log.info("pty.client.connected", {"id": pty_id})
        session.subscribers.add(ws)
        
        # Send buffered data
        if session.buffer:
            buffer = session.buffer if len(session.buffer) <= BUFFER_LIMIT else session.buffer[-BUFFER_LIMIT:]
            session.buffer = ""
            try:
                # Send in chunks
                for i in range(0, len(buffer), BUFFER_CHUNK):
                    await ws.send_text(buffer[i:i + BUFFER_CHUNK])
            except Exception:
                session.subscribers.discard(ws)
                session.buffer = buffer
                await ws.close()
                return None
        
        async def on_message(message: str) -> None:
            """Handle incoming message from WebSocket"""
            await cls.write(pty_id, message)
        
        def on_close() -> None:
            """Handle WebSocket close"""
            log.info("pty.client.disconnected", {"id": pty_id})
            session.subscribers.discard(ws)
        
        return {
            "onMessage": on_message,
            "onClose": on_close,
        }

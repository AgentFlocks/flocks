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
# Each running PTY is a live shell process plus a reader task; without a cap a
# client (or a runaway agent loop) can open shells until the host runs out of
# process slots. Override with FLOCKS_PTY_MAX_SESSIONS (0 or negative = unlimited).
DEFAULT_MAX_SESSIONS = 32
MAX_SESSIONS_ENV = "FLOCKS_PTY_MAX_SESSIONS"


class PtyLimitExceeded(RuntimeError):
    """Raised when creating a PTY would exceed the running-session cap."""


def max_sessions() -> Optional[int]:
    """Configured cap on concurrently running PTY sessions (None = unlimited)."""
    raw = os.environ.get(MAX_SESSIONS_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_MAX_SESSIONS
    try:
        value = int(raw)
    except ValueError:
        log.warn("pty.max_sessions.invalid", {"value": raw, "default": DEFAULT_MAX_SESSIONS})
        return DEFAULT_MAX_SESSIONS
    return value if value > 0 else None


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
            # Reject dangerous host env vars (LD_PRELOAD / DYLD_* / PATH / PYTHONPATH ...)
            # supplied by the caller before merging them into the child environment.
            from flocks.sandbox.env_security import validate_host_env
            validate_host_env(input_env)
            env.update(input_env)

        env["TERM"] = "xterm-256color"
        return env

    @classmethod
    def _execution_profile(cls, workspace_dir: str) -> Dict[str, Any]:
        """Execution profile for the synthetic ``pty`` session.

        PTY has no persisted session, so the tool-lifecycle payload would carry
        no actor subject and policy gates that require a trusted subject deny
        the call. Bind the request's authenticated user instead.
        """
        profile: Dict[str, Any] = {
            "entry": "pty",
            "workspace_dir": workspace_dir,
            "permission_mode": "require-confirm",
            "runtime_mode": "dev-mode",
            "network_mode": "require-confirm",
        }
        try:
            from flocks.auth.context import get_current_auth_user

            user = get_current_auth_user()
        except Exception:
            user = None
        if user is not None:
            profile["subject_id"] = str(getattr(user, "id", "") or "")
            profile["owner_user_id"] = str(getattr(user, "id", "") or "")
            profile["owner_username"] = str(getattr(user, "username", "") or "")
        return profile
    
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
    async def create(
        cls,
        input_data: CreateInput,
        *,
        use_action_lifecycle: bool = True,
    ) -> PtyInfo:
        """Create a PTY session, optionally applying execution policy hooks."""
        if not use_action_lifecycle:
            return await cls._create(input_data)

        command = input_data.command or cls._get_shell()
        args = list(input_data.args or [])
        cls._validate_process_arguments(command, args)
        normalized_input = {
            **input_data.model_dump(),
            "command": command,
            "args": args,
        }
        from flocks.session.tool_execution import (
            build_session_tool_execution_payload,
            run_tool_execution_lifecycle,
        )
        call_id = Identifier.create("pty")
        payload = await build_session_tool_execution_payload(
            session_id="pty",
            message_id=call_id,
            agent="pty",
            tool_name="pty.open",
            tool_input=input_data.model_dump(),
            validated_input=normalized_input,
            tool_schema=CreateInput.model_json_schema(),
            tool_context_extra={
                "tool_source": "pty",
                "tool_category": "command",
                "workspace_dir": input_data.cwd or os.getcwd(),
                # Durable audit (Pro) records the attempt under the call id;
                # without one the gate refuses to start the tool.
                "tool_call_id": call_id,
                "session_execution_profile": cls._execution_profile(input_data.cwd or os.getcwd()),
            },
        )

        async def _patched_effect(patch: Dict[str, Any]) -> PtyInfo:
            patched = CreateInput.model_validate({**normalized_input, **patch})
            cls._validate_process_arguments(
                patched.command or cls._get_shell(),
                list(patched.args or []),
            )
            return await cls._create(patched)

        return await run_tool_execution_lifecycle(
            payload,
            lambda: cls._create(input_data),
            patched_effect=_patched_effect,
        )

    @classmethod
    def running_count(cls) -> int:
        """Sessions whose shell is still alive (exited ones hold no process)."""
        return sum(1 for session in cls._sessions.values() if session.info.status == PtyStatus.RUNNING)

    @classmethod
    def _assert_capacity(cls) -> None:
        cap = max_sessions()
        if cap is None:
            return
        running = cls.running_count()
        if running >= cap:
            raise PtyLimitExceeded(
                f"PTY session limit reached ({running}/{cap} running); "
                "close an existing session before opening another"
            )

    @classmethod
    async def _create(cls, input_data: CreateInput) -> PtyInfo:
        """Create the process after the public lifecycle adapter allows it."""
        pty_id = Identifier.create("pty")
        command = input_data.command or cls._get_shell()
        args = list(input_data.args) if input_data.args else []
        cls._validate_process_arguments(command, args)
        # Checked before the shell is spawned so a refused request leaves no
        # process behind; the count only drops when a session exits or is
        # removed, so "close one, open one" keeps working at the cap.
        cls._assert_capacity()
        
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
        if not os.path.isdir(cwd):
            # ptyprocess only fails inside the forked child, which would leave a
            # session that is "exited" before the caller even sees it.
            raise ValueError(f"Working directory does not exist: {cwd}")
        
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
                        # ptyprocess: ``read`` blocks until the child writes, so
                        # run it in a worker thread — a silent terminal must not
                        # freeze the server event loop (and every other request).
                        try:
                            data = await asyncio.to_thread(session.process.read, 4096)
                            if not data:
                                break
                        except EOFError:
                            break
                        if isinstance(data, bytes):
                            # PtyProcess.read yields bytes; buffer/WebSocket are text.
                            data = data.decode('utf-8', errors='replace')
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
    async def write(
        cls,
        pty_id: str,
        data: str,
        *,
        use_action_lifecycle: bool = True,
    ) -> None:
        """Write terminal bytes, optionally applying execution policy hooks."""
        if not use_action_lifecycle:
            await cls._write_async(pty_id, data)
            return

        from flocks.session.tool_execution import (
            build_session_tool_execution_payload,
            run_tool_execution_lifecycle,
        )
        call_id = Identifier.create("pty")
        payload = await build_session_tool_execution_payload(
            session_id="pty",
            message_id=call_id,
            agent="pty",
            tool_name="pty.input",
            tool_input={"data": data, "pty_id": pty_id},
            validated_input={"data": data, "pty_id": pty_id},
            tool_schema={
                "type": "object",
                "properties": {
                    "data": {"type": "string"},
                    "pty_id": {"type": "string"},
                },
                "required": ["data", "pty_id"],
            },
            tool_context_extra={
                "tool_source": "pty",
                "tool_category": "command",
                "tool_call_id": call_id,
                "session_execution_profile": cls._execution_profile(os.getcwd()),
            },
        )

        async def _patched_effect(patch: Dict[str, Any]) -> None:
            patched_data = patch.get("data", data)
            if not isinstance(patched_data, str):
                raise ValueError("PTY hook patch must contain string data")
            await cls._write_async(pty_id, patched_data)

        await run_tool_execution_lifecycle(
            payload,
            lambda: cls._write_async(pty_id, data),
            patched_effect=_patched_effect,
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
                    # ptyprocess: PtyProcess.write expects bytes
                    session.process.write(data.encode('utf-8') if isinstance(data, str) else data)
                elif hasattr(session.process, 'stdin'):
                    # subprocess
                    session.process.stdin.write(data.encode())
            except Exception as e:
                log.error("pty.write.error", {"id": pty_id, "error": str(e)})
    
    @classmethod
    async def connect(
        cls,
        pty_id: str,
        ws: Any,
        *,
        use_action_lifecycle: bool = True,
    ) -> Optional[Dict[str, Callable]]:
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
            await cls.write(
                pty_id,
                message,
                use_action_lifecycle=use_action_lifecycle,
            )
        
        def on_close() -> None:
            """Handle WebSocket close"""
            log.info("pty.client.disconnected", {"id": pty_id})
            session.subscribers.discard(ws)
        
        return {
            "onMessage": on_message,
            "onClose": on_close,
        }

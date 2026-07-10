"""
Bash Tool - Shell command execution

Executes bash commands with:
- Configurable timeout
- Working directory support
- Output streaming
- Permission system integration
- Sandbox support (Docker container isolation, aligned with OpenClaw)
"""

import os
import sys
import asyncio
import subprocess
import shlex
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from flocks.sandbox.types import BashSandboxConfig

from flocks.tool.registry import ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
from flocks.project.instance import Instance
from flocks.tool.path_utils import get_tool_base_dir, resolve_host_path
from flocks.utils.log import Log


log = Log.create(service="tool.bash")


# Constants
MAX_METADATA_LENGTH = 30_000
DEFAULT_TIMEOUT_MS = 2 * 60 * 1000  # 2 minutes
MAX_OUTPUT_LINES = 1000
MAX_OUTPUT_BYTES = 100 * 1024  # 100KB
DEFAULT_PATH = os.environ.get(
    "PATH",
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
)


def get_description(directory: str) -> str:
    """Get the platform-specific tool description."""
    if sys.platform == "win32":
        return _get_powershell_description(directory)
    return _get_unix_shell_description(directory)


def _get_unix_shell_description(directory: str) -> str:
    """Build the Unix shell description for the bash tool."""
    return f"""Execute shell commands with optional timeout.

All commands run in {directory} by default. Use the `workdir` parameter if you need a different directory. Avoid `cd <directory> && <command>` patterns and set `workdir` instead.

Use this tool for terminal work such as git, uv/pip/npm, docker, builds, tests, servers, scripts, process inspection, system status, networking commands, and shell pipelines or compound commands.

Do not use this tool when a dedicated tool is a better fit:
- Read file contents -> `read`
- Write a new file -> `write`
- Edit an existing file -> `edit`
- Search file names or directories -> `glob`
- Search file contents -> `grep`

Before executing commands:
1. If the command will create files or directories, verify the target location.
2. Always quote file paths that contain spaces with double quotes.
   - `mkdir "/Users/name/My Documents"` (correct)
   - `mkdir /Users/name/My Documents` (incorrect)

Usage notes:
- The `command` argument is required.
- You can specify an optional timeout in milliseconds. If not specified, commands time out after {DEFAULT_TIMEOUT_MS}ms.
- It is very helpful to write a clear, concise `description` in 5-10 words.
- If the output exceeds {MAX_OUTPUT_LINES} lines or {MAX_OUTPUT_BYTES} bytes, it will be truncated and the full output will be written to a file.
- Prefer dedicated tools instead of shell equivalents: use `glob` instead of `find` or `ls`, `grep` instead of shell `grep`/`rg`, `read` instead of `cat`/`head`/`tail`, `edit` instead of `sed`/`awk`, and `write` instead of shell redirection or `echo`-based file creation.
- If commands are independent, make multiple bash tool calls in one message so they can run in parallel.
- If commands depend on each other, use a single bash tool call with `&&` to chain them together.
- Use `;` only when you want sequential commands and do not care if earlier ones fail."""


def _detect_windows_powershell_shell() -> Optional[str]:
    """Detect the preferred PowerShell executable on Windows."""
    for shell in ["pwsh", "powershell"]:
        if shutil_which(shell):
            return shell
    return None


def _get_windows_powershell_51_guidance() -> str:
    """Return Windows PowerShell 5.1 specific notes when relevant."""
    if _detect_windows_powershell_shell() != "powershell":
        return ""

    return """

Windows PowerShell 5.1 notes:
- Pipeline chain operators `&&` and `||` are not available. Run `A; if ($?) {{ B }}` for conditional chaining, or `A; B` for unconditional chaining.
- Avoid `2>&1` on native executables. PowerShell 5.1 may wrap stderr in `NativeCommandError` records and set `$?` to `$false` even when the executable exits with code 0. stderr is already captured for you.
- Default file encoding is UTF-16 LE with BOM. If you absolutely must write text for another tool to consume, pass `-Encoding utf8`.
- `ConvertFrom-Json` returns `PSCustomObject`; `-AsHashtable` is not available.
"""


def _get_powershell_description(directory: str) -> str:
    """Build the Windows PowerShell description for the bash tool."""
    powershell_51_guidance = _get_windows_powershell_51_guidance()
    return f"""Execute PowerShell commands with optional timeout.

All commands run in {directory} by default. Use the `workdir` parameter if you need a different directory. Do not prefix commands with `cd` or `Set-Location`; set `workdir` instead.

Use this tool for terminal work via PowerShell: git, uv/pip/npm, docker, builds, tests, servers, scripts, process inspection, system status, networking commands, native executables, and PowerShell cmdlets.

IMPORTANT: This tool is for terminal operations. Do not use PowerShell for file operations when a dedicated tool is a better fit:
- Read file contents -> `read`
- Write a new file -> `write`
- Edit an existing file -> `edit`
- Search file names or directories -> `glob`
- Search file contents -> `grep`

Before executing commands:
1. If the command will create files or directories, verify the target location first.
2. Always quote file paths that contain spaces with double quotes.
   - `New-Item -ItemType Directory "C:\\Users\\...\\My Documents"` (correct)
   - `New-Item -ItemType Directory C:\\Users\\...\\My Documents` (incorrect)

PowerShell syntax notes:
- Variables use the `$` prefix: `$name = "value"`.
- The escape character is backtick (`` ` ``), not backslash.
- Prefer Verb-Noun cmdlets such as `Get-ChildItem`, `Set-Location`, `New-Item`, `Remove-Item`.
- Pipes pass objects, not plain text. Use `Select-Object`, `Where-Object`, and `ForEach-Object` for filtering and transformation.
- Read environment variables with `$env:NAME`; set them with `$env:NAME = "value"`.
- Use the call operator for native executables whose path contains spaces: `& "C:\\Program Files\\App\\app.exe" arg1 arg2`.
- Avoid bash-only syntax such as `export NAME=value`, `cat <<'EOF'`, backtick command substitution, or `if [ -f x ]`.

Unix to PowerShell quick reference:
- `head` / `tail` -> `Get-Content file -TotalCount N` / `Get-Content file -Tail N`
- `which` -> `(Get-Command name).Source`
- `touch` -> `if (-not (Test-Path path)) {{ New-Item -ItemType File path }}`
- `wc -l` -> `(Get-Content file | Measure-Object -Line).Lines`
- `mkdir -p` -> `New-Item -ItemType Directory -Force path`
- `rm -rf` -> `Remove-Item -Recurse -Force path`
- `VAR=x cmd` -> `$env:VAR = 'x'; cmd`
- `2>/dev/null` -> `2>$null` (usually unnecessary because stderr is already captured)

Interactive and blocking commands:
- Never use `Read-Host`, `Get-Credential`, `Out-GridView`, `$Host.UI.PromptForChoice`, or `pause`.
- Destructive cmdlets may prompt for confirmation. Add `-Confirm:$false` when you intend the action to proceed.

Passing multiline strings to native executables:
- Use a single-quoted here-string so PowerShell does not expand `$` or backticks inside:
  `git commit -m @'`
  `Commit message here.`
  `'@`
- The closing `'@` must start at column 0 on its own line.
- Use `@'...'@` unless you explicitly need interpolation.
- For arguments PowerShell may parse as operators, use the stop-parsing token: `git log --% --format=%H`.

Usage notes:
- The `command` argument is required.
- You can specify an optional timeout in milliseconds. If not specified, commands time out after {DEFAULT_TIMEOUT_MS}ms.
- It is very helpful to write a clear, concise `description` in 5-10 words.
- If the output exceeds {MAX_OUTPUT_LINES} lines or {MAX_OUTPUT_BYTES} bytes, it will be truncated and the full output will be written to a file.
- Avoid unnecessary `Start-Sleep`. If commands can run immediately, run them immediately. If you must wait, prefer a short polling check over sleeping first.
- If commands are independent, make multiple bash tool calls in one message so they can run in parallel.
- If commands depend on each other, chain them in one bash tool call. On PowerShell 7+, `&&` is available. On Windows PowerShell 5.1, use `A; if ($?) {{ B }}` instead.
- Use `;` only when you want sequential commands and do not care if earlier ones fail.{powershell_51_guidance}"""


def _build_error_message(
    *,
    output: str,
    exit_code: Optional[int],
    timeout_ms: int,
    timed_out: bool,
    aborted: bool,
) -> str:
    """Build a concise failure message from bash execution details."""
    if timed_out:
        return f"Command timed out after {timeout_ms} ms"
    if aborted:
        return "Command was aborted"

    output_text = output.strip()
    if output_text:
        if exit_code is not None:
            return f"Command failed with exit code {exit_code}\n\n{output_text}"
        return output_text

    if exit_code is not None:
        return f"Command failed with exit code {exit_code}"
    return "Command failed"


def get_shell() -> str:
    """Get the appropriate shell for the current platform"""
    if sys.platform == "win32":
        shell = _detect_windows_powershell_shell()
        if shell:
            return shell
        raise FileNotFoundError("PowerShell executable not found (expected `pwsh` or `powershell` in PATH)")
    else:
        # Unix-like systems
        return os.environ.get("SHELL", "/bin/bash")


def shutil_which(cmd: str) -> Optional[str]:
    """Cross-platform which command"""
    import shutil

    return shutil.which(cmd)


def _get_windows_shell_command(command: str) -> tuple[str, list[str]]:
    """Build an explicit Windows shell invocation for the command."""
    shell = get_shell()
    return shell, [shell, "-NoProfile", "-NonInteractive", "-Command", command]


def _get_windows_default_workdir_fallback() -> Optional[str]:
    """Return a safe existing directory for Windows host commands."""
    for candidate in (
        os.environ.get("USERPROFILE"),
        os.path.expanduser("~"),
        tempfile.gettempdir(),
        "C:\\",
    ):
        if candidate and os.path.isdir(candidate):
            return candidate
    return None


def _resolve_workdir(base_dir: str, workdir: Optional[str]) -> str:
    """Resolve command working directory, with Windows-only default cwd fallback."""
    cwd = resolve_host_path(workdir or base_dir, base_dir=base_dir)

    if sys.platform == "win32" and workdir is None and not os.path.isdir(cwd):
        if fallback := _get_windows_default_workdir_fallback():
            log.warn("bash.invalid_default_cwd_fallback", {"invalid_cwd": cwd, "fallback": fallback})
            return fallback

    return cwd


async def kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """
    Kill a process and all its children

    Args:
        proc: Process to kill
    """
    try:
        if sys.platform == "win32":
            # Windows: use taskkill
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            # Unix: send SIGTERM to process group
            import signal

            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass

            # Wait briefly for graceful shutdown
            await asyncio.sleep(0.1)

            # Force kill if still running
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
    except Exception as e:
        log.warn("kill_process_tree.failed", {"error": str(e)})


def _get_sandbox_config_from_ctx(ctx: ToolContext) -> Optional["BashSandboxConfig"]:
    """从 ToolContext.extra 中提取沙箱配置."""
    sandbox_data = ctx.extra.get("sandbox") if ctx.extra else None
    if not sandbox_data:
        return None

    from flocks.sandbox.types import BashSandboxConfig

    if isinstance(sandbox_data, BashSandboxConfig):
        return sandbox_data
    if isinstance(sandbox_data, dict):
        return BashSandboxConfig(**sandbox_data)
    return None


def _is_elevated_allowed(ctx: ToolContext, tool_name: str) -> bool:
    """Check whether elevated host execution is allowed in sandbox mode."""
    elevated = ctx.extra.get("sandbox_elevated") if ctx.extra else None
    if not isinstance(elevated, dict):
        return False
    if not elevated.get("enabled", False):
        return False
    allowed_tools = elevated.get("tools") or ["bash"]
    return tool_name in allowed_tools


async def _resolve_sandbox_workdir(
    workdir: str,
    sandbox: "BashSandboxConfig",
) -> tuple[str, str]:
    """
    解析沙箱内的工作目录。

    对齐 OpenClaw resolveSandboxWorkdir (bash-tools.shared.ts)。

    Returns:
        (host_workdir, container_workdir)
    """
    from flocks.sandbox.paths import assert_sandbox_path

    fallback = sandbox.workspace_dir
    try:
        result = await assert_sandbox_path(
            file_path=workdir,
            cwd=os.getcwd(),
            root=sandbox.workspace_dir,
        )
        if not os.path.isdir(result.resolved):
            raise ValueError("workdir is not a directory")

        # 将相对路径映射为容器路径
        relative = result.relative.replace(os.sep, "/") if result.relative else ""
        container_workdir = f"{sandbox.container_workdir}/{relative}" if relative else sandbox.container_workdir
        return result.resolved, container_workdir
    except (ValueError, OSError):
        return fallback, sandbox.container_workdir


@ToolRegistry.register_function(
    name="bash",
    description=get_description(os.getcwd()),
    category=ToolCategory.TERMINAL,
    parameters=[
        ToolParameter(name="command", type=ParameterType.STRING, description="The command to execute", required=True),
        ToolParameter(
            name="timeout",
            type=ParameterType.INTEGER,
            description="Optional timeout in milliseconds",
            required=False,
            default=DEFAULT_TIMEOUT_MS,
        ),
        ToolParameter(
            name="workdir",
            type=ParameterType.STRING,
            description="The working directory to run the command in. It may be absolute, use `~`, or be relative to the current project directory.",
            required=False,
        ),
        ToolParameter(
            name="description",
            type=ParameterType.STRING,
            description="Clear, concise description of what this command does in 5-10 words",
            required=False,
        ),
        ToolParameter(
            name="host",
            type=ParameterType.STRING,
            description="Execution host override: 'sandbox' (default) or 'host' (elevated when sandbox is active)",
            required=False,
            enum=["sandbox", "host"],
        ),
    ],
)
async def bash_tool(
    ctx: ToolContext,
    command: str,
    timeout: Optional[int] = None,
    workdir: Optional[str] = None,
    description: Optional[str] = None,
    host: Optional[str] = None,
) -> ToolResult:
    """
    Execute a bash command.

    Supports two execution paths:
    1. Host execution (default) - directly on the host machine
    2. Sandbox execution - inside a Docker container (when sandbox config is present)
    """
    # Resolve working directory
    base_dir = get_tool_base_dir()
    cwd = _resolve_workdir(base_dir, workdir)

    # Validate timeout
    timeout_ms = timeout or DEFAULT_TIMEOUT_MS
    if timeout_ms < 0:
        return ToolResult(
            success=False, error=f"Invalid timeout value: {timeout_ms}. Timeout must be a positive number."
        )

    timeout_sec = timeout_ms / 1000

    # Check for sandbox configuration
    sandbox = _get_sandbox_config_from_ctx(ctx)

    if sandbox:
        desired_host = (host or "sandbox").strip().lower()
        if desired_host == "host":
            if not _is_elevated_allowed(ctx, "bash"):
                return ToolResult(
                    success=False,
                    error=(
                        "Elevated host execution is not allowed for bash in current sandbox policy. "
                        "Enable sandbox.elevated.enabled and include 'bash' in sandbox.elevated.tools."
                    ),
                    title=description or command,
                    metadata={"sandbox": True, "elevated_requested": True},
                )
            return await _execute_host(
                ctx=ctx,
                command=command,
                cwd=cwd,
                timeout_sec=timeout_sec,
                timeout_ms=timeout_ms,
                description=description,
                extra_metadata={"sandbox": True, "elevated": True},
            )
        return await _execute_sandboxed(
            ctx=ctx,
            command=command,
            cwd=cwd,
            sandbox=sandbox,
            timeout_sec=timeout_sec,
            timeout_ms=timeout_ms,
            description=description,
        )
    else:
        return await _execute_host(
            ctx=ctx,
            command=command,
            cwd=cwd,
            timeout_sec=timeout_sec,
            timeout_ms=timeout_ms,
            description=description,
        )


async def _execute_host(
    ctx: ToolContext,
    command: str,
    cwd: str,
    timeout_sec: float,
    timeout_ms: int,
    description: Optional[str],
    extra_metadata: Optional[dict] = None,
) -> ToolResult:
    """在宿主机上执行命令（原有逻辑）."""
    # Check if working directory is outside project
    if not Instance.contains_path(cwd):
        await ctx.ask(permission="external_directory", patterns=[cwd], always=[os.path.dirname(cwd) + "*"], metadata={})

    # Request bash permission
    await ctx.ask(permission="bash", patterns=[command], always=[], metadata={})

    # Initialize metadata
    ctx.metadata(
        {
            "metadata": {
                "output": "",
                "description": description or command,
                **(extra_metadata or {}),
            }
        }
    )

    # Build environment with UTF-8 encoding for Windows
    env = None
    if sys.platform == "win32":
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

    # Execute command
    try:
        if sys.platform == "win32":
            shell_name, shell_cmd = _get_windows_shell_command(command)
            log.info(
                "bash.execute.host",
                {"command": command, "cwd": cwd, "shell": shell_name, "shell_cmd": shell_cmd[:-1]},
            )
            proc = await asyncio.create_subprocess_exec(
                *shell_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        else:
            shell = get_shell()
            log.info("bash.execute.host", {"command": command, "cwd": cwd, "shell": shell})
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                start_new_session=True,  # Create new process group
            )
    except Exception as e:
        return ToolResult(success=False, error=f"Failed to start command: {str(e)}", title=description or command)

    return await _stream_output(
        ctx=ctx,
        proc=proc,
        command=command,
        timeout_sec=timeout_sec,
        timeout_ms=timeout_ms,
        description=description,
        extra_metadata=extra_metadata,
    )


async def _execute_sandboxed(
    ctx: ToolContext,
    command: str,
    cwd: str,
    sandbox: "BashSandboxConfig",
    timeout_sec: float,
    timeout_ms: int,
    description: Optional[str],
) -> ToolResult:
    """
    在沙箱容器内执行命令。

    对齐 OpenClaw bash-tools.exec.ts sandbox 路径:
    - 使用 docker exec 在容器内运行
    - 路径映射 host → container
    - 构建隔离环境变量
    """
    from flocks.sandbox.docker import build_docker_exec_args, build_sandbox_env

    log.info(
        "bash.execute.sandbox",
        {
            "command": command,
            "container": sandbox.container_name,
        },
    )

    # Request bash permission (沙箱内也需要权限)
    await ctx.ask(permission="bash", patterns=[command], always=[], metadata={"sandbox": True})

    # Initialize metadata
    ctx.metadata(
        {
            "metadata": {
                "output": "",
                "description": description or command,
                "sandbox": True,
                "container": sandbox.container_name,
            }
        }
    )

    # 解析工作目录 (host → container 路径映射)
    host_workdir, container_workdir = await _resolve_sandbox_workdir(cwd, sandbox)

    # 构建沙箱环境变量
    env = build_sandbox_env(
        default_path=DEFAULT_PATH,
        sandbox_env=sandbox.env,
        container_workdir=container_workdir,
    )

    # 构建 docker exec 参数
    docker_args = build_docker_exec_args(
        container_name=sandbox.container_name,
        command=command,
        workdir=container_workdir,
        env=env,
        tty=False,
    )

    # 使用 docker exec 执行
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *docker_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=host_workdir,
            start_new_session=True if sys.platform != "win32" else False,
        )
    except Exception as e:
        return ToolResult(
            success=False,
            error=f"Failed to start sandboxed command: {str(e)}",
            title=description or command,
            metadata={"sandbox": True, "container": sandbox.container_name},
        )

    return await _stream_output(
        ctx=ctx,
        proc=proc,
        command=command,
        timeout_sec=timeout_sec,
        timeout_ms=timeout_ms,
        description=description,
        extra_metadata={"sandbox": True, "container": sandbox.container_name},
    )


async def _stream_output(
    ctx: ToolContext,
    proc: asyncio.subprocess.Process,
    command: str,
    timeout_sec: float,
    timeout_ms: int,
    description: Optional[str],
    extra_metadata: Optional[dict] = None,
) -> ToolResult:
    """流式读取进程输出并返回结果（host 和 sandbox 共用）."""
    output = ""
    timed_out = False
    aborted = False

    def update_output_metadata() -> None:
        truncated_output = output
        if len(truncated_output) > MAX_METADATA_LENGTH:
            truncated_output = truncated_output[:MAX_METADATA_LENGTH] + "\n\n..."

        ctx.metadata(
            {
                "metadata": {
                    "output": truncated_output,
                    "description": description or command,
                    **(extra_metadata or {}),
                }
            }
        )

    async def read_stream(stream: asyncio.StreamReader) -> None:
        nonlocal output
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            output += chunk.decode("utf-8", errors="replace")
            update_output_metadata()

    async def wait_for_abort() -> None:
        while not ctx.aborted:
            await asyncio.sleep(0.1)

    stream_tasks = [
        asyncio.create_task(read_stream(proc.stdout)),
        asyncio.create_task(read_stream(proc.stderr)),
    ]
    wait_task = asyncio.create_task(proc.wait())
    completion_task = asyncio.gather(wait_task, *stream_tasks)
    abort_task = asyncio.create_task(wait_for_abort())

    try:
        done, _pending = await asyncio.wait(
            [completion_task, abort_task],
            timeout=timeout_sec,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            timed_out = True
        elif abort_task in done and ctx.aborted:
            aborted = True
        else:
            completion_task.result()
    except asyncio.TimeoutError:
        timed_out = True
    finally:
        if timed_out or aborted:
            await kill_process_tree(proc)
        for task in (completion_task, abort_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(completion_task, abort_task, return_exceptions=True)

    # Check for abort
    if ctx.aborted and not timed_out:
        aborted = True

    # Wait for process to finish
    try:
        await asyncio.wait_for(proc.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        pass

    exit_code = proc.returncode

    # Build result metadata
    result_metadata = []
    if timed_out:
        result_metadata.append(f"bash tool terminated command after exceeding timeout {timeout_ms} ms")
    if aborted:
        result_metadata.append("User aborted the command")

    if result_metadata:
        output += "\n\n<bash_metadata>\n" + "\n".join(result_metadata) + "\n</bash_metadata>"

    # Truncate output for metadata
    truncated_output = output
    if len(truncated_output) > MAX_METADATA_LENGTH:
        truncated_output = truncated_output[:MAX_METADATA_LENGTH] + "\n\n..."

    # Determine success based on exit code
    success = exit_code == 0 if exit_code is not None else not timed_out and not aborted
    error_message = None
    if not success:
        error_message = _build_error_message(
            output=truncated_output,
            exit_code=exit_code,
            timeout_ms=timeout_ms,
            timed_out=timed_out,
            aborted=aborted,
        )

    return ToolResult(
        success=success,
        output=output,
        error=error_message,
        title=description or command,
        metadata={
            "output": truncated_output,
            "exit": exit_code,
            "description": description or command,
            "timed_out": timed_out,
            "aborted": aborted,
            **(extra_metadata or {}),
        },
    )

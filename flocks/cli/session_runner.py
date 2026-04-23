"""
CLI session runner.

Handles CLI-specific UI for session execution:
- User input/output
- Tool execution display
- Streaming text display

Core logic is in session/runner.py
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Optional, Dict, Any

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text
from rich.prompt import Prompt, Confirm

from flocks.utils.log import Log
from flocks.session.session import Session, SessionInfo
from flocks.session.runner import SessionRunner, RunnerCallbacks, ToolResult
from flocks.session.message import Message, MessageRole
from flocks.agent.registry import Agent
from flocks.provider.provider import Provider
from flocks.tool.registry import ToolRegistry
from flocks.project.project import Project
from dotenv import load_dotenv


log = Log.create(service="cli.runner")


# Module-level storage for CLI callbacks (used by SessionRunner during loop execution)
_CLI_CALLBACKS: Optional['RunnerCallbacks'] = None


def _set_cli_callbacks(callbacks: Optional['RunnerCallbacks']) -> None:
    """Set CLI callbacks for current execution"""
    global _CLI_CALLBACKS
    _CLI_CALLBACKS = callbacks


def _get_cli_callbacks() -> Optional['RunnerCallbacks']:
    """Get CLI callbacks for current execution"""
    return _CLI_CALLBACKS


# Tool display styles
TOOL_STYLES: Dict[str, tuple] = {
    "todowrite": ("Todo", "yellow bold"),
    "todoread": ("Todo", "yellow bold"),
    "bash": ("Bash", "red bold"),
    "edit": ("Edit", "green bold"),
    "write": ("Write", "green bold"),
    "glob": ("Glob", "cyan bold"),
    "grep": ("Grep", "cyan bold"),
    "list": ("List", "cyan bold"),
    "read": ("Read", "magenta bold"),
    "websearch": ("Search", "dim bold"),
    "delegate_task": ("Delegate", "bright_magenta bold"),
    "call_omo_agent": ("Delegate", "bright_magenta bold"),
}

DELEGATE_TOOLS = {"delegate_task", "call_omo_agent"}


class CLISessionRunner:
    """
    CLI wrapper for SessionRunner.
    
    Handles all CLI-specific display logic.
    """
    
    def __init__(
        self,
        console: Console,
        directory: Path,
        model: Optional[str] = None,
        agent: Optional[str] = None,
        auto_confirm: bool = False,
    ):
        self.console = console
        self.directory = directory
        self.model = model
        self.agent_name = agent
        self.auto_confirm = auto_confirm
        self._session: Optional[SessionInfo] = None
        self._runner: Optional[SessionRunner] = None
        self._live: Optional[Live] = None
        self._content_buffer: list[str] = []
        
        # Streaming display state (Flocks style: accumulate and flush)
        self._has_content = False
        self._reasoning_buffer: list[str] = []
        self._has_reasoning = False
    
    def _create_cli_question_handler(self):
        """Create a CLI question handler that interactively displays questions in terminal."""
        async def cli_question_handler(
            session_id: str,
            questions: list[dict],
        ) -> list[list[str]]:
            answers = []

            # Stop live display completely so auto-refresh doesn't interfere with terminal input
            if self._live:
                self._live.stop()
                self._live = None

            for q in questions:
                question_text = q.get("question", "")
                header = q.get("header", "")
                q_type = q.get("type", "choice")
                options = q.get("options", [])
                multiple = q.get("multiple", False)
                placeholder = q.get("placeholder", "")

                self.console.print()
                if header:
                    self.console.print(f"[bold]{header}[/bold]")
                self.console.print(f"[cyan bold]? {question_text}[/cyan bold]")

                if q_type == "choice" and options:
                    for i, opt in enumerate(options, 1):
                        label = opt.get("label", "") if isinstance(opt, dict) else opt
                        desc = opt.get("description", "") if isinstance(opt, dict) else ""
                        if desc:
                            self.console.print(f"  [bold]{i}.[/bold] {label}  [dim]{desc}[/dim]")
                        else:
                            self.console.print(f"  [bold]{i}.[/bold] {label}")

                    if multiple:
                        raw = Prompt.ask("[dim]输入编号（多选用逗号分隔，如 1,3）[/dim]")
                        selected = []
                        for part in raw.split(","):
                            part = part.strip()
                            if part.isdigit():
                                idx = int(part) - 1
                                if 0 <= idx < len(options):
                                    opt = options[idx]
                                    selected.append(opt.get("label", "") if isinstance(opt, dict) else opt)
                        if not selected and options:
                            opt = options[0]
                            selected = [opt.get("label", "") if isinstance(opt, dict) else opt]
                        answers.append(selected)
                    else:
                        raw = Prompt.ask(f"[dim]输入编号（1-{len(options)}）[/dim]")
                        idx = 0
                        if raw.strip().isdigit():
                            idx = max(0, min(int(raw.strip()) - 1, len(options) - 1))
                        opt = options[idx]
                        answers.append([opt.get("label", "") if isinstance(opt, dict) else opt])

                elif q_type == "confirm":
                    result = Confirm.ask(question_text)
                    answers.append(["Yes" if result else "No"])

                elif q_type == "number":
                    min_val = q.get("min_value")
                    max_val = q.get("max_value")
                    hint = ""
                    if min_val is not None and max_val is not None:
                        hint = f" ({min_val}~{max_val})"
                    raw = Prompt.ask(f"[dim]输入数字{hint}[/dim]")
                    answers.append([raw.strip()])

                elif q_type == "password":
                    import getpass
                    raw = getpass.getpass("输入内容（不回显）: ")
                    answers.append([raw])

                else:
                    # text / file / fallback
                    prompt_text = placeholder or "输入内容"
                    raw = Prompt.ask(f"[dim]{prompt_text}[/dim]")
                    answers.append([raw])

            return answers

        return cli_question_handler

    async def start(
        self,
        message: Optional[str] = None,
        session_id: Optional[str] = None,
        continue_session: bool = False,
    ) -> None:
        """Start interactive session."""
        # Mark CLI run mode for permission overrides
        os.environ["FLOCKS_CLI_RUN_MODE"] = "true"
        os.environ["FLOCKS_CLI_RUN_MODE"] = "true"

        # Register CLI question handler so question tool shows interactive prompts
        from flocks.tool.system.question import set_question_handler
        set_question_handler(self._create_cli_question_handler())

        # Initialize
        ToolRegistry.init()
        try:
            await Provider.init()
        except Exception as e:
            raise

        # Load custom providers from flocks.json (same as server mode)
        try:
            from flocks.server.routes.custom_provider import load_custom_providers_on_startup
            await load_custom_providers_on_startup()
        except Exception as e:
            log.debug("cli.custom_providers.load.failed", {"error": str(e)})

        # Initialize MCP subsystem — registers MCP tools into ToolRegistry so that
        # the CLI sees the same tool set as the web server (same underlying API).
        try:
            from flocks.mcp import MCP
            await MCP.init()
            log.info("cli.mcp.initialized")
        except Exception as e:
            log.warn("cli.mcp.init.failed", {"error": str(e)})
        
        # Get project
        try:
            result = await Project.from_directory(str(self.directory))
            project = result["project"]
        except Exception as e:
            raise
        
        # Get or create session
        try:
            self._session = await self._get_or_create_session(
                project_id=project.id,
                session_id=session_id,
                continue_session=continue_session,
            )
            Session.set_current(self._session)
        except Exception as e:
            raise
        
        log.info("session.started", {"session_id": self._session.id})
        
        if message:
            await self._process_message(message)
        else:
            await self._interactive_loop()
    
    async def _get_or_create_session(
        self,
        project_id: str,
        session_id: Optional[str] = None,
        continue_session: bool = False,
    ) -> SessionInfo:
        """Get or create session."""
        if session_id:
            session = await Session.get(project_id, session_id)
            if session:
                return session
            self.console.print(f"[yellow]Session {session_id} not found, creating new[/yellow]")
        
        if continue_session:
            sessions = await Session.list(project_id)
            if sessions:
                for s in sessions:
                    if not s.parent_id:
                        return s
        
        return await Session.create(
            project_id=project_id,
            directory=str(self.directory),
        )
    
    async def _interactive_loop(self) -> None:
        """Run interactive input loop."""
        self.console.print("[dim]Type your message and press Enter. Use Ctrl+C to exit.[/dim]\n")
        
        while True:
            try:
                user_input = Prompt.ask("[cyan]>[/cyan]")
                
                if not user_input.strip():
                    continue
                
                if user_input.strip().lower() in ("/exit", "/quit", "/q"):
                    break
                
                await self._process_message(user_input)
                
            except KeyboardInterrupt:
                self.console.print("\n[dim]Interrupted[/dim]")
                if self._runner:
                    self._runner.abort()
                break
            except EOFError:
                break
    
    async def _process_message(
        self,
        content: str,
        *,
        display_text: Optional[str] = None,
        dispatch_commands: bool = True,
    ) -> None:
        """Process a user message using unified SessionLoop."""
        stripped = content.strip()
        # Parse model: CLI flag > default_models.llm > config.model > env vars > defaults
        model_str = self.model
        provider_id = None
        model_id = None
        
        if model_str:
            # CLI flag explicitly set
            model_info = self._parse_model(model_str)
            provider_id = model_info.get("provider_id") if model_info else None
            model_id = model_info.get("model_id") if model_info else None
        
        if not provider_id or not model_id:
            # Try unified default LLM resolution (default_models.llm -> config.model)
            try:
                from flocks.config.config import Config
                default_llm = await Config.resolve_default_llm()
                if default_llm:
                    provider_id = default_llm["provider_id"]
                    model_id = default_llm["model_id"]
            except Exception as e:
                log.debug("cli.config.resolve_default_llm.failed", {"error": str(e)})
        
        if not provider_id or not model_id:
            provider_id = provider_id or os.getenv("LLM_PROVIDER", "anthropic")
            model_id = model_id or os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")

        # Resolve agent
        agent_name = self.agent_name or await Agent.default_agent()
        agent = await Agent.get(agent_name) or await Agent.get("rex")

        if dispatch_commands and stripped.startswith("/"):
            from flocks.input.dispatcher import dispatch_user_input
            from flocks.input.events import UserInputEvent
            from flocks.input.output import CliOutputSink

            event = UserInputEvent(
                source_type="cli",
                sessionID=self._session.id,
                text=stripped,
                parts=[{"type": "text", "text": stripped}],
                agent=agent.name,
                model={"providerID": provider_id, "modelID": model_id},
                display_text=stripped,
            )
            handled = await dispatch_user_input(
                event,
                CliOutputSink(
                    "cli",
                    direct_response=lambda _event, text: self._emit_assistant_text(
                        text,
                        agent.name,
                        provider_id,
                        model_id,
                    ),
                    run_llm=lambda _event, prompt, dispatch_display_text: self._process_message(
                        prompt,
                        display_text=dispatch_display_text,
                        dispatch_commands=False,
                    ),
                    clear_screen=self._clear_screen,
                    restart_session=lambda: self._restart_session(
                        agent.name,
                        provider_id,
                        model_id,
                    ),
                ),
            )
            if handled.handled:
                return
        
        # Check provider configuration
        provider = Provider.get(provider_id)
        provider_configured = provider.is_configured() if provider else False
        
        if not provider_configured:
            log.warning("cli.provider.not_configured", {
                "provider_id": provider_id,
                "model_id": model_id
            })
            self.console.print(f"\n[yellow]Warning: Provider '{provider_id}' is not configured[/yellow]")
            self.console.print(f"[yellow]Please check your API keys in .env file[/yellow]\n")

        # Create user message
        await Message.create(
            session_id=self._session.id,
            role=MessageRole.USER,
            content=display_text or content,
            agent=agent.name,
            model={"providerID": provider_id, "modelID": model_id},
        )
        
        # Import SessionLoop and LoopCallbacks
        from flocks.session.session_loop import SessionLoop, LoopCallbacks
        from flocks.session.runner import RunnerCallbacks
        
        # Create loop callbacks (wrapping runner callbacks)
        loop_callbacks = LoopCallbacks(
            on_step_start=self._on_step_start,
            on_step_end=self._on_step_end,
            on_error=self._on_error,
            on_compaction=self._on_compaction,
        )
        
        # Store runner callbacks for tool events
        # We need to hook into SessionRunner to get tool callbacks
        # This is done by temporarily storing callbacks in a module-level variable
        _set_cli_callbacks(RunnerCallbacks(
            on_text_delta=self._on_text_delta,
            on_reasoning_delta=self._on_reasoning_delta,
            on_tool_start=self._on_tool_start,
            on_tool_end=self._on_tool_end,
            on_permission_request=self._on_permission_request,
            on_error=self._on_error,
        ))
        
        # Start streaming display
        self._content_buffer = []
        self._reasoning_buffer = []
        self._has_content = False
        self._has_reasoning = False
        self._accumulated_text = ""
        self._last_display_time = 0
        self.console.print()
        
        # Use auto_refresh to control update rate (4fps for smooth animation, minimal IO)
        # Following Flocks's philosophy: minimal terminal updates during streaming
        loop_succeeded = False
        with Live(
            Spinner("dots", text="Thinking..."), 
            console=self.console, 
            refresh_per_second=4,  # 4fps足够流畅，降低终端IO
            auto_refresh=True
        ) as live:
            self._live = live
            
            # Run session loop (unified with TUI)
            try:
                result = await SessionLoop.run(
                    session_id=self._session.id,
                    provider_id=provider_id,
                    model_id=model_id,
                    agent_name=agent.name,
                    callbacks=loop_callbacks,
                )
                
                # Check result for errors
                if result and result.action == "error":
                    error_msg = result.error or "Unknown error"
                    log.error("cli.session_loop.error", {
                        "action": result.action,
                        "error": error_msg
                    })
                    # Show error to user
                    self.console.print(f"\n[red]Error: {error_msg}[/red]\n")
                elif result:
                    log.debug("cli.session_loop.completed", {
                        "action": result.action
                    })
                    loop_succeeded = True
            except Exception as e:
                log.error("cli.session_loop.exception", {"error": str(e)})
                self.console.print(f"\n[red]Error: {e}[/red]\n")
                import traceback
                traceback.print_exc()
            
            # Clear live display before exiting context
            live.update(Text(""))
            self._live = None
        
        # Clear callbacks
        _set_cli_callbacks(None)
        
        # Print any remaining content not yet printed
        if self._content_buffer:
            self._flush_content()

        # Ensure session title is generated after a successful first message.
        # session_loop fires title generation via fire_and_forget at step 1 (early
        # generation: runs concurrently with the LLM response for low latency).
        # This explicit call is the guaranteed safety net: in single-run mode the
        # fire_and_forget task can be cancelled by asyncio cleanup before it
        # completes. generate_title_after_first_message is idempotent — it returns
        # immediately when the background task already saved the title.
        if loop_succeeded:
            try:
                from flocks.session.lifecycle.title import SessionTitle
                await SessionTitle.generate_title_after_first_message(
                    session_id=self._session.id,
                    model_id=model_id,
                    provider_id=provider_id,
                )
            except Exception as e:
                log.warning("cli.title_generation.error", {"error": str(e)})

    async def _emit_assistant_text(
        self,
        text: str,
        agent_name: str,
        provider_id: str,
        model_id: str,
    ) -> None:
        await Message.create(
            session_id=self._session.id,
            role=MessageRole.ASSISTANT,
            content=text,
            agent=agent_name,
            providerID=provider_id,
            modelID=model_id,
            mode=agent_name,
        )
        self.console.print(Markdown(text))

    async def _clear_screen(self) -> None:
        self.console.clear()

    async def _restart_session(
        self,
        agent_name: str,
        provider_id: str,
        model_id: str,
    ) -> None:
        from flocks.session.message import Message
        from flocks.session.features.todo import Todo
        from flocks.session.core.status import SessionStatus

        await Message.clear(self._session.id)
        await Todo.clear(self._session.id)
        SessionStatus.clear(self._session.id)
        self._content_buffer = []
        self._has_content = False
        self.console.clear()
        await self._emit_assistant_text("会话已重置。", agent_name, provider_id, model_id)

    
    def _flush_content(self) -> None:
        """
        Flush and finalize accumulated content (Flocks style).
        
        策略：
        1. 合并所有累积的delta
        2. 清除Live/Spinner显示
        3. 一次性渲染Markdown输出
        4. 避免重复渲染带来的性能问题
        """
        # Flush reasoning first (if any)
        if self._reasoning_buffer:
            reasoning_content = "".join(self._reasoning_buffer)
            self._reasoning_buffer = []
            
            # Clear the Live/Spinner display before printing reasoning
            if self._live:
                self._live.update(Text(""))
                self._live.refresh()
            
            # 输出reasoning内容（使用紫色/dim样式表示thinking）
            if reasoning_content.strip():
                self.console.print()
                self.console.print("[dim magenta]🧠 Thinking:[/dim magenta]")
                self.console.print(Text(reasoning_content, style="dim italic"))
                self.console.print()
        
        # Then flush normal content
        if self._content_buffer:
            content = "".join(self._content_buffer)
            self._content_buffer = []
            
            # Clear the Live/Spinner display before printing final content
            if self._live:
                self._live.update(Text(""))
                self._live.refresh()
            
            # 只输出非空内容
            if content.strip():
                self.console.print()
                self.console.print(Markdown(content))
    
    async def _on_step_start(self, step: int) -> None:
        """Handle step start."""
        # Flush previous step's content before starting new step
        if self._content_buffer:
            self._flush_content()
        
        # Update spinner to show current step
        if self._live:
            self._live.update(Spinner("dots", text=f"Step {step}..."))
    
    async def _on_step_end(self, step: int) -> None:
        """Handle step end."""
        # Flush any remaining content at the end of each step
        if self._reasoning_buffer or self._content_buffer:
            self._flush_content()
        
        # Add a visual separator between steps if there was content
        # This helps distinguish different reasoning attempts
        if self._has_content or self._has_reasoning:
            self.console.print()  # Blank line to separate steps
            self._has_content = False  # Reset for next step
            self._has_reasoning = False  # Reset for next step
    
    async def _on_compaction(self) -> None:
        """Handle compaction start — display a styled panel."""
        self._flush_content()
        self.console.print(
            Panel(
                "[bold yellow]正在压缩上下文…[/bold yellow]\n"
                "[dim]裁剪旧工具输出 → 生成摘要 → 保存记忆[/dim]",
                border_style="yellow",
                expand=False,
            )
        )
    
    async def _on_text_delta(self, delta: str) -> None:
        """
        Handle text delta from LLM - Flocks style (最优性能).
        
        核心策略（完全模仿 Flocks）：
        1. Delta时静默累积，不更新UI
        2. 让Spinner自然刷新（4fps）
        3. 只在内容结束时flush输出
        
        为什么这样做？
        - 避免频繁的终端IO（每次update触发完整重绘）
        - 避免频繁的Markdown渲染（越来越慢）
        - 让用户看到流畅的spinner动画
        - 内容结束时一次性渲染，体验最佳
        
        性能提升：
        - 修复前：30次UI更新/秒 → 明显卡顿
        - 修复后：0次UI更新/秒 → 完全流畅
        """
        self._content_buffer.append(delta)
        self._has_content = True
        # ✅ 不调用 self._live.update()
        # ✅ Spinner会按照4fps自然刷新
        # ✅ 完全消除"打字卡顿"问题
    
    async def _on_reasoning_delta(self, delta: str) -> None:
        """
        Handle reasoning delta from LLM (thinking process).
        
        Reasoning内容会被累积，并在flush时以不同格式显示，
        让用户能看到AI的思考过程。
        """
        self._reasoning_buffer.append(delta)
        self._has_reasoning = True
        # 同样不更新UI，让Spinner自然刷新
    
    async def _on_tool_start(self, tool_name: str, arguments: Dict[str, Any]) -> None:
        """Handle tool execution start."""
        # Flush any accumulated content before showing tool
        # This ensures streaming text is finalized before tool output
        if self._content_buffer:
            self._flush_content()
        
        if tool_name in DELEGATE_TOOLS:
            agent = arguments.get("subagent_type") or arguments.get("category") or "unknown"
            desc = arguments.get("description", "子任务")
            bg = " [dim](后台)[/dim]" if arguments.get("run_in_background") else ""
            self.console.print(
                f"[bright_magenta bold]⤷[/bright_magenta bold] "
                f"委派 [bright_magenta bold]{agent}[/bright_magenta bold]{bg}  "
                f"[dim]{desc}[/dim]"
            )
            return

        display_name, style = TOOL_STYLES.get(tool_name, (tool_name, "blue bold"))
        # Calculate available width for arguments (subtract prefix: "| Write   " = ~12 chars)
        prefix_len = 12
        available_width = max(40, self.console.width - prefix_len)
        args_str = json.dumps(arguments, ensure_ascii=False)
        if len(args_str) > available_width:
            args_str = args_str[:available_width - 3] + "..."
        self.console.print(f"[{style}]|[/{style}] [dim]{display_name.ljust(7)}[/dim] {args_str}")
    
    async def _on_tool_end(self, tool_name: str, result: ToolResult) -> None:
        """Handle tool execution end."""
        if tool_name in DELEGATE_TOOLS:
            self._render_delegate_result(result)
            return

        if result.success:
            if tool_name == "bash" and result.output:
                self.console.print()
                # Truncate long bash output to avoid terminal slowdown
                output_text = str(result.output)
                if len(output_text) > 5000:
                    output_text = output_text[:5000] + "\n...(output truncated, too long)"
                self.console.print(Text(output_text, style="dim"))
            elif tool_name in ("write", "edit"):
                self.console.print(f"  [green]✓[/green] {result.title or 'Done'}")
            else:
                # Show tool outputs for non-bash tools to aid debugging.
                if result.output is not None:
                    self.console.print()
                    output_text = str(result.output)
                    # Aggressive truncation to prevent terminal slowdown
                    if len(output_text) > 2000:
                        output_text = output_text[:2000] + "...(output truncated)"
                    self.console.print(Text(output_text, style="dim"))
        else:
            # Show error message or exit code for failed commands
            if result.error:
                self.console.print(f"  [red]✗[/red] {result.error}")
            elif tool_name == "bash" and result.metadata:
                exit_code = result.metadata.get("exit")
                if exit_code is not None:
                    self.console.print(f"  [red]✗[/red] Exit code: {exit_code}")
                    if result.output:
                        # Show last few lines of output
                        lines = result.output.strip().split("\n")[-5:]
                        for line in lines:
                            self.console.print(f"    [dim]{line}[/dim]")
            else:
                self.console.print(f"  [red]✗[/red] Failed")
    
    def _render_delegate_result(self, result: ToolResult) -> None:
        """Render delegate_task / call_omo_agent result with a compact panel."""
        import re
        if result.success:
            output_text = str(result.output or "")
            # Strip <task_metadata> tags
            cleaned = re.sub(r"<task_metadata>[\s\S]*?</task_metadata>", "", output_text).strip()
            session_id = (result.metadata or {}).get("sessionId", "")
            # Truncate long output
            if len(cleaned) > 600:
                cleaned = cleaned[:600] + "…"
            status_line = f"[green]✓ 完成[/green]"
            if session_id:
                status_line += f"  [dim]session={session_id[:12]}…[/dim]"
            self.console.print(f"  {status_line}")
            if cleaned:
                self.console.print(Panel(
                    cleaned,
                    title="[bright_magenta]子Agent 结果[/bright_magenta]",
                    border_style="bright_magenta",
                    expand=False,
                    width=min(100, self.console.width - 4),
                ))
        else:
            error_msg = result.error or "Unknown error"
            self.console.print(f"  [red]✗ 委派失败:[/red] {error_msg}")

    async def _on_permission_request(self, request) -> bool:
        """Handle permission request."""
        if self.auto_confirm:
            return True
        
        # Clear live display so prompt is visible
        if self._live:
            self._live.update(Text(""))
            self._live.refresh()
        
        # Show styled permission prompt
        self.console.print()
        self.console.print(f"[yellow bold]⚠ Permission Required[/yellow bold]")
        message = f"Allow [cyan]{request.permission}[/cyan] for {', '.join(request.patterns[:3])}?"
        if len(request.patterns) > 3:
            message += f" (+{len(request.patterns) - 3} more)"
        return Confirm.ask(message, default=True)
    
    async def _on_error(self, error: str) -> None:
        """Handle error."""
        # Clear Live display so error is visible
        if self._live:
            self._live.update(Text(""))
            self._live.refresh()
        self.console.print(f"\n[red]Error: {error}[/red]")
    
    def _parse_model(self, model_str: Optional[str]) -> Optional[Dict[str, str]]:
        """Parse model string."""
        if not model_str:
            return None
        
        if "/" in model_str:
            provider_id, model_id = model_str.split("/", 1)
            return {"provider_id": provider_id, "model_id": model_id}
        
        return {"provider_id": "anthropic", "model_id": model_str}
    
    def _print_help(self) -> None:
        """Print help."""
        help_text = """
[bold]Commands:[/bold]
  /help            Show help
  /tools           List tools (same as /tools list)
  /tools list      List tools
  /tools refresh   Refresh dynamic tools
  /tools info      Show tool details
  /tools create    Create tool via tool-builder skill
  /skills          List skills (same as /skills list)
  /skills list     List skills
  /skills refresh  Refresh skills
  /clear           Clear screen
  /restart         Restart session
  /exit            Exit session
  /quit            Exit session
  /q               Exit session

[bold]Keyboard:[/bold]
  Ctrl+C   Cancel or exit
  Enter    Send message
"""
        self.console.print(Panel(help_text, title="Help", border_style="blue"))


__all__ = [
    "CLISessionRunner",
    "run_session",
    "_get_cli_callbacks",
    "_set_cli_callbacks",
]


async def run_session(
    directory: Path,
    message: Optional[str] = None,
    model: Optional[str] = None,
    agent: Optional[str] = None,
    session_id: Optional[str] = None,
    continue_session: bool = False,
    console: Optional[Console] = None,
    auto_confirm: bool = False,
) -> None:
    """
    Run an interactive CLI session.
    
    Args:
        directory: Working directory
        message: Initial message (single-run mode)
        model: Model ID (provider/model format)
        agent: Agent name
        session_id: Session ID to continue
        continue_session: Continue last session
        console: Rich console
        auto_confirm: Auto-confirm all permission requests
    """
    console = console or Console()
    # Ensure project-specific .env is loaded even when cwd differs.
    dotenv_path = directory / ".env"
    load_dotenv(dotenv_path=dotenv_path)
    if dotenv_path.exists() and not os.environ.get("THREATBOOK_API_KEY"):
        console.print("Warning: .env found but THREATBOOK_API_KEY is not loaded.")
    
    runner = CLISessionRunner(
        console=console,
        directory=directory,
        model=model,
        agent=agent,
        auto_confirm=auto_confirm,
    )
    
    await runner.start(
        message=message,
        session_id=session_id,
        continue_session=continue_session,
    )

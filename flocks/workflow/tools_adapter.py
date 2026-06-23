"""Adapter: expose flocks ToolRegistry as workflow's tool registry (sync interface)."""

from __future__ import annotations

import asyncio
import json as _json
from concurrent.futures import TimeoutError as _FuturesTimeoutError
from typing import Any, Callable, Dict, List, Optional

from flocks.tool import ToolContext, ToolRegistry, ToolResult
from flocks.utils.log import Log
from flocks.workflow.errors import NodeExecutionError, RunCancelledError
from flocks.workflow._async_runtime import (
    run_sync as _run_sync_on_shared_loop,
    run_sync_cancellable as _run_sync_cancellable_on_shared_loop,
)
from flocks.workflow.tools_spec import ToolSpec

# Tools that must not be exposed inside workflow (avoid circular invocation).
WORKFLOW_TOOL_BLOCKLIST = frozenset({"run_workflow"})
log = Log.create(service="workflow.tools_adapter")
_MCP_INIT_ATTEMPTED = False


class FlocksToolAdapter:
    """Adapts flocks async ToolRegistry to workflow sync tool.run(name, **kwargs).

    - run(name, **kwargs): sync, executes via the shared workflow async loop
    - list(): all tool ids (auto-discovered from flocks), excluding blocklist
    - get(name): stub impl for code_gen signature extraction
    - get_spec(name): ToolSpec for code_gen
    - run_workflow is hidden to avoid workflow-in-workflow circular calls.
    """

    def __init__(self, tool_context: Optional[ToolContext] = None):
        ToolRegistry.init()
        self._ctx = tool_context
        self.cancel_checker: Optional[Callable[[], bool]] = None

    def _blocked(self, name: str) -> bool:
        return (name or "").strip() in WORKFLOW_TOOL_BLOCKLIST

    def _execute_tool_async(self, name: str, ctx: ToolContext, kwargs: Dict[str, Any]) -> ToolResult:
        """
        Execute an async tool from a sync context safely.

        All workflow-triggered tool calls are routed through the shared
        workflow async runtime instead of creating an ephemeral event loop
        per parallel node. This keeps loop-bound provider / HTTP resources
        attached to a stable loop and avoids cross-loop Future errors under
        sibling-node concurrency.
        """
        coro = ToolRegistry.execute(name, ctx=ctx, **kwargs)
        if self.cancel_checker is not None:
            return _run_sync_cancellable_on_shared_loop(coro, self.cancel_checker)
        return _run_sync_on_shared_loop(coro)

    def run(self, name: str, /, **kwargs: Any) -> Any:
        name = (name or "").strip()
        if self.cancel_checker is not None and self.cancel_checker():
            raise RunCancelledError("<tool>")
        if self._blocked(name):
            raise NodeExecutionError(
                node_id="<tool>",
                message=f"Tool {name!r} is not available inside workflow (blocked to avoid circular invocation)",
            )
        tool = ToolRegistry.get(name)
        if tool is None:
            _try_lazy_load_mcp_tools()
            tool = ToolRegistry.get(name)
        if tool is None:
            raise NodeExecutionError(node_id="<tool>", message=f"Tool not found: {name!r}")

        ctx = self._ctx or ToolContext(session_id="workflow", message_id="workflow")
        try:
            if self.cancel_checker is not None and self.cancel_checker():
                raise RunCancelledError("<tool>")
            result: ToolResult = self._execute_tool_async(name, ctx, dict(kwargs))
            if self.cancel_checker is not None and self.cancel_checker():
                raise RunCancelledError("<tool>")
        except _FuturesTimeoutError:
            raise
        except RunCancelledError:
            raise
        except asyncio.CancelledError as e:
            raise RunCancelledError("<tool>") from e
        except Exception as e:
            raise NodeExecutionError(node_id="<tool>", message=f"Tool {name!r} failed: {e}") from e

        if not result.success:
            raise NodeExecutionError(
                node_id="<tool>",
                message=result.error or f"Tool {name!r} failed",
            )
        return result.output

    def run_safe(self, name: str, /, **kwargs: Any) -> Dict[str, Any]:
        """Run tool and return a unified envelope dict.

        Returns:
            {
                "success": bool,
                "text": str,   # always a string (safe for prompt / string ops)
                "obj": Any,    # raw output (str | dict | list | None)
                "error": str | None,
            }

        Unlike ``run()`` which raises on failure and returns raw *output*
        (whose type varies per tool), ``run_safe()`` never raises and always
        returns a dict with a guaranteed ``text`` field.
        """
        try:
            raw = self.run(name, **kwargs)
            if isinstance(raw, str):
                text = raw
            elif raw is None:
                text = ""
            else:
                try:
                    text = _json.dumps(raw, ensure_ascii=False, default=str)
                except Exception:
                    text = str(raw)
            return {"success": True, "text": text, "obj": raw, "error": None}
        except _FuturesTimeoutError:
            raise
        except Exception as exc:
            return {"success": False, "text": "", "obj": None, "error": str(exc)}

    def list(self) -> List[str]:
        ToolRegistry.init()
        return [n for n in ToolRegistry.all_tool_ids() if n not in WORKFLOW_TOOL_BLOCKLIST]

    def get(self, name: str) -> Optional[Any]:
        """Return a stub with .run for code_gen signature extraction."""
        if self._blocked(name):
            return None
        tool = ToolRegistry.get(name)
        if tool is None:
            return None
        return _ToolStub(tool)

    def get_spec(self, name: str) -> Optional[ToolSpec]:
        if self._blocked(name):
            return None
        tool = ToolRegistry.get(name)
        if tool is None:
            return None
        info = tool.info
        props: Dict[str, Any] = {}
        required: List[str] = []
        for p in info.parameters:
            props[p.name] = {"type": p.type.value, "description": p.description or ""}
            if p.required:
                required.append(p.name)
        args_schema: Dict[str, Any] = {"type": "object", "properties": props}
        if required:
            args_schema["required"] = required
        sig_parts = [f"{p.name}: {p.type.value}" for p in info.parameters]
        signature = "(" + ", ".join(sig_parts) + ")"
        return ToolSpec(
            name=info.name,
            description=info.description or "",
            args_schema=args_schema,
            signature=signature,
        )


class _ToolStub:
    """Stub so code_gen can get run() signature from flocks tool."""

    def __init__(self, tool: Any):
        self._tool = tool

    def run(self, **kwargs: Any) -> Any:
        raise NotImplementedError("Use adapter.run()")

    @property
    def __doc__(self) -> str:
        return getattr(self._tool.info, "description", "") or ""


def _try_lazy_load_mcp_tools() -> None:
    global _MCP_INIT_ATTEMPTED
    if _MCP_INIT_ATTEMPTED:
        return
    _MCP_INIT_ATTEMPTED = True
    try:
        from flocks.mcp import MCP

        _run_sync_on_shared_loop(MCP.init())
    except Exception as exc:
        log.warning("workflow.tools_adapter.mcp_lazy_init_failed", {"error": str(exc)})

"""
Hook Pipeline

Provides a lightweight hook registry and execution pipeline that mirrors
oh-my-opencode's lifecycle stages:
- chat.message
- llm.call.before
- llm.call.after
- tool.execute.before
- tool.execute.after
- event
"""

import asyncio
import inspect
from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Callable, Awaitable

from flocks.extensions import FailPolicy, normalize_fail_policy, normalize_timeout
from flocks.utils.log import Log


log = Log.create(service="hooks.pipeline")


class HookStage:
    CHAT_MESSAGE = "chat.message"
    LLM_BEFORE = "llm.call.before"
    LLM_AFTER = "llm.call.after"
    TOOL_BEFORE = "tool.execute.before"
    TOOL_AFTER = "tool.execute.after"
    EVENT = "event"
    CHANNEL_INBOUND = "channel.inbound"
    CHANNEL_OUTBOUND_BEFORE = "channel.outbound.before"
    CHANNEL_OUTBOUND_AFTER = "channel.outbound.after"


_DEFAULT_STAGE_TIMEOUTS: Dict[str, float] = {
    HookStage.CHAT_MESSAGE: 5.0,
    HookStage.LLM_BEFORE: 5.0,
    HookStage.LLM_AFTER: 5.0,
    HookStage.TOOL_BEFORE: 5.0,
    HookStage.TOOL_AFTER: 5.0,
    HookStage.CHANNEL_INBOUND: 5.0,
    HookStage.CHANNEL_OUTBOUND_BEFORE: 5.0,
    HookStage.CHANNEL_OUTBOUND_AFTER: 5.0,
    HookStage.EVENT: 10.0,
}


@dataclass
class HookContext:
    stage: str
    input: Dict[str, Any]
    output: Dict[str, Any] = field(default_factory=dict)


class HookBase:
    async def chat_message(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def llm_before(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def llm_after(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def tool_before(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def tool_after(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def event(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def channel_inbound(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def channel_outbound_before(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def channel_outbound_after(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None


@dataclass(order=True)
class _HookEntry:
    order: int
    name: str
    hook: HookBase = field(compare=False)
    timeout_seconds: Optional[float] = field(default=None, compare=False)
    fail_policy: FailPolicy = field(default=FailPolicy.ISOLATE, compare=False)


class HookPipeline:
    """
    Global hook pipeline registry and runner.
    """

    _hooks: List[_HookEntry] = []
    _initialized: bool = False
    _loaded_project_dir: Optional[str] = None
    _plugin_hook_names: set[str] = set()

    @classmethod
    def register(
        cls,
        name: str,
        hook: HookBase,
        order: int = 0,
        *,
        plugin_managed: bool = False,
        timeout_seconds: Optional[float] = None,
        fail_policy: FailPolicy | str | None = None,
        critical: bool = False,
    ) -> None:
        cls.unregister(name)
        if plugin_managed:
            cls._plugin_hook_names.add(name)
        else:
            cls._plugin_hook_names.discard(name)
        cls._hooks.append(_HookEntry(
            order=order,
            name=name,
            hook=hook,
            timeout_seconds=normalize_timeout(timeout_seconds),
            fail_policy=normalize_fail_policy(fail_policy, critical=critical),
        ))
        cls._hooks.sort()
        log.info("hook.registered", {"name": name, "order": order})

    @classmethod
    def unregister(cls, name: str) -> None:
        before = len(cls._hooks)
        cls._hooks = [h for h in cls._hooks if h.name != name]
        cls._plugin_hook_names.discard(name)
        if len(cls._hooks) != before:
            log.info("hook.unregistered", {"name": name})

    @classmethod
    def list_hooks(cls) -> List[str]:
        return [h.name for h in cls._hooks]

    @classmethod
    def reset(cls) -> None:
        """Reset pipeline state (primarily for tests)."""
        cls._hooks = []
        cls._initialized = False
        cls._loaded_project_dir = None
        cls._plugin_hook_names = set()

    @staticmethod
    def _normalize_project_dir(project_dir: Optional[Path]) -> Optional[str]:
        if project_dir is None:
            return None
        return str(project_dir.expanduser().resolve(strict=False))

    @classmethod
    def _clear_plugin_hooks(cls) -> None:
        """Remove previously loaded plugin hooks before reloading another project."""
        for name in list(cls._plugin_hook_names):
            cls.unregister(name)
        cls._plugin_hook_names = set()

    @classmethod
    async def _resolve_project_dir(cls, input_data: Dict[str, Any]) -> Optional[Path]:
        """Resolve the current project directory from hook payload metadata."""
        for key in ("workspace", "workspaceDir", "workspace_dir", "projectDir", "project_dir", "directory", "cwd"):
            value = input_data.get(key)
            if isinstance(value, str) and value.strip():
                return Path(value.strip())

        session_id = input_data.get("sessionID") or input_data.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            return None

        try:
            from flocks.session.session import Session

            session = await Session.get_by_id(session_id)
            if session and isinstance(session.directory, str) and session.directory.strip():
                return Path(session.directory.strip())
        except Exception as exc:
            log.debug("hook.project_dir.resolve_failed", {"session_id": session_id, "error": str(exc)})
        return None

    @classmethod
    async def ensure_initialized(cls, project_dir: Optional[Path] = None) -> None:
        """Lazily load hooks and reload project hooks when workspace changes."""
        resolved_project_dir = cls._normalize_project_dir(project_dir)
        if cls._initialized and (
            resolved_project_dir is None or resolved_project_dir == cls._loaded_project_dir
        ):
            return

        cls._register_plugin_extension_point()
        load_project_dir = Path(resolved_project_dir) if resolved_project_dir else Path.cwd()
        if cls._initialized:
            cls._clear_plugin_hooks()

        try:
            from flocks.config.config import Config
            from flocks.plugin import PluginLoader

            cfg = await Config.get()
            PluginLoader.load_extension(
                "HOOKS",
                extra_sources=cfg.plugin or [],
                project_dir=load_project_dir,
            )
        except Exception as exc:
            log.warn("hook.plugin_load_failed", {"error": str(exc)})
        finally:
            cls._initialized = True
            cls._loaded_project_dir = str(load_project_dir.resolve(strict=False))

    @classmethod
    async def run_chat_message(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        return await cls._run_stage(HookStage.CHAT_MESSAGE, input_data, output_data)

    @classmethod
    async def run_llm_before(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        return await cls._run_stage(HookStage.LLM_BEFORE, input_data, output_data)

    @classmethod
    async def run_llm_after(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        return await cls._run_stage(HookStage.LLM_AFTER, input_data, output_data)

    @classmethod
    async def run_tool_before(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        return await cls._run_stage(HookStage.TOOL_BEFORE, input_data, output_data)

    @classmethod
    async def run_tool_after(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        return await cls._run_stage(HookStage.TOOL_AFTER, input_data, output_data)

    @classmethod
    async def run_event(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        return await cls._run_stage(HookStage.EVENT, input_data, output_data)

    @classmethod
    async def run_channel_inbound(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        return await cls._run_stage(HookStage.CHANNEL_INBOUND, input_data, output_data)

    @classmethod
    async def run_channel_outbound_before(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        return await cls._run_stage(HookStage.CHANNEL_OUTBOUND_BEFORE, input_data, output_data)

    @classmethod
    async def run_channel_outbound_after(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        return await cls._run_stage(HookStage.CHANNEL_OUTBOUND_AFTER, input_data, output_data)

    @classmethod
    async def has_stage_handlers(
        cls,
        stage: str,
        input_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Return True when at least one hook can handle the stage."""
        started_at = time.perf_counter()
        project_dir = await cls._resolve_project_dir(input_data or {})
        await cls.ensure_initialized(project_dir)
        has_handlers = any(
            cls._resolve_handler(entry.hook, stage) is not None
            for entry in cls._hooks
        )
        log.debug("hook.stage_probe", {
            "stage": stage,
            "has_handlers": has_handlers,
            "duration_ms": int((time.perf_counter() - started_at) * 1000),
        })
        return has_handlers

    @classmethod
    async def _run_stage(
        cls,
        stage: str,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        stage_started_at = time.perf_counter()
        project_dir = await cls._resolve_project_dir(input_data)
        await cls.ensure_initialized(project_dir)
        ctx = HookContext(stage=stage, input=input_data, output=output_data or {})
        handler_count = 0
        for entry in cls._hooks:
            handler = cls._resolve_handler(entry.hook, stage)
            if not handler:
                continue
            handler_count += 1
            try:
                timeout_seconds = entry.timeout_seconds
                if timeout_seconds is None:
                    timeout_seconds = _DEFAULT_STAGE_TIMEOUTS.get(stage, 5.0)
                handler_started_at = time.perf_counter()
                if timeout_seconds is not None:
                    await asyncio.wait_for(
                        cls._invoke_handler(handler, ctx),
                        timeout=timeout_seconds,
                    )
                else:
                    await cls._invoke_handler(handler, ctx)
            except asyncio.TimeoutError:
                duration_ms = int((time.perf_counter() - handler_started_at) * 1000)
                log.warning("hook.timeout", {
                    "stage": stage,
                    "hook": entry.name,
                    "duration_ms": duration_ms,
                    "timeout_ms": int((timeout_seconds or 0) * 1000),
                    "critical": entry.fail_policy != FailPolicy.ISOLATE,
                    "fail_policy": entry.fail_policy.value,
                })
                if entry.fail_policy != FailPolicy.ISOLATE:
                    raise
            except Exception as exc:
                log.error("hook.error", {
                    "stage": stage,
                    "hook": entry.name,
                    "error": str(exc),
                    "critical": entry.fail_policy != FailPolicy.ISOLATE,
                    "fail_policy": entry.fail_policy.value,
                })
                if entry.fail_policy != FailPolicy.ISOLATE:
                    raise
        log.debug("hook.stage_complete", {
            "stage": stage,
            "handler_count": handler_count,
            "duration_ms": int((time.perf_counter() - stage_started_at) * 1000),
        })
        return ctx

    @classmethod
    def _register_plugin_extension_point(cls) -> None:
        """Register the HOOKS extension point with the unified plugin loader."""
        from flocks.plugin import ExtensionPoint, PluginLoader

        def _hook_name(hook: HookBase) -> str:
            explicit_name = getattr(hook, "name", None)
            if isinstance(explicit_name, str) and explicit_name.strip():
                return explicit_name.strip()
            return f"{hook.__class__.__module__}.{hook.__class__.__name__}"

        def _hook_order(hook: HookBase) -> int:
            order = getattr(hook, "order", 0)
            return order if isinstance(order, int) else 0

        def _consume_hooks(items: list, source: str) -> None:
            for hook in items:
                cls.register(
                    _hook_name(hook),
                    hook,
                    order=_hook_order(hook),
                    plugin_managed=True,
                )
            log.info("hook.plugins.loaded", {"source": source, "count": len(items)})

        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="HOOKS",
            subdir="hooks",
            consumer=_consume_hooks,
            item_type=HookBase,
            dedup_key=_hook_name,
            recursive=True,
            max_depth=2,
        ))

    @staticmethod
    async def _invoke_handler(handler: Callable[[HookContext], Awaitable[None]], ctx: HookContext) -> None:
        result = handler(ctx)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _resolve_handler(hook: HookBase, stage: str) -> Optional[Callable[[HookContext], Awaitable[None]]]:
        if stage == HookStage.CHAT_MESSAGE:
            return getattr(hook, "chat_message", None)
        if stage == HookStage.LLM_BEFORE:
            return getattr(hook, "llm_before", None)
        if stage == HookStage.LLM_AFTER:
            return getattr(hook, "llm_after", None)
        if stage == HookStage.TOOL_BEFORE:
            return getattr(hook, "tool_before", None)
        if stage == HookStage.TOOL_AFTER:
            return getattr(hook, "tool_after", None)
        if stage == HookStage.EVENT:
            return getattr(hook, "event", None)
        if stage == HookStage.CHANNEL_INBOUND:
            return getattr(hook, "channel_inbound", None)
        if stage == HookStage.CHANNEL_OUTBOUND_BEFORE:
            return getattr(hook, "channel_outbound_before", None)
        if stage == HookStage.CHANNEL_OUTBOUND_AFTER:
            return getattr(hook, "channel_outbound_after", None)
        return None

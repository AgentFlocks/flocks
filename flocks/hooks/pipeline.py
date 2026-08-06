"""
Hook Pipeline

Provides a lightweight hook registry and execution pipeline that mirrors
agent lifecycle stages:
- user.prompt.before
- session.start
- llm.call.before
- llm.call.after
- tool.execute.before
- tool.execute.after
- turn.after
- subagent.before
- subagent.after
- channel.inbound.before
- channel.outbound.before
- channel.outbound.after
"""

import asyncio
import inspect
from dataclasses import dataclass, field
from pathlib import Path
import time
import warnings
from typing import Any, Dict, List, Optional, Callable, Awaitable

from flocks.extensions import FailPolicy, normalize_fail_policy, normalize_timeout
from flocks.utils.log import Log


log = Log.create(service="hooks.pipeline")


class HookStage:
    USER_PROMPT_BEFORE = "user.prompt.before"
    USER_PROMPT_SUBMIT = USER_PROMPT_BEFORE  # backward-compatible alias
    SESSION_START = "session.start"
    LLM_BEFORE = "llm.call.before"
    LLM_AFTER = "llm.call.after"
    TOOL_BEFORE = "tool.execute.before"
    TOOL_AFTER = "tool.execute.after"
    TURN_AFTER = "turn.after"
    TURN_FINISH = TURN_AFTER  # backward-compatible alias
    SUBAGENT_BEFORE = "subagent.before"
    SUBAGENT_AFTER = "subagent.after"
    SUBAGENT_START = SUBAGENT_BEFORE  # backward-compatible alias
    SUBAGENT_STOP = SUBAGENT_AFTER  # backward-compatible alias
    CHANNEL_INBOUND_BEFORE = "channel.inbound.before"
    CHANNEL_INBOUND = CHANNEL_INBOUND_BEFORE  # backward-compatible alias
    CHANNEL_OUTBOUND_BEFORE = "channel.outbound.before"
    CHANNEL_OUTBOUND_AFTER = "channel.outbound.after"
    # Deprecated compatibility stages; canonical flow no longer uses them.
    ACTION_BEFORE = "action.before"
    ACTION_AFTER = "action.after"
    FILESYSTEM_BEFORE = "filesystem.before"
    FILESYSTEM_AFTER = "filesystem.after"
    INGRESS_BEFORE = "ingress.before"
    INGRESS_AFTER = "ingress.after"
    CHANNEL_WEBHOOK_BEFORE = "channel.webhook.before"
    CHANNEL_WEBHOOK_AFTER = "channel.webhook.after"


_DEFAULT_STAGE_TIMEOUTS: Dict[str, float] = {
    HookStage.USER_PROMPT_BEFORE: 5.0,
    HookStage.SESSION_START: 5.0,
    HookStage.LLM_BEFORE: 5.0,
    HookStage.LLM_AFTER: 5.0,
    HookStage.TOOL_BEFORE: 5.0,
    HookStage.TOOL_AFTER: 5.0,
    HookStage.TURN_AFTER: 5.0,
    HookStage.SUBAGENT_BEFORE: 5.0,
    HookStage.SUBAGENT_AFTER: 5.0,
    HookStage.CHANNEL_INBOUND_BEFORE: 5.0,
    HookStage.CHANNEL_OUTBOUND_BEFORE: 5.0,
    HookStage.CHANNEL_OUTBOUND_AFTER: 5.0,
}


@dataclass
class HookContext:
    stage: str
    input: Dict[str, Any]
    output: Dict[str, Any] = field(default_factory=dict)
    # ``output`` is deliberately shared by every hook, so it remains suitable
    # for cooperative metadata.  The generic execution stop, however, is a
    # monotonic lifecycle control: once a hook has requested it, a later hook
    # must not be able to resume the effect by replacing ``output.execution``.
    execution_stop_requested: bool = False
    execution_stop_detail: str | None = None


class HookBase:
    async def user_prompt_before(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def session_start(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def llm_before(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def llm_after(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def tool_before(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def tool_after(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def turn_after(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def subagent_before(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def subagent_after(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def channel_inbound_before(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    # Backward compatibility methods. Plugin authors should migrate to the
    # canonical method names above.
    async def user_prompt_submit(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return await self.user_prompt_before(ctx)

    async def turn_finish(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return await self.turn_after(ctx)

    async def subagent_start(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return await self.subagent_before(ctx)

    async def subagent_stop(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return await self.subagent_after(ctx)

    async def channel_inbound(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return await self.channel_inbound_before(ctx)

    async def channel_outbound_before(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def channel_outbound_after(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    # Deprecated compatibility methods.
    async def action_before(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def action_after(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def filesystem_before(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def filesystem_after(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def ingress_before(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def ingress_after(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return None

    async def channel_webhook_before(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
        return await self.channel_inbound_before(ctx)

    async def channel_webhook_after(self, ctx: HookContext) -> None:  # pragma: no cover - default no-op
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
    async def run_user_prompt_before(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        return await cls._run_stage(HookStage.USER_PROMPT_BEFORE, input_data, output_data)

    @classmethod
    async def run_session_start(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        return await cls._run_stage(HookStage.SESSION_START, input_data, output_data)

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
    async def run_turn_after(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        return await cls._run_stage(HookStage.TURN_AFTER, input_data, output_data)

    @classmethod
    async def run_subagent_before(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        return await cls._run_stage(HookStage.SUBAGENT_BEFORE, input_data, output_data)

    @classmethod
    async def run_subagent_after(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        return await cls._run_stage(HookStage.SUBAGENT_AFTER, input_data, output_data)

    @classmethod
    async def run_channel_inbound_before(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        return await cls._run_stage(HookStage.CHANNEL_INBOUND_BEFORE, input_data, output_data)

    @classmethod
    async def run_user_prompt_submit(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        warnings.warn(
            "run_user_prompt_submit is deprecated, use run_user_prompt_before",
            DeprecationWarning,
            stacklevel=2,
        )
        return await cls.run_user_prompt_before(input_data, output_data)

    @classmethod
    async def run_turn_finish(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        warnings.warn(
            "run_turn_finish is deprecated, use run_turn_after",
            DeprecationWarning,
            stacklevel=2,
        )
        return await cls.run_turn_after(input_data, output_data)

    @classmethod
    async def run_subagent_start(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        warnings.warn(
            "run_subagent_start is deprecated, use run_subagent_before",
            DeprecationWarning,
            stacklevel=2,
        )
        return await cls.run_subagent_before(input_data, output_data)

    @classmethod
    async def run_subagent_stop(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        warnings.warn(
            "run_subagent_stop is deprecated, use run_subagent_after",
            DeprecationWarning,
            stacklevel=2,
        )
        return await cls.run_subagent_after(input_data, output_data)

    @classmethod
    async def run_channel_inbound(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        warnings.warn(
            "run_channel_inbound is deprecated, use run_channel_inbound_before",
            DeprecationWarning,
            stacklevel=2,
        )
        return await cls.run_channel_inbound_before(input_data, output_data)

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
    async def run_action_before(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        warnings.warn(
            "run_action_before is deprecated, use run_tool_before",
            DeprecationWarning,
            stacklevel=2,
        )
        return await cls._run_stage(HookStage.ACTION_BEFORE, input_data, output_data)

    @classmethod
    async def run_action_after(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        warnings.warn(
            "run_action_after is deprecated, use run_tool_after",
            DeprecationWarning,
            stacklevel=2,
        )
        return await cls._run_stage(HookStage.ACTION_AFTER, input_data, output_data)

    @classmethod
    async def run_filesystem_before(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        warnings.warn(
            "run_filesystem_before is deprecated, use run_tool_before",
            DeprecationWarning,
            stacklevel=2,
        )
        return await cls._run_stage(HookStage.FILESYSTEM_BEFORE, input_data, output_data)

    @classmethod
    async def run_filesystem_after(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        warnings.warn(
            "run_filesystem_after is deprecated, use run_tool_after",
            DeprecationWarning,
            stacklevel=2,
        )
        return await cls._run_stage(HookStage.FILESYSTEM_AFTER, input_data, output_data)

    @classmethod
    async def run_ingress_before(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        warnings.warn(
            "run_ingress_before is deprecated and transport-owned",
            DeprecationWarning,
            stacklevel=2,
        )
        return await cls._run_stage(HookStage.INGRESS_BEFORE, input_data, output_data)

    @classmethod
    async def run_ingress_after(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        warnings.warn(
            "run_ingress_after is deprecated and transport-owned",
            DeprecationWarning,
            stacklevel=2,
        )
        return await cls._run_stage(HookStage.INGRESS_AFTER, input_data, output_data)

    @classmethod
    async def run_channel_webhook_before(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        warnings.warn(
            "run_channel_webhook_before is deprecated, use run_channel_inbound_before",
            DeprecationWarning,
            stacklevel=2,
        )
        return await cls._run_stage(HookStage.CHANNEL_WEBHOOK_BEFORE, input_data, output_data)

    @classmethod
    async def run_channel_webhook_after(
        cls,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        warnings.warn(
            "run_channel_webhook_after is deprecated",
            DeprecationWarning,
            stacklevel=2,
        )
        return await cls._run_stage(HookStage.CHANNEL_WEBHOOK_AFTER, input_data, output_data)

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
        cls._latch_execution_stop(ctx)
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
                cls._latch_execution_stop(ctx)
            except asyncio.TimeoutError as exc:
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

    @staticmethod
    def _latch_execution_stop(ctx: HookContext) -> None:
        """Remember a generic stop request even if later hooks mutate output.

        This is intentionally limited to the pre-existing generic
        ``execution.stop`` contract.  Flocks assigns no policy meaning to the
        request; extensions remain responsible for deciding whether to emit
        it and for supplying an opaque detail string.
        """
        execution = ctx.output.get("execution")
        if not isinstance(execution, dict) or execution.get("stop") is not True:
            return
        ctx.execution_stop_requested = True
        if ctx.execution_stop_detail is None:
            detail = execution.get("detail")
            ctx.execution_stop_detail = (
                str(detail)
                if detail is not None
                else "operation stopped by extension"
            )

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
            result = await result
        if isinstance(result, dict):
            ctx.output.update(result)

    @staticmethod
    def _resolve_handler(hook: HookBase, stage: str) -> Optional[Callable[[HookContext], Awaitable[None]]]:
        candidate_methods = {
            HookStage.USER_PROMPT_BEFORE: ("user_prompt_before", "user_prompt_submit"),
            HookStage.SESSION_START: ("session_start",),
            HookStage.LLM_BEFORE: ("llm_before",),
            HookStage.LLM_AFTER: ("llm_after",),
            HookStage.TOOL_BEFORE: ("tool_before",),
            HookStage.TOOL_AFTER: ("tool_after",),
            HookStage.TURN_AFTER: ("turn_after", "turn_finish"),
            HookStage.SUBAGENT_BEFORE: ("subagent_before", "subagent_start"),
            HookStage.SUBAGENT_AFTER: ("subagent_after", "subagent_stop"),
            HookStage.CHANNEL_INBOUND_BEFORE: (
                "channel_inbound_before",
                "channel_inbound",
            ),
            HookStage.CHANNEL_OUTBOUND_BEFORE: ("channel_outbound_before",),
            HookStage.CHANNEL_OUTBOUND_AFTER: ("channel_outbound_after",),
            HookStage.ACTION_BEFORE: ("action_before",),
            HookStage.ACTION_AFTER: ("action_after",),
            HookStage.FILESYSTEM_BEFORE: ("filesystem_before",),
            HookStage.FILESYSTEM_AFTER: ("filesystem_after",),
            HookStage.INGRESS_BEFORE: ("ingress_before",),
            HookStage.INGRESS_AFTER: ("ingress_after",),
            HookStage.CHANNEL_WEBHOOK_BEFORE: ("channel_webhook_before", "channel_inbound_before", "channel_inbound"),
            HookStage.CHANNEL_WEBHOOK_AFTER: ("channel_webhook_after",),
        }.get(stage)
        if candidate_methods is None:
            return None

        for method_name in candidate_methods:
            handler = getattr(hook, method_name, None)
            if not callable(handler):
                continue
            base_handler = getattr(HookBase, method_name, None)
            concrete_handler = getattr(type(hook), method_name, None)
            if base_handler is not None and concrete_handler is base_handler:
                continue
            return handler
        return None

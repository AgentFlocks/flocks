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

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable, Awaitable

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
    hook: HookBase


class HookPipeline:
    """
    Global hook pipeline registry and runner.
    """

    _hooks: List[_HookEntry] = []

    @classmethod
    def register(cls, name: str, hook: HookBase, order: int = 0) -> None:
        cls.unregister(name)
        cls._hooks.append(_HookEntry(order=order, name=name, hook=hook))
        cls._hooks.sort()
        log.info("hook.registered", {"name": name, "order": order})

    @classmethod
    def unregister(cls, name: str) -> None:
        before = len(cls._hooks)
        cls._hooks = [h for h in cls._hooks if h.name != name]
        if len(cls._hooks) != before:
            log.info("hook.unregistered", {"name": name})

    @classmethod
    def list_hooks(cls) -> List[str]:
        return [h.name for h in cls._hooks]

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
    async def _run_stage(
        cls,
        stage: str,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
    ) -> HookContext:
        ctx = HookContext(stage=stage, input=input_data, output=output_data or {})
        for entry in cls._hooks:
            handler = cls._resolve_handler(entry.hook, stage)
            if not handler:
                continue
            try:
                result = handler(ctx)
                if isinstance(result, Awaitable):
                    await result
            except Exception as exc:
                log.error("hook.error", {
                    "stage": stage,
                    "hook": entry.name,
                    "error": str(exc),
                })
        return ctx

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

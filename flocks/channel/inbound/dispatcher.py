"""
InboundDispatcher — the core router for platform → Flocks message flow.

Sequence:
  dedup → allowlist → group-trigger → hook → binding → lock → user message → agent run

Includes the previously-separate middleware (MessageDedup, check_allowlist)
and ChannelDeliveryCallbacks, all of which are private to this module.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Optional

from flocks.channel.base import ChatType, InboundMessage, OutboundContext
from flocks.channel.inbound.session_binding import SessionBindingService
from flocks.config.config import ChannelConfig
from flocks.utils.log import Log

log = Log.create(service="channel.dispatcher")

_GROUP_CONTEXT_KEYS_MAX = 2000
_GROUP_CONTEXT_TEXT_MAX = 500


@dataclass
class _GroupContextEntry:
    sender_label: str
    text: str


def _parse_slash_command(text: str) -> tuple[Optional[str], str]:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None, ""
    parts = stripped[1:].split(None, 1)
    if not parts:
        return None, ""
    name = parts[0].strip().lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    return name or None, args


# =====================================================================
# MessageDedup — TTL-based dedup filter
# =====================================================================

class MessageDedup:
    """TTL-based dedup filter backed by an OrderedDict.

    IM platforms (especially in webhook mode) may deliver the same
    message_id more than once.  This filter ensures each id is
    processed at most once within the TTL window.
    """

    def __init__(self, ttl_seconds: int = 300, max_size: int = 10000) -> None:
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._seen: OrderedDict[str, float] = OrderedDict()

    def is_duplicate(self, message_id: str) -> bool:
        now = time.monotonic()
        self._evict_expired(now)

        if message_id in self._seen:
            return True

        self._seen[message_id] = now
        if len(self._seen) > self._max_size:
            self._seen.popitem(last=False)
        return False

    def _evict_expired(self, now: float) -> None:
        while self._seen:
            oldest_key, oldest_time = next(iter(self._seen.items()))
            if now - oldest_time > self._ttl:
                self._seen.popitem(last=False)
            else:
                break


# =====================================================================
# Allowlist / authorisation check
# =====================================================================

def _check_allowlist(msg: InboundMessage, config: ChannelConfig) -> bool:
    """Return True if the message is allowed through.

    Rules
    -----
    * ``dm_policy = "open"``       — all DMs are allowed (default).
    * ``dm_policy = "allowlist"``  — only senders in ``allow_from``.
    * ``dm_policy = "pairing"``    — reserved for future 1-on-1 flow.
    * Group messages are subject to ``allow_from`` when the list is
      non-empty, but are otherwise unrestricted.
    """
    allow_from = config.allow_from
    dm_policy = config.dm_policy or "open"

    if msg.chat_type == ChatType.DIRECT:
        if dm_policy == "open":
            return True
        if dm_policy == "allowlist":
            if not allow_from:
                log.debug("allowlist.dm_blocked_no_list", {"sender": msg.sender_id})
                return False
            return msg.sender_id in allow_from
        if dm_policy == "pairing":
            return True
        return True

    if allow_from and msg.sender_id not in allow_from:
        log.debug("allowlist.group_blocked", {
            "sender": msg.sender_id, "chat_id": msg.chat_id,
        })
        return False

    return True


# =====================================================================
# ChannelDeliveryCallbacks — bridges SessionLoop output → IM delivery
# =====================================================================

@dataclass
class ChannelDeliveryCallbacks:
    """LoopCallbacks-compatible struct that routes Agent output to a channel."""

    channel_id: str
    account_id: str
    chat_id: str
    thread_id: Optional[str] = None
    reply_to_id: Optional[str] = None
    session_id: Optional[str] = None

    def _build_ctx(self, text: str) -> OutboundContext:
        return OutboundContext(
            channel_id=self.channel_id,
            account_id=self.account_id,
            to=self.chat_id,
            text=text,
            reply_to_id=self.reply_to_id,
            thread_id=self.thread_id,
        )

    async def on_step_end(self, step: int) -> None:
        """Called by SessionLoop after each reasoning step (step number only)."""

    async def on_error(self, error_msg: str) -> None:
        """Deliver an error notification to the platform user."""
        await self.deliver_text(f"⚠ 处理消息时出错：{error_msg}")

    async def deliver_text(self, text: str) -> None:
        """Deliver arbitrary text to the bound conversation."""
        if not text:
            return
        from flocks.channel.outbound.deliver import OutboundDelivery
        await OutboundDelivery.deliver(
            self._build_ctx(text),
            session_id=self.session_id,
        )

    def to_loop_callbacks(self, runner_callbacks=None):
        """Convert to a LoopCallbacks dataclass understood by SessionLoop."""
        from flocks.session.session_loop import LoopCallbacks
        return LoopCallbacks(
            on_step_end=self.on_step_end,
            on_error=self.on_error,
            event_publish_callback=self._publish_sse_event,
            runner_callbacks=runner_callbacks,
        )

    @staticmethod
    async def _publish_sse_event(event_type: str, data: dict) -> None:
        """Forward loop events to the SSE stream so WebUI can observe channel sessions."""
        try:
            from flocks.server.routes.event import publish_event
            await publish_event(event_type, data)
        except Exception:
            pass


# =====================================================================
# Config cache
# =====================================================================

_CONFIG_CACHE_TTL = 60  # seconds


class _CachedConfig:
    __slots__ = ("config", "fetched_at")

    def __init__(self, config: ChannelConfig) -> None:
        self.config = config
        self.fetched_at = time.monotonic()

    def is_stale(self) -> bool:
        return (time.monotonic() - self.fetched_at) > _CONFIG_CACHE_TTL


_channel_config_cache: dict[str, _CachedConfig] = {}

_SESSION_LOCK_MAX = 5000


# =====================================================================
# InboundDispatcher
# =====================================================================

class InboundDispatcher:
    """Central dispatcher: platform message → Flocks Session → Agent → reply.

    Maintains per-session locks to serialise messages within one conversation.
    """

    def __init__(self) -> None:
        self.binding_service = SessionBindingService()
        self.dedup = MessageDedup(ttl_seconds=300, max_size=10000)
        self._session_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._group_context: OrderedDict[str, deque[_GroupContextEntry]] = OrderedDict()

    async def dispatch(self, msg: InboundMessage) -> None:
        # 1. dedup
        if self.dedup.is_duplicate(msg.message_id):
            log.debug("dispatcher.dedup", {"message_id": msg.message_id})
            return

        # 2. allowlist
        channel_config = await self._get_channel_config(msg.channel_id)
        if not _check_allowlist(msg, channel_config):
            log.debug("dispatcher.blocked", {"sender": msg.sender_id})
            return

        if self._cache_feishu_group_context(msg, channel_config):
            return

        # 3. group-trigger filter
        if not self._check_group_trigger(msg, channel_config):
            return

        # 4. channel.inbound hook — allows plugins to inspect/block/modify
        try:
            from flocks.hooks.pipeline import HookPipeline
            hook_ctx = await HookPipeline.run_channel_inbound({
                "channel_id": msg.channel_id,
                "sender_id": msg.sender_id,
                "chat_id": msg.chat_id,
                "chat_type": msg.chat_type.value,
                "text": msg.mention_text or msg.text,
                "message_id": msg.message_id,
            })
            if hook_ctx.output.get("blocked"):
                log.debug("dispatcher.hook_blocked", {"message_id": msg.message_id})
                return
            if "text" in hook_ctx.output:
                msg.text = hook_ctx.output["text"]
                msg.mention_text = hook_ctx.output["text"]
        except Exception as e:
            log.warning("dispatcher.hook_inbound_failed", {"error": str(e)})

        # 5. session binding — resolve feishu group-level overrides (scope & agent)
        # default_agent priority (matches WebUI):
        #   1. ChannelConfig.defaultAgent (or feishu group override)
        #   2. Agent.default_agent() — honours global ``defaultAgent`` and
        #      finally falls back to ``rex``
        # The literal string ``"default"`` is no longer used as a fallback,
        # because that was not a real agent name and silently fell through to
        # ``rex`` only via ``Agent.get(name) or Agent.get("rex")``, hiding the
        # real default and making behaviour diverge between WebUI and channel.
        default_agent = channel_config.default_agent
        scope_override = None
        if msg.channel_id == "feishu" and msg.chat_type == ChatType.GROUP:
            scope_override, feishu_agent = _resolve_feishu_group_overrides(
                channel_config, msg.chat_id,
            )
            if feishu_agent:
                default_agent = feishu_agent
        if not default_agent:
            try:
                from flocks.agent.registry import Agent as _Agent
                default_agent = await _Agent.default_agent()
            except Exception as exc:
                log.debug("dispatcher.default_agent_resolution_failed", {
                    "error": str(exc),
                })
                default_agent = "rex"

        binding = await self.binding_service.resolve_or_create(
            msg,
            default_agent=default_agent,
            scope_override=scope_override,
            directory=channel_config.workspace_dir,
        )

        user_text = msg.mention_text if msg.mention_text else msg.text
        if await self._handle_feishu_native_command(
            binding=binding,
            msg=msg,
            channel_config=channel_config,
            user_text=user_text,
            scope_override=scope_override,
        ):
            return

        recent_group_context = self._pull_feishu_group_context(msg, channel_config)

        # 6. publish inbound event
        try:
            from flocks.channel.events import ChannelMessageReceived
            from flocks.bus.bus import Bus
            await Bus.publish(ChannelMessageReceived, {
                "channel_id": msg.channel_id,
                "account_id": msg.account_id,
                "message_id": msg.message_id,
                "sender_id": msg.sender_id,
                "chat_id": msg.chat_id or msg.sender_id,
                "chat_type": msg.chat_type.value,
                "session_id": binding.session_id,
                "text": msg.mention_text or msg.text,
            })
        except Exception as e:
            log.warning("dispatcher.event_failed", {"error": str(e)})

        # 7. per-session lock → serialise messages in the same conversation
        lock = self._get_session_lock(binding.session_id)

        async with lock:
            if isinstance(recent_group_context, str) and recent_group_context.strip():
                user_text = f"{recent_group_context}\n\n[Current message]\n{user_text}".strip()

            # 8. 解析发送者名称（best-effort，失败不阻塞）
            if msg.channel_id == "feishu" and not msg.sender_name:
                try:
                    from flocks.channel.builtin.feishu.sender_name import resolve_sender_name
                    raw_cfg: dict = channel_config.model_dump(by_alias=True, exclude_none=True)
                    sender_name, is_perm_error = await resolve_sender_name(
                        msg.sender_id, raw_cfg, msg.account_id or "default",
                    )
                    if sender_name:
                        msg.sender_name = sender_name
                    # 权限错误：记录到上下文，5分钟内只通知一次
                    if is_perm_error:
                        account_id = msg.account_id or "default"
                        import time as _time
                        now = _time.monotonic()
                        last = _perm_notice_last.get(account_id, 0)
                        if now - last >= _PERM_NOTICE_COOLDOWN:
                            _perm_notice_last[account_id] = now
                            user_text = (
                                f"{user_text}\n\n"
                                "[System: Bot 可能缺少 contact:user.base:readonly 权限，"
                                "无法解析发送者姓名。请提醒管理员在飞书开放平台授权该权限。]"
                            )
                except Exception:
                    pass  # 名称解析失败不阻塞消息流程

            # 9. merge_forward 消息展开：主动拉取子消息并替换占位符
            if msg.channel_id == "feishu" and user_text.startswith("__merge_forward_expand__"):
                merge_msg_id = user_text[len("__merge_forward_expand__"):]
                try:
                    expanded = await _expand_merge_forward(
                        merge_msg_id,
                        channel_config,
                        msg.account_id or "default",
                    )
                    user_text = expanded
                    msg.text = expanded
                    msg.mention_text = expanded
                except Exception:
                    user_text = f"[合并转发消息 {merge_msg_id}]"
                    msg.text = user_text
                    msg.mention_text = user_text

            # 9b. 引用消息内容回溯（回复消息时，将被引用的原消息内容拼入上下文）
            if msg.channel_id == "feishu" and msg.reply_to_id:
                try:
                    quoted = await _fetch_quoted_message(
                        msg.reply_to_id,
                        channel_config,
                        msg.account_id or "default",
                    )
                    if quoted:
                        user_text = f'[回复: "{quoted}"]\n\n{user_text}'
                except Exception:
                    pass  # 引用内容获取失败不阻塞消息流程

            # 10. append user message to Session
            #
            # Resolve provider/model BEFORE writing the user message, mirroring
            # what _process_session_message does in the WebUI route. Storing
            # the resolved model on the user message keeps two things aligned
            # between WebUI and channel:
            #   - Title generation (``SessionLoop._run_loop`` reads
            #     ``last_user.model``).
            #   - The provider-specific base prompt template
            #     (``SystemPrompt.provider``) selected on the next loop tick.
            # Without this, channel sessions ended up with the hardcoded
            # ``defaults.fallback_*`` values from ``Message.create``, while
            # WebUI sessions got the real resolved model.
            resolved_model = await _resolve_session_model(binding.session_id)

            await self._append_user_message(
                binding.session_id,
                user_text,
                msg,
                channel_config,
                model=resolved_model,
                agent=binding.agent_id,
            )

            # 11. build delivery callbacks
            callbacks = ChannelDeliveryCallbacks(
                channel_id=msg.channel_id,
                account_id=msg.account_id,
                chat_id=msg.chat_id or msg.sender_id,
                thread_id=msg.thread_id,
                reply_to_id=msg.message_id,
                session_id=binding.session_id,
            )

            # 12. run Agent (inside lock to keep same-session serial)
            # Wrap with Typing Indicator for feishu channels when enabled
            if msg.channel_id == "feishu":
                await self._run_agent_with_typing(binding, callbacks, msg, channel_config)
            else:
                await self._run_agent(binding, callbacks)

    # --- helpers ---

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create a per-session lock with LRU eviction."""
        if session_id in self._session_locks:
            self._session_locks.move_to_end(session_id)
            return self._session_locks[session_id]
        lock = asyncio.Lock()
        self._session_locks[session_id] = lock
        if len(self._session_locks) > _SESSION_LOCK_MAX:
            self._evict_stale_locks()
        return lock

    def _evict_stale_locks(self) -> None:
        """Remove the oldest unlocked session locks until we're within capacity."""
        to_remove: list[str] = []
        for sid, lk in self._session_locks.items():
            if len(self._session_locks) - len(to_remove) <= _SESSION_LOCK_MAX:
                break
            if not lk.locked():
                to_remove.append(sid)
        for sid in to_remove:
            del self._session_locks[sid]

    def _cache_feishu_group_context(
        self,
        msg: InboundMessage,
        channel_config: ChannelConfig,
    ) -> bool:
        if msg.channel_id != "feishu" or msg.chat_type != ChatType.GROUP:
            return False

        limit = self._resolve_feishu_context_limit(channel_config, msg.chat_id)
        if limit <= 0:
            return False

        if msg.mentioned:
            return False

        text = self._summarize_group_context_text(msg)
        if not text:
            return True

        self._push_group_context_entry(
            self._group_context_key(msg),
            _GroupContextEntry(
                sender_label=msg.sender_name or msg.sender_id,
                text=text,
            ),
            limit=limit,
        )
        return True

    def _pull_feishu_group_context(
        self,
        msg: InboundMessage,
        channel_config: ChannelConfig,
    ) -> Optional[str]:
        if msg.channel_id != "feishu" or msg.chat_type != ChatType.GROUP or not msg.mentioned:
            return None

        limit = self._resolve_feishu_context_limit(channel_config, msg.chat_id)
        if limit <= 0:
            return None

        entries = self._group_context.pop(self._group_context_key(msg), None)
        if not entries:
            return None

        lines = ["[Recent group context]"]
        for entry in entries:
            lines.append(f"- {entry.sender_label}: {entry.text}")
        return "\n".join(lines)

    @staticmethod
    def _resolve_feishu_context_limit(channel_config: ChannelConfig, chat_id: str) -> int:
        from flocks.channel.builtin.feishu.config import merge_group_overrides

        merged = merge_group_overrides(channel_config.get_extra("groups"), chat_id)
        raw_limit = merged.get("mentionContextMessages")
        if raw_limit is None:
            raw_limit = channel_config.model_dump(
                by_alias=True,
                exclude_none=True,
            ).get("mentionContextMessages", 0)
        try:
            return max(0, int(raw_limit or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _group_context_key(msg: InboundMessage) -> str:
        thread_id = msg.thread_id or "main"
        return f"{msg.channel_id}:{msg.account_id}:{msg.chat_id}:thread:{thread_id}"

    @staticmethod
    def _summarize_group_context_text(msg: InboundMessage) -> str:
        text = (msg.mention_text or msg.text or "").strip()
        if not text and msg.media_url:
            if msg.media_url.startswith("lark://image/"):
                text = "[Image]"
            else:
                text = "[Attachment]"
        text = text.strip()
        if len(text) > _GROUP_CONTEXT_TEXT_MAX:
            text = text[:_GROUP_CONTEXT_TEXT_MAX].rstrip() + "..."
        return text

    def _push_group_context_entry(
        self,
        key: str,
        entry: _GroupContextEntry,
        *,
        limit: int,
    ) -> None:
        if key in self._group_context:
            self._group_context.move_to_end(key)
            entries = self._group_context[key]
        else:
            entries = deque(maxlen=limit)
            self._group_context[key] = entries

        if entries.maxlen != limit:
            entries = deque(entries, maxlen=limit)
            self._group_context[key] = entries

        entries.append(entry)

        while len(self._group_context) > _GROUP_CONTEXT_KEYS_MAX:
            self._group_context.popitem(last=False)

    @staticmethod
    def _check_group_trigger(msg: InboundMessage, config: ChannelConfig) -> bool:
        """Apply groupTrigger policy. DMs always pass."""
        if msg.chat_type == ChatType.DIRECT:
            return True
        trigger = config.group_trigger or "mention"
        if trigger == "all":
            return True
        if trigger == "mention":
            return msg.mentioned
        return False

    @staticmethod
    def _build_callbacks(binding, msg: InboundMessage) -> ChannelDeliveryCallbacks:
        return ChannelDeliveryCallbacks(
            channel_id=msg.channel_id,
            account_id=msg.account_id,
            chat_id=msg.chat_id or msg.sender_id,
            thread_id=msg.thread_id,
            reply_to_id=msg.message_id,
            session_id=binding.session_id,
        )

    async def _handle_feishu_native_command(
        self,
        *,
        binding,
        msg: InboundMessage,
        channel_config: ChannelConfig,
        user_text: str,
        scope_override: Optional[str],
    ) -> bool:
        command_name, command_args = _parse_slash_command(user_text)
        if not command_name:
            return False

        from flocks.command.command import Command
        from flocks.input.dispatcher import dispatch_user_input
        from flocks.input.events import UserInputEvent
        from flocks.input.output import ChannelOutputSink

        command_def = Command.resolve(command_name)
        if command_def is None:
            return False

        callbacks = self._build_callbacks(binding, msg)

        async def _publish_direct_response(_event, text: str) -> None:
            await callbacks.deliver_text(text)

        async def _run_llm(_event, prompt_text: str, display_text: Optional[str] = None) -> None:
            await callbacks.deliver_text(
                f"命令 `{display_text or prompt_text}` 暂不支持在当前渠道中以 slash 形式执行。"
            )

        async def _run_session_control(_event, parsed) -> bool:
            if parsed.canonical_name == "status":
                await self._handle_status_command(binding, msg, callbacks)
                return True
            if parsed.canonical_name == "model":
                await self._handle_model_command(binding, callbacks, command_args)
                return True
            if parsed.canonical_name == "new":
                await self._handle_session_command(
                    binding=binding,
                    msg=msg,
                    callbacks=callbacks,
                    scope_override=scope_override,
                )
                return True
            return False

        event = UserInputEvent(
            source_type=msg.channel_id if msg.channel_id in {"feishu", "wecom", "telegram"} else "channel",
            sessionID=binding.session_id,
            text=user_text,
            parts=[{"type": "text", "text": user_text}],
            agent=binding.agent_id,
            display_text=user_text,
            working_directory=channel_config.workspace_dir,
            metadata={
                "channel_id": msg.channel_id,
                "chat_id": msg.chat_id or msg.sender_id,
            },
        )
        await dispatch_user_input(
            event,
            ChannelOutputSink(
                "channel",
                direct_response=_publish_direct_response,
                run_llm=_run_llm,
                session_control=_run_session_control,
            ),
        )
        return True

    async def _handle_status_command(self, binding, msg: InboundMessage, callbacks: ChannelDeliveryCallbacks) -> None:
        from flocks.session.core.status import SessionStatus
        from flocks.session.session import Session
        from flocks.session.session_loop import SessionLoop

        session = await Session.get_by_id(binding.session_id)
        if not session:
            await callbacks.deliver_text("当前会话不存在，请发送一条普通消息后重试。")
            return

        provider_id, model_id = await SessionLoop._resolve_model(session, None, None)
        status = SessionStatus.get(binding.session_id)
        model_label = (
            f"{provider_id}/{model_id}"
            if provider_id and model_id
            else "未解析"
        )
        lines = [
            "当前会话状态：",
            f"- Session: `{binding.session_id}`",
            f"- Agent: `{session.agent or binding.agent_id or 'default'}`",
            f"- Model: `{model_label}`",
            f"- Status: `{status.type}`",
            f"- Chat: `{msg.chat_id or msg.sender_id}`",
        ]
        if binding.thread_id:
            lines.append(f"- Thread: `{binding.thread_id}`")
        await callbacks.deliver_text("\n".join(lines))

    async def _handle_model_command(
        self,
        binding,
        callbacks: ChannelDeliveryCallbacks,
        command_args: str,
    ) -> None:
        from flocks.provider.provider import Provider
        from flocks.session.session import Session
        from flocks.session.session_loop import SessionLoop

        session = await Session.get_by_id(binding.session_id)
        if not session:
            await callbacks.deliver_text("当前会话不存在，请发送一条普通消息后重试。")
            return

        args = command_args.strip()
        if not args:
            provider_id, model_id = await SessionLoop._resolve_model(session, None, None)
            if provider_id and model_id:
                await callbacks.deliver_text(f"当前模型：`{provider_id}/{model_id}`")
            else:
                await callbacks.deliver_text("当前模型未解析。")
            return

        if "/" not in args:
            await callbacks.deliver_text("用法：`/model provider/model`")
            return

        provider_id, model_id = args.split("/", 1)
        provider_id = provider_id.strip()
        model_id = model_id.strip()
        if not provider_id or not model_id:
            await callbacks.deliver_text("用法：`/model provider/model`")
            return

        provider = Provider.get(provider_id)
        if not provider:
            await callbacks.deliver_text(f"未找到 Provider：`{provider_id}`")
            return

        models = Provider.list_models(provider_id)
        if models:
            matched = any(
                getattr(model, "id", "") in {model_id, f"{provider_id}/{model_id}"}
                or getattr(model, "id", "").endswith(f"/{model_id}")
                for model in models
            )
            if not matched:
                await callbacks.deliver_text(
                    f"Provider `{provider_id}` 下未找到模型 `{model_id}`"
                )
                return

        await Session.update(
            session.project_id,
            session.id,
            **Session.explicit_model_updates(provider_id, model_id),
        )
        await self._trigger_command_hook(
            "model",
            session.id,
            {
                "provider_id": provider_id,
                "model_id": model_id,
            },
        )
        await callbacks.deliver_text(f"已切换到模型：`{provider_id}/{model_id}`")

    async def _handle_session_command(
        self,
        *,
        binding,
        msg: InboundMessage,
        callbacks: ChannelDeliveryCallbacks,
        scope_override: Optional[str],
    ) -> None:
        from flocks.session.session import Session
        from flocks.channel.inbound.session_binding import _build_title

        session = await Session.get_by_id(binding.session_id)
        if not session:
            await callbacks.deliver_text("当前会话不存在，请发送一条普通消息后重试。")
            return

        new_session = await Session.create(
            project_id=session.project_id,
            directory=session.directory,
            title=_build_title(msg),
            agent=session.agent,
            **Session.inherited_model_kwargs(session),
        )
        new_binding = await self.binding_service.rebind(
            msg,
            new_session.id,
            agent_id=new_session.agent,
            scope_override=scope_override,
        )
        await self._trigger_command_hook(
            "new",
            session.id,
            {
                "previous_session_id": session.id,
                "new_session_id": new_session.id,
                "channel_id": msg.channel_id,
                "chat_id": msg.chat_id or msg.sender_id,
            },
        )
        new_callbacks = self._build_callbacks(new_binding, msg)
        await new_callbacks.deliver_text(
            "\n".join(
                [
                    "已开始全新对话。",
                    f"Session: `{new_session.id}`",
                    f"Agent: `{new_session.agent or 'default'}`",
                ]
            )
        )

    @staticmethod
    async def _trigger_command_hook(action: str, session_id: str, context: dict[str, Any]) -> None:
        try:
            from flocks.hooks import create_command_event, trigger_hook

            await trigger_hook(create_command_event(action, session_id, context))
        except Exception as e:
            log.warning("dispatcher.command_hook_failed", {
                "action": action,
                "session_id": session_id,
                "error": str(e),
            })

    @staticmethod
    async def _get_channel_config(channel_id: str) -> ChannelConfig:
        cached = _channel_config_cache.get(channel_id)
        if cached and not cached.is_stale():
            return cached.config
        try:
            from flocks.config.config import Config
            cfg = await Config.get()
            ch_cfg = cfg.get_channel_config(channel_id)
            _channel_config_cache[channel_id] = _CachedConfig(ch_cfg)
            return ch_cfg
        except Exception:
            return ChannelConfig()

    @staticmethod
    async def _run_agent(binding, callbacks: ChannelDeliveryCallbacks) -> None:
        """Run Agent and deliver the final assistant reply."""
        try:
            from flocks.session.session_loop import SessionLoop
            loop_callbacks = callbacks.to_loop_callbacks()
            result = await SessionLoop.run(
                session_id=binding.session_id,
                agent_name=binding.agent_id,
                callbacks=loop_callbacks,
            )

            if result.last_message:
                text = await _extract_message_text(
                    binding.session_id, result.last_message,
                )
                if text:
                    await callbacks.deliver_text(text)
        except Exception as e:
            log.error("dispatcher.agent_error", {
                "session": binding.session_id,
                "error": str(e),
            })
            await callbacks.on_error(f"{type(e).__name__}: {e}")

    @staticmethod
    async def _run_agent_with_typing(
        binding,
        callbacks: ChannelDeliveryCallbacks,
        msg: InboundMessage,
        channel_config: ChannelConfig,
    ) -> None:
        """Run Agent wrapped with Feishu Typing Indicator and optional Streaming Card."""
        raw_cfg: dict = channel_config.model_dump(by_alias=True, exclude_none=True)
        streaming_enabled = raw_cfg.get("streaming", False)

        if streaming_enabled:
            try:
                await InboundDispatcher._run_agent_with_streaming(
                    binding, callbacks, msg, raw_cfg,
                )
                return
            except Exception:
                log.warning("dispatcher.streaming_setup_failed", {
                    "session": binding.session_id,
                })

        try:
            from flocks.channel.builtin.feishu.typing import feishu_typing_indicator
            async with feishu_typing_indicator(
                raw_cfg, msg.message_id, msg.account_id or "default",
            ):
                await InboundDispatcher._run_agent(binding, callbacks)
        except Exception:
            await InboundDispatcher._run_agent(binding, callbacks)

    @staticmethod
    async def _run_agent_with_streaming(
        binding,
        callbacks: ChannelDeliveryCallbacks,
        msg: InboundMessage,
        raw_cfg: dict,
    ) -> None:
        """Run Agent with Feishu Streaming Card for real-time text output."""
        from flocks.channel.builtin.feishu.streaming_card import StreamingCard

        coalesce_ms = int(raw_cfg.get("streamingCoalesceMs", 200))
        card = StreamingCard(
            config=raw_cfg,
            account_id=msg.account_id,
            chat_id=msg.chat_id or msg.sender_id,
            reply_to_id=msg.message_id,
            coalesce_ms=coalesce_ms,
        )

        message_id = await card.start()
        if card.is_degraded or not message_id:
            # Streaming unavailable, fall back to non-streaming path
            from flocks.channel.builtin.feishu.typing import feishu_typing_indicator
            async with feishu_typing_indicator(
                raw_cfg, msg.message_id, msg.account_id or "default",
            ):
                await InboundDispatcher._run_agent(binding, callbacks)
            return

        try:
            from flocks.session.runner import RunnerCallbacks

            async def _on_text_delta(delta: str) -> None:
                await card.append(delta)

            runner_cbs = RunnerCallbacks(on_text_delta=_on_text_delta)
            loop_callbacks = callbacks.to_loop_callbacks(runner_callbacks=runner_cbs)

            from flocks.session.session_loop import SessionLoop
            result = await SessionLoop.run(
                session_id=binding.session_id,
                agent_name=binding.agent_id,
                callbacks=loop_callbacks,
            )

            final_text = None
            if result.last_message:
                final_text = await _extract_message_text(
                    binding.session_id, result.last_message,
                )

            if final_text:
                await card.finalize(final_text)
            else:
                await card.finalize(card._current_text or "")

        except Exception as e:
            await card.abort(f"⚠ 处理时发生错误：{type(e).__name__}")
            log.error("dispatcher.streaming_agent_error", {
                "session": binding.session_id,
                "error": str(e),
            })
            await callbacks.on_error(f"{type(e).__name__}: {e}")

    @staticmethod
    async def _append_user_message(
        session_id: str,
        text: str,
        msg: InboundMessage,
        channel_config: Optional[ChannelConfig] = None,
        model: Optional[dict] = None,
        agent: Optional[str] = None,
    ) -> None:
        from flocks.session.message import FilePart, Message, MessageRole

        create_kwargs: dict = dict(
            session_id=session_id,
            role=MessageRole.USER,
            content=text,
            part_metadata={
                "source": "channel",
                "channel_id": msg.channel_id,
                "sender_id": msg.sender_id,
                "sender_name": msg.sender_name,
                "message_id": msg.message_id,
            },
        )
        if model is not None:
            create_kwargs["model"] = model
        if agent:
            create_kwargs["agent"] = agent

        message = await Message.create(**create_kwargs)

        if not msg.media_url or channel_config is None:
            return

        raw_cfg = channel_config.model_dump(by_alias=True, exclude_none=True)

        try:
            media = await _download_channel_media(msg, raw_cfg)
            if not media:
                return

            file_part = FilePart(
                sessionID=session_id,
                messageID=message.id,
                mime=media.mime,
                filename=media.filename,
                url=media.url,
                source=media.source,
            )
            await Message.store_part(session_id, message.id, file_part)

            try:
                from flocks.session.message import TextPart
                parts = await Message.parts(message.id, session_id=session_id)
                for p in parts:
                    if p.type == "text" and hasattr(p, "text") and (
                        p.text in ("[文件消息]", "[图片消息]")
                        or p.text.startswith("[文件消息:")
                    ):
                        from pathlib import PurePosixPath
                        file_path_str = media.url.replace("file://", "")
                        try:
                            display_path = str(PurePosixPath(file_path_str))
                        except Exception:
                            display_path = file_path_str
                        new_text = f"Attached files:\n- {display_path}"
                        updated = TextPart(
                            id=p.id,
                            sessionID=session_id,
                            messageID=message.id,
                            type="text",
                            text=new_text,
                        )
                        await Message.store_part(session_id, message.id, updated)
                        break
            except Exception:
                pass

            try:
                from flocks.server.routes.event import publish_event
                part_event = file_part.model_dump(by_alias=True, exclude_none=True)
                await publish_event("message.part.updated", {
                    "part": part_event,
                    "sessionID": session_id,
                })
            except Exception:
                pass
        except Exception as e:
            log.warning("dispatcher.inbound_media_download_failed", {
                "channel_id": msg.channel_id,
                "message_id": msg.message_id,
                "media_url": msg.media_url,
                "error": str(e),
            })


async def _extract_message_text(
    session_id: str,
    message: Any,
) -> Optional[str]:
    """Read the text parts of a MessageInfo and join them."""
    try:
        from flocks.session.message import Message
        msg_id = getattr(message, "id", None)
        if not msg_id:
            log.warning("dispatcher.extract_text.no_id", {
                "session": session_id,
                "message_type": type(message).__name__,
            })
            return None
        parts = await Message.parts(msg_id, session_id=session_id)
        text_parts = [
            p.text for p in parts
            if hasattr(p, "text") and p.text and getattr(p, "type", None) == "text"
        ]
        return "\n".join(text_parts) if text_parts else None
    except Exception as e:
        log.warning("dispatcher.extract_text.failed", {
            "session": session_id,
            "error": f"{type(e).__name__}: {e}",
        })
        return None


async def _resolve_session_model(session_id: str) -> Optional[dict]:
    """Resolve provider/model for a channel-bound session.

    Reuses ``SessionLoop._resolve_model`` so that channel and WebUI follow
    the exact same resolution chain (session-stored → agent override
    storage → AgentInfo.model → parent → config default → env). Returns
    a ``{"providerID", "modelID"}`` dict on success, ``None`` on failure
    so the caller can fall back to ``Message.create`` defaults.

    The session's own ``agent`` field is what drives agent-scoped lookups
    inside ``_resolve_model``, so no agent argument is needed here.
    """
    try:
        from flocks.session.session import Session as _Session
        from flocks.session.session_loop import SessionLoop

        session = await _Session.get_by_id(session_id)
        if not session:
            return None
        provider_id, model_id = await SessionLoop._resolve_model(
            session, None, None,
        )
        if not provider_id or not model_id:
            return None
        return {"providerID": provider_id, "modelID": model_id}
    except Exception as exc:
        log.debug("dispatcher.model_resolution_failed", {
            "session": session_id,
            "error": str(exc),
        })
        return None


def _resolve_feishu_group_overrides(
    channel_config: ChannelConfig,
    chat_id: str,
) -> tuple[Optional[str], Optional[str]]:
    """Read per-group ``groupSessionScope`` and ``defaultAgent`` from the feishu config.

    Returns ``(scope_override, agent_override)``.  Either may be ``None`` if not
    configured for the given *chat_id*.

    Resolution order (highest priority first):
        groups.<chat_id>  →  groups.*  →  top-level channel config
    """
    from flocks.channel.builtin.feishu.config import merge_group_overrides

    merged = merge_group_overrides(channel_config.get_extra("groups"), chat_id)
    if not merged:
        return None, None

    scope = (
        merged.get("groupSessionScope")
        or channel_config.get_extra("groupSessionScope")
        or None
    )
    agent = merged.get("defaultAgent") or None
    return scope, agent


async def _expand_merge_forward(
    message_id: str,
    channel_config: "ChannelConfig",
    account_id: str,
    max_messages: int = 50,
) -> str:
    """通过飞书 API 拉取合并转发消息的子消息并格式化为文本。

    与 openclaw 的 parseMergeForwardContent 逻辑对齐：
    - 最多展开 50 条子消息
    - 按 create_time 排序
    - 超时 3s 返回占位符
    """
    try:
        import asyncio as _asyncio
        import json as _json
        from flocks.channel.builtin.feishu.client import api_request_for_account
        from flocks.channel.builtin.feishu.monitor import _extract_content

        raw_cfg: dict = channel_config.model_dump(by_alias=True, exclude_none=True)
        data = await _asyncio.wait_for(
            api_request_for_account(
                "GET", f"/im/v1/messages/{message_id}",
                config=raw_cfg,
                account_id=account_id,
            ),
            timeout=3.0,
        )

        items = (data.get("data") or {}).get("items") or []
        if not items:
            return "[合并转发消息 - 无子消息]"

        # 过滤出子消息（有 upper_message_id 的条目）
        sub_messages = [item for item in items if item.get("upper_message_id")]
        if not sub_messages:
            return "[合并转发消息 - 无子消息]"

        # 按 create_time 排序
        sub_messages.sort(key=lambda x: int(x.get("create_time", "0") or "0"))

        lines = ["[合并转发消息]"]
        for item in sub_messages[:max_messages]:
            msg_type = item.get("msg_type", "text")
            try:
                body_content = _json.loads(item.get("body", {}).get("content", "{}"))
            except Exception:
                body_content = {}
            text, _ = _extract_content(msg_type, body_content)
            if text:
                lines.append(f"- {text}")

        if len(sub_messages) > max_messages:
            lines.append(f"... 还有 {len(sub_messages) - max_messages} 条消息")

        return "\n".join(lines)

    except Exception:
        return f"[合并转发消息 {message_id}]"


async def _fetch_quoted_message(
    parent_id: str,
    channel_config: ChannelConfig,
    account_id: str,
) -> Optional[str]:
    """通过飞书 API 获取被引用消息的文本内容（用于拼入上下文）。

    超时 2s 返回 None，不阻塞主流程。
    """
    try:
        import asyncio as _asyncio
        from flocks.channel.builtin.feishu.client import api_request_for_account
        from flocks.channel.builtin.feishu.monitor import _extract_content

        raw_cfg: dict = channel_config.model_dump(by_alias=True, exclude_none=True)
        data = await _asyncio.wait_for(
            api_request_for_account(
                "GET", f"/im/v1/messages/{parent_id}",
                config=raw_cfg,
                account_id=account_id,
            ),
            timeout=2.0,
        )
        items = (data.get("data") or {}).get("items") or []
        if not items:
            return None

        item = items[0]
        msg_type = item.get("msg_type", "")
        import json as _json
        try:
            content = _json.loads(item.get("body", {}).get("content", "{}"))
        except Exception:
            content = {}
        text, _ = _extract_content(msg_type, content)
        return text.strip() or None

    except Exception:
        return None


# 权限错误通知冷却时间（同一账号 5 分钟内只通知一次）
_PERM_NOTICE_COOLDOWN = 300
_perm_notice_last: dict[str, float] = {}


async def _download_channel_media(msg: InboundMessage, config: dict):
    """Dispatch inbound media download to the appropriate channel handler."""
    if msg.channel_id == "feishu":
        from flocks.channel.builtin.feishu.inbound_media import download_inbound_media
        return await download_inbound_media(msg, config)

    if msg.channel_id == "wecom":
        from flocks.channel.builtin.wecom.inbound_media import download_inbound_media
        return await download_inbound_media(msg, config)

    return None

"""Resolve a messaging target and optionally deliver a message.

Calls with an exact ``session_id`` and a message preserve deterministic workflow
delivery. Calls without a session resolve the current or hinted target first. Omitting
``message`` returns the resolved binding without sending.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)

_CHANNEL_ALIASES: dict[str, list[str]] = {
    "wecom": ["wecom", "企微", "企业微信", "wechat_work", "wxwork"],
    "weixin": ["weixin", "微信", "wechat", "wx"],
    "feishu": ["feishu", "飞书", "lark"],
    "dingtalk": ["dingtalk", "钉钉", "dingding", "dingtalk-connector"],
    "telegram": ["telegram", "tg", "tele"],
    "whatsapp": ["whatsapp", "wa"],
    "email": ["email", "mail", "邮件", "imap", "smtp"],
    "slack": ["slack", "sl"],
}


def _get_server_port() -> int:
    """Return the port of the API server hosting the current process."""
    try:
        runtime_port = os.environ.get("_FLOCKS_SERVER_PORT")
        if runtime_port:
            return int(runtime_port)

        from flocks.config import Config

        return Config.get_global().server_port
    except Exception:
        return 8000


def _normalize_channel_type(channel_type: str | None) -> str | None:
    """Normalize a user-supplied channel_type (Chinese or English) to its canonical channel id."""
    if not channel_type:
        return None
    lower = channel_type.strip().lower()
    for canonical, aliases in _CHANNEL_ALIASES.items():
        if lower in [a.lower() for a in aliases]:
            return canonical
    return lower


@dataclass(frozen=True)
class _Candidate:
    """One exact channel binding that can receive a message."""

    session_id: str
    channel_id: str
    account_id: str
    chat_type: str
    chat_id: str
    title: str
    last_message_at: float

    @property
    def label(self) -> str:
        return f"{self.title} [{self.channel_id}] ({self.session_id})"

    @property
    def description(self) -> str:
        return f"account_id={self.account_id} chat_type={self.chat_type} chat_id={self.chat_id}"


def _matches_target(candidate: _Candidate, target: str | None) -> bool:
    if not target:
        return True
    needle = target.strip().lower()
    if not needle:
        return True
    return (
        needle in candidate.session_id.lower()
        or needle in candidate.channel_id.lower()
        or needle in candidate.title.lower()
        or needle in candidate.chat_id.lower()
    )


async def _list_candidates(
    channel_type: str | None = None,
    target: str | None = None,
) -> list[_Candidate]:
    from flocks.channel.inbound.session_binding import SessionBindingService
    from flocks.session.session import Session

    svc = SessionBindingService()
    bindings = await svc.list_bindings(channel_id=channel_type)
    candidates: list[_Candidate] = []

    for binding in bindings:
        session = await Session.get_by_id(binding.session_id)
        if not session or session.status != "active" or session.category != "user":
            continue
        candidate = _Candidate(
            session_id=binding.session_id,
            channel_id=binding.channel_id,
            account_id=binding.account_id,
            chat_type=binding.chat_type.value if binding.chat_type else "unknown",
            chat_id=binding.chat_id,
            title=session.title,
            last_message_at=binding.last_message_at,
        )
        if _matches_target(candidate, target):
            candidates.append(candidate)

    return sorted(candidates, key=lambda candidate: candidate.last_message_at, reverse=True)


async def _current_session_candidates(
    ctx: ToolContext,
    channel_type: str | None,
) -> list[_Candidate]:
    if not ctx.session_id:
        return []
    candidates = await _list_candidates(channel_type=channel_type, target=ctx.session_id)
    return [candidate for candidate in candidates if candidate.session_id == ctx.session_id]


async def _is_interactive_context(ctx: ToolContext) -> bool:
    """Return whether target ambiguity may be resolved by asking the user."""
    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    workflow_context = extra.get("workflow_context")
    if isinstance(workflow_context, dict) and workflow_context.get("source") == "workflow_runtime":
        return False

    execution_profile = extra.get("session_execution_profile")
    if isinstance(execution_profile, dict) and execution_profile.get("entry") == "workflow":
        return False

    try:
        from flocks.session.session import Session

        session = await Session.get_by_id(ctx.session_id)
        if session is not None:
            return session.category == "user"
    except Exception:
        pass

    return True


async def _ask_user_to_choose(
    ctx: ToolContext,
    candidates: list[_Candidate],
) -> ToolResult:
    from flocks.tool.system.question import question_tool

    options = [{"label": candidate.label, "description": candidate.description} for candidate in candidates]
    options.append(
        {
            "label": "I don't know",
            "description": "Stop and ask me to provide an exact session ID.",
        }
    )
    return await question_tool(
        ctx,
        questions=[
            {
                "question": "Which messaging session should receive this message?",
                "type": "choice",
                "options": options,
            }
        ],
    )


def _selected_candidate(
    question_result: ToolResult,
    candidates: list[_Candidate],
) -> _Candidate | None:
    answers: Any = (question_result.metadata or {}).get("answers")
    if not answers or not answers[0]:
        return None

    selected_label = str(answers[0][0])
    for candidate in candidates:
        if candidate.label == selected_label:
            return candidate
    return None


def _resolution_result(candidate: _Candidate) -> ToolResult:
    return ToolResult(
        success=True,
        output=(
            f"Resolved messaging target: session_id={candidate.session_id} "
            f"channel_type={candidate.channel_id} chat_type={candidate.chat_type} "
            f"account_id={candidate.account_id} chat_id={candidate.chat_id}"
        ),
        metadata={"mode": "resolve", "target": candidate.__dict__},
    )


async def _resolve_candidates(
    ctx: ToolContext,
    candidates: list[_Candidate],
) -> ToolResult:
    if len(candidates) == 1:
        return _resolution_result(candidates[0])

    if not await _is_interactive_context(ctx):
        return ToolResult(
            success=False,
            error=(
                "Multiple messaging targets matched. Workflows and background tasks must provide an exact session_id."
            ),
        )

    question_result = await _ask_user_to_choose(ctx, candidates)
    if not question_result.success or (question_result.metadata or {}).get("deferred"):
        return question_result

    selected = _selected_candidate(question_result, candidates)
    if selected is None:
        return ToolResult(
            success=False,
            error="No messaging target selected. Provide an exact session_id before sending.",
        )
    return _resolution_result(selected)


async def _resolve_target(
    ctx: ToolContext,
    session_id: str | None,
    channel_type: str | None,
    target: str | None,
    account_id: str | None = None,
    chat_id: str | None = None,
) -> ToolResult:
    if session_id:
        candidates = await _list_candidates(channel_type=channel_type, target=session_id)
        candidates = [
            candidate
            for candidate in candidates
            if candidate.session_id == session_id
            and (not account_id or candidate.account_id == account_id)
            and (not chat_id or candidate.chat_id == chat_id)
        ]
        if not candidates:
            return ToolResult(
                success=False,
                error=f"No active messaging binding found for session_id='{session_id}'.",
            )
        return await _resolve_candidates(ctx, candidates)

    current_candidates = await _current_session_candidates(ctx, channel_type)
    if current_candidates and not target:
        return await _resolve_candidates(ctx, current_candidates)

    candidates = await _list_candidates(channel_type=channel_type, target=target)
    if not candidates:
        filter_text = f" matching '{target}'" if target else ""
        channel_text = f" for channel_type='{channel_type}'" if channel_type else ""
        return ToolResult(
            success=False,
            error=(
                f"No active messaging sessions found{channel_text}{filter_text}. "
                "Ask the user to message the Flocks bot from the target chat first, "
                "or provide an exact session_id."
            ),
        )
    return await _resolve_candidates(ctx, candidates)


def _get_api_token() -> str | None:
    """Read the server API token from the secret manager (non-async, best-effort).

    Reuses ``API_TOKEN_SECRET_ID`` from ``flocks.server.auth`` so that the
    secret id stays in lockstep with what the server-side auth middleware
    expects; if those drift apart the request will silently start failing
    with 401.
    """
    try:
        from flocks.security import get_secret_manager
        from flocks.server.auth import API_TOKEN_SECRET_ID

        token = get_secret_manager().get(API_TOKEN_SECRET_ID)
        return token.strip() if token and token.strip() else None
    except Exception:
        return None


async def _http_session_send(
    port: int,
    session_id: str,
    text: str,
    channel_type: str | None = None,
    media_url: str | None = None,
    account_id: str | None = None,
    chat_id: str | None = None,
) -> ToolResult | None:
    """Send a message via the running flocks server's /api/channel/session-send endpoint,
    reusing the already-established WebSocket connection.

    Returns None when the HTTP path is unavailable (server not running),
    signalling the caller to fall back to the in-process path.
    """
    try:
        import httpx

        payload: dict = {"session_id": session_id, "text": text}
        if channel_type:
            payload["channel_type"] = channel_type
        if media_url:
            payload["media_url"] = media_url
        if account_id:
            payload["account_id"] = account_id
        if chat_id:
            payload["chat_id"] = chat_id

        headers: dict[str, str] = {}
        api_token = _get_api_token()
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"http://localhost:{port}/api/channel/session-send",
                json=payload,
                headers=headers,
                timeout=10.0,
            )
            body = resp.json()
            if resp.status_code == 200:
                resolved_session_id = body.get("session_id") or session_id
                return ToolResult(
                    success=True,
                    output=(
                        f"Message sent to session '{resolved_session_id}' "
                        f"via channels {body.get('channels', [])}, "
                        f"ids: {body.get('message_ids', [])}"
                    ),
                    metadata={
                        "mode": "send",
                        "session_id": resolved_session_id,
                        "channels": body.get("channels", []),
                        "message_ids": body.get("message_ids", []),
                    },
                )
            # 401 + we had no token to present: either the secret is unset
            # or this process can't read it. Either way, the in-process
            # path bypasses HTTP auth and can still deliver the message,
            # so we fall back instead of surfacing an error.
            # (If we DID send a token and it was rejected, fall through
            # and report the server's detail so misconfiguration is visible.)
            if resp.status_code == 401 and not api_token:
                return None
            return ToolResult(
                success=False,
                error=f"Send failed (HTTP {resp.status_code}): {body.get('detail', body)}",
            )
    except ImportError:
        return None
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return None  # server not running — fall back to in-process path
    except Exception as e:
        return ToolResult(success=False, error=f"HTTP send failed: {e}")


@ToolRegistry.register_function(
    name="channel_message",
    description=(
        "Resolve a connected messaging target and optionally send a message. "
        "Use this tool for WeCom/企业微信, Weixin/微信, Feishu, DingTalk, Telegram, "
        "WhatsApp, Email/邮件, Slack, and custom messaging channels. "
        "Provide message to resolve the target and send. Omit message to resolve and "
        "return the target without sending, for example before creating a scheduled task. "
        "Provide session_id for deterministic workflow and background delivery. "
        "When session_id is omitted, the tool resolves the current messaging session or "
        "searches using target and channel_type. Interactive sessions may ask the user to "
        "choose when multiple targets match. Workflows and background tasks must provide "
        "an exact session_id."
    ),
    description_cn=(
        "解析已连接的消息渠道目标，并可选择发送消息。提供 message 时解析并发送；"
        "不提供 message 时只返回目标，可用于创建定时任务前确定 session_id。"
        "Workflow 和后台任务应提供明确的 session_id。"
    ),
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="message",
            type=ParameterType.STRING,
            required=False,
            description=(
                "Message content (Markdown supported). When omitted, the tool only "
                "resolves and returns the target without sending."
            ),
        ),
        ToolParameter(
            name="session_id",
            type=ParameterType.STRING,
            required=False,
            description=(
                "Exact Flocks session ID. Workflows and background tasks should always "
                "provide this for deterministic delivery."
            ),
        ),
        ToolParameter(
            name="channel_type",
            type=ParameterType.STRING,
            required=False,
            description=(
                "Optional channel filter. Built-in values include wecom=企业微信, "
                "weixin=微信, feishu=飞书, dingtalk=钉钉, telegram, whatsapp, "
                "email=邮件, and slack. Chinese aliases and custom channel IDs are accepted."
            ),
        ),
        ToolParameter(
            name="target",
            type=ParameterType.STRING,
            required=False,
            description=(
                "Optional target hint matching a session title, session ID, channel ID, "
                "or chat ID. Do not combine with session_id."
            ),
        ),
        ToolParameter(
            name="media",
            type=ParameterType.STRING,
            required=False,
            description="Media URL or local file path (optional).",
        ),
        ToolParameter(
            name="account_id",
            type=ParameterType.STRING,
            required=False,
            description="Advanced exact binding filter. Requires session_id.",
        ),
        ToolParameter(
            name="chat_id",
            type=ParameterType.STRING,
            required=False,
            description="Advanced exact chat binding filter. Requires session_id.",
        ),
    ],
)
async def channel_message(ctx: ToolContext, **kwargs) -> ToolResult:
    message: str | None = kwargs.get("message")
    session_id: str | None = kwargs.get("session_id")
    target: str | None = kwargs.get("target")
    media: str | None = kwargs.get("media")
    account_id: str | None = kwargs.get("account_id")
    chat_id: str | None = kwargs.get("chat_id")
    raw_channel_type: str | None = kwargs.get("channel_type")
    channel_type: str | None = _normalize_channel_type(raw_channel_type)

    if message is not None and not message.strip():
        return ToolResult(success=False, error="message must not be empty.")
    if session_id and target:
        return ToolResult(
            success=False,
            error=(
                "session_id and target cannot be used together. Provide an exact session_id or a target hint, not both."
            ),
        )
    if (account_id or chat_id) and not session_id:
        return ToolResult(
            success=False,
            error="account_id and chat_id require an exact session_id.",
        )
    if media and message is None:
        return ToolResult(success=False, error="media requires a non-empty message.")
    if not any((message, session_id, target, channel_type)):
        return ToolResult(
            success=False,
            error="Provide message, session_id, target, or channel_type.",
        )

    # Preserve the existing deterministic workflow contract: an exact session
    # plus a message goes directly through the delivery path without discovery
    # or user interaction.
    if session_id and message is not None:
        return await _send_message_to_session(
            ctx,
            session_id=session_id,
            message=message,
            channel_type=channel_type,
            raw_channel_type=raw_channel_type,
            media=media,
            account_id=account_id,
            chat_id=chat_id,
        )

    resolved = await _resolve_target(
        ctx,
        session_id,
        channel_type,
        target,
        account_id,
        chat_id,
    )
    if not resolved.success or message is None or (resolved.metadata or {}).get("deferred"):
        return resolved

    resolved_target = (resolved.metadata or {}).get("target") or {}
    resolved_session_id = resolved_target.get("session_id")
    if not resolved_session_id:
        return ToolResult(success=False, error="Failed to resolve a messaging session_id.")

    return await _send_message_to_session(
        ctx,
        session_id=resolved_session_id,
        message=message,
        channel_type=resolved_target.get("channel_id"),
        raw_channel_type=resolved_target.get("channel_id"),
        media=media,
        account_id=resolved_target.get("account_id"),
        chat_id=resolved_target.get("chat_id"),
    )


async def _send_message_to_session(
    ctx: ToolContext,
    *,
    session_id: str,
    message: str,
    channel_type: str | None,
    raw_channel_type: str | None,
    media: str | None,
    account_id: str | None,
    chat_id: str | None,
) -> ToolResult:
    """Deliver a message to an exact session using the existing transport path."""

    # Prefer the HTTP endpoint of the running flocks server to reuse its WS connection.
    port = _get_server_port()

    result = await _http_session_send(
        port,
        session_id,
        message,
        channel_type,
        media,
        account_id,
        chat_id,
    )
    if result is not None:
        return result

    # Fallback: in-process delivery (requires the channel to be started in the same process).
    from flocks.channel.inbound.session_binding import SessionBindingService
    from flocks.channel.outbound.deliver import OutboundDelivery
    from flocks.channel.base import OutboundContext

    svc = SessionBindingService()
    all_bindings = await svc.list_bindings()
    matched = [b for b in all_bindings if b.session_id == session_id]
    resolved_session_id = session_id

    if not matched and channel_type:
        latest = await svc.latest_active_user_binding(
            channel_id=channel_type,
            account_id=account_id,
            chat_id=chat_id,
        )
        if latest:
            matched = [latest]
            resolved_session_id = latest.session_id

    if not matched:
        return ToolResult(
            success=False,
            error=(
                f"No channel binding found for session_id='{session_id}'. "
                "Resolve the current messaging target again by calling channel_message "
                "without message, or ask the user to confirm the target session."
            ),
        )

    if channel_type:
        filtered = [b for b in matched if b.channel_id == channel_type]
        if not filtered:
            available = list({b.channel_id for b in matched})
            return ToolResult(
                success=False,
                error=(
                    f"Session '{session_id}' has no binding for channel_type='{raw_channel_type}'. "
                    f"Available channels: {available}"
                ),
            )
        targets = filtered
    else:
        targets = matched

    if account_id:
        targets = [b for b in targets if b.account_id == account_id]
    if chat_id:
        targets = [b for b in targets if b.chat_id == chat_id]
    if (account_id or chat_id) and not targets:
        return ToolResult(
            success=False,
            error=(f"Session '{session_id}' has no binding matching account_id='{account_id}' chat_id='{chat_id}'."),
        )

    all_results = []
    errors = []

    for binding in targets:
        out_ctx = OutboundContext(
            channel_id=binding.channel_id,
            account_id=binding.account_id,
            to=binding.chat_id,
            text=message,
            media_url=media,
        )
        results = await OutboundDelivery.deliver(out_ctx, session_id=resolved_session_id)
        all_results.extend(results)

        failed = [r for r in results if not r.success]
        if failed:
            errors.append(f"[{binding.channel_id}/{binding.chat_id}] {failed[0].error}")

    if errors:
        return ToolResult(
            success=False,
            error="Delivery failed for some channels:\n" + "\n".join(errors),
        )

    msg_ids = [r.message_id for r in all_results if r.message_id]
    channels_sent = list({b.channel_id for b in targets})
    return ToolResult(
        success=True,
        output=(
            f"Message sent to session '{resolved_session_id}' "
            f"via channels {channels_sent}, "
            f"{len(all_results)} chunk(s), ids: {msg_ids}"
        ),
        metadata={
            "mode": "send",
            "session_id": resolved_session_id,
            "channels": channels_sent,
            "message_ids": msg_ids,
        },
    )

"""High-level IM sending helper built on top of channel_message."""

from __future__ import annotations

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
}


@dataclass(frozen=True)
class _Candidate:
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
        return f"session_id={self.session_id} chat_type={self.chat_type} chat_id={self.chat_id}"


def _normalize_channel_type(channel_type: str | None) -> str | None:
    if not channel_type:
        return None
    lower = channel_type.strip().lower()
    for canonical, aliases in _CHANNEL_ALIASES.items():
        if lower in [alias.lower() for alias in aliases]:
            return canonical
    return lower


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


async def _list_candidates(channel_type: str | None = None, target: str | None = None) -> list[_Candidate]:
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

    return sorted(candidates, key=lambda c: c.last_message_at, reverse=True)


async def _current_session_candidates(ctx: ToolContext, channel_type: str | None) -> list[_Candidate]:
    if not ctx.session_id:
        return []
    candidates = await _list_candidates(channel_type=channel_type, target=ctx.session_id)
    return [candidate for candidate in candidates if candidate.session_id == ctx.session_id]


async def _ask_user_to_choose(ctx: ToolContext, candidates: list[_Candidate]) -> ToolResult:
    from flocks.tool.system.question import question_tool

    options = [
        {"label": candidate.label, "description": candidate.description}
        for candidate in candidates
    ]
    options.append({
        "label": "I don't know",
        "description": "Stop and ask me to provide a session ID.",
    })

    return await question_tool(
        ctx,
        questions=[
            {
                "question": "Which IM session should receive this message?",
                "type": "choice",
                "options": options,
            }
        ],
    )


def _selected_candidate(question_result: ToolResult, candidates: list[_Candidate]) -> _Candidate | None:
    answers: Any = (question_result.metadata or {}).get("answers")
    if not answers or not answers[0]:
        return None

    selected_label = str(answers[0][0])
    for candidate in candidates:
        if candidate.label == selected_label:
            return candidate
    return None


def _resolution_output(candidate: _Candidate) -> str:
    return (
        f"Resolved IM target: session_id={candidate.session_id} "
        f"channel_type={candidate.channel_id} chat_type={candidate.chat_type}"
    )


async def _resolve_target(
    ctx: ToolContext,
    session_id: str | None,
    channel_type: str | None,
    target: str | None,
) -> ToolResult:
    if session_id:
        candidates = await _list_candidates(channel_type=channel_type, target=session_id)
        exact = [candidate for candidate in candidates if candidate.session_id == session_id]
        if exact:
            return ToolResult(success=True, output=_resolution_output(exact[0]), metadata={"target": exact[0].__dict__})
        return ToolResult(
            success=False,
            error=f"No active IM binding found for session_id='{session_id}'.",
        )

    current_candidates = await _current_session_candidates(ctx, channel_type)
    if len(current_candidates) == 1 and not target:
        candidate = current_candidates[0]
        return ToolResult(success=True, output=_resolution_output(candidate), metadata={"target": candidate.__dict__})

    candidates = await _list_candidates(channel_type=channel_type, target=target)
    if not candidates:
        filter_text = f" matching '{target}'" if target else ""
        channel_text = f" for channel_type='{channel_type}'" if channel_type else ""
        return ToolResult(
            success=False,
            error=(
                f"No active IM sessions found{channel_text}{filter_text}. "
                "Ask the user to send a message to the Flocks bot from the target IM chat first, "
                "or provide an exact session_id."
            ),
        )

    if len(candidates) == 1:
        candidate = candidates[0]
        return ToolResult(success=True, output=_resolution_output(candidate), metadata={"target": candidate.__dict__})

    question_result = await _ask_user_to_choose(ctx, candidates)
    if not question_result.success:
        return question_result
    if (question_result.metadata or {}).get("deferred"):
        return question_result

    selected = _selected_candidate(question_result, candidates)
    if selected is None:
        return ToolResult(
            success=False,
            error="No IM session selected. Ask the user for the exact session_id before sending.",
        )

    return ToolResult(success=True, output=_resolution_output(selected), metadata={"target": selected.__dict__})


@ToolRegistry.register_function(
    name="im_send_message",
    description=(
        "Resolve an IM target session and optionally send a message. "
        "Use this for WeCom/企业微信, Weixin/微信, Feishu, DingTalk, or custom channel sessions when the user asks to send an IM message. "
        "Use channel_type=wecom for 企业微信 and channel_type=weixin for 微信. "
        "If session_id is omitted, it uses the current IM session when available, otherwise asks the user to pick one."
    ),
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="message",
            type=ParameterType.STRING,
            required=False,
            description="Message content to send. Required unless resolve_only=true.",
        ),
        ToolParameter(
            name="session_id",
            type=ParameterType.STRING,
            required=False,
            description="Exact Flocks session ID for the target IM chat, if already known.",
        ),
        ToolParameter(
            name="channel_type",
            type=ParameterType.STRING,
            required=False,
            description=(
                "Optional channel filter, such as wecom=企业微信, weixin=微信, "
                "feishu, dingtalk, telegram, or a custom channel id."
            ),
        ),
        ToolParameter(
            name="target",
            type=ParameterType.STRING,
            required=False,
            description="Optional target hint: platform name, session title, session ID fragment, or chat ID fragment.",
        ),
        ToolParameter(
            name="media",
            type=ParameterType.STRING,
            required=False,
            description="Media URL or local file path (optional).",
        ),
        ToolParameter(
            name="resolve_only",
            type=ParameterType.BOOLEAN,
            required=False,
            default=False,
            description="Resolve and return session_id/channel_type without sending. Use before schedule_task_create.",
        ),
    ],
)
async def im_send_message(ctx: ToolContext, **kwargs) -> ToolResult:
    message: str | None = kwargs.get("message")
    session_id: str | None = kwargs.get("session_id")
    target: str | None = kwargs.get("target")
    media: str | None = kwargs.get("media")
    resolve_only: bool = bool(kwargs.get("resolve_only", False))
    channel_type = _normalize_channel_type(kwargs.get("channel_type"))

    if not resolve_only and not message:
        return ToolResult(success=False, error="message is required unless resolve_only=true.")

    resolved = await _resolve_target(ctx, session_id, channel_type, target)
    if not resolved.success or resolve_only or (resolved.metadata or {}).get("deferred"):
        return resolved

    resolved_target = (resolved.metadata or {}).get("target") or {}
    resolved_session_id = resolved_target.get("session_id")
    resolved_channel_type = resolved_target.get("channel_id")
    resolved_account_id = resolved_target.get("account_id")
    resolved_chat_id = resolved_target.get("chat_id")
    if not resolved_session_id:
        return ToolResult(success=False, error="Failed to resolve an IM session_id.")

    from flocks.tool.channel.channel_message import channel_message

    return await channel_message(
        ctx,
        session_id=resolved_session_id,
        message=message,
        channel_type=resolved_channel_type,
        account_id=resolved_account_id,
        chat_id=resolved_chat_id,
        media=media,
    )

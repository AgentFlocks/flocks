"""
channel_message tool — sends a message to the IM channel bound to a given session.

Looks up the SessionBinding for the given session_id to automatically resolve
the target channel (WeCom / Feishu / DingTalk) and chat_id, so the caller
does not need to specify them manually.

The optional channel_type parameter selects a specific channel when a session
is bound to multiple channels.
"""

from __future__ import annotations

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


def _normalize_channel_type(channel_type: str | None) -> str | None:
    """Normalize a user-supplied channel_type (Chinese or English) to its canonical channel id."""
    if not channel_type:
        return None
    lower = channel_type.strip().lower()
    for canonical, aliases in _CHANNEL_ALIASES.items():
        if lower in [a.lower() for a in aliases]:
            return canonical
    return lower


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
                return ToolResult(
                    success=True,
                    output=(
                        f"Message sent to session '{session_id}' "
                        f"via channels {body.get('channels', [])}, "
                        f"ids: {body.get('message_ids', [])}"
                    ),
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
        "Send a message to the IM channel bound to a session. "
        "Channel types: WeCom/企业微信=wecom, Weixin/微信=weixin, Feishu=feishu, DingTalk=dingtalk. "
        "Resolves the target channel and chat automatically from session_id. "
        "Use channel_type to target a specific channel when the session has multiple bindings."
    ),
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="session_id",
            type=ParameterType.STRING,
            required=True,
            description="ID of the target session. The tool resolves the bound IM channel and chat from this.",
        ),
        ToolParameter(
            name="message",
            type=ParameterType.STRING,
            required=True,
            description="Message content (Markdown supported).",
        ),
        ToolParameter(
            name="channel_type",
            type=ParameterType.STRING,
            required=False,
            enum=["wecom", "weixin", "feishu", "dingtalk", "企微", "企业微信", "微信", "飞书", "钉钉"],
            description=(
                "Target channel: wecom=企业微信, weixin=微信, feishu=飞书, or dingtalk=钉钉. "
                "Chinese aliases are accepted. "
                "If omitted and the session has only one binding, that channel is used automatically. "
                "If omitted and the session has multiple bindings, the message is sent to all of them."
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
            description="Optional exact binding filter. Usually supplied by im_send_message after target resolution.",
        ),
        ToolParameter(
            name="chat_id",
            type=ParameterType.STRING,
            required=False,
            description="Optional exact binding filter. Usually supplied by im_send_message after target resolution.",
        ),
    ],
)
async def channel_message(ctx: ToolContext, **kwargs) -> ToolResult:
    session_id: str = kwargs["session_id"]
    message: str = kwargs["message"]
    media: str | None = kwargs.get("media")
    account_id: str | None = kwargs.get("account_id")
    chat_id: str | None = kwargs.get("chat_id")
    raw_channel_type: str | None = kwargs.get("channel_type")
    channel_type: str | None = _normalize_channel_type(raw_channel_type)

    # Prefer the HTTP endpoint of the running flocks server to reuse its WS connection.
    try:
        from flocks.config import Config
        cfg = await Config.get()
        port = getattr(cfg, "port", None) or 8000
    except Exception:
        port = 8000

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

    if not matched:
        return ToolResult(
            success=False,
            error=(
                f"No channel binding found for session_id='{session_id}'. "
                "Make sure the session was initiated via an IM channel."
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
            error=(
                f"Session '{session_id}' has no binding matching "
                f"account_id='{account_id}' chat_id='{chat_id}'."
            ),
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
        results = await OutboundDelivery.deliver(out_ctx, session_id=session_id)
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
            f"Message sent to session '{session_id}' "
            f"via channels {channels_sent}, "
            f"{len(all_results)} chunk(s), ids: {msg_ids}"
        ),
    )

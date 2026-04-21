"""
DingTalk outbound send library.

This package intentionally does **not** ship a ``ChannelPlugin`` class —
the inbound side is owned by the project-local plugin
``.flocks/plugins/channels/dingtalk/dingtalk.py`` (Node.js connector).

Other code (channel plugins, tools, hooks, …) can drive active outbound
messages by importing :func:`send_message_app` directly.  Only the
enterprise app robot OAPI path is supported; custom group webhooks are
intentionally out of scope.
"""

from flocks.channel.builtin.dingtalk.client import (
    DingTalkApiError,
    close_http_client,
)
from flocks.channel.builtin.dingtalk.config import (
    strip_target_prefix,
)
from flocks.channel.builtin.dingtalk.send import (
    build_app_payload,
    send_message_app,
)

__all__ = [
    "DingTalkApiError",
    "build_app_payload",
    "close_http_client",
    "send_message_app",
    "strip_target_prefix",
]

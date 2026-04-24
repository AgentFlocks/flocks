"""
DingTalk channel — Stream Mode inbound + OAPI app-robot outbound.

The :class:`DingTalkChannel` plugin connects to DingTalk's Stream Mode
WebSocket via the official ``dingtalk-stream`` SDK and routes outbound
messages through the enterprise app-robot OAPI.  It supersedes the
legacy Node.js connector that previously owned the ``dingtalk`` channel
id.

Public surface:

* :class:`DingTalkChannel` — the registered plugin entry point.
* :func:`send_message_app` — low-level OAPI sender, reusable from tools
  and hooks that need to push messages without going through the
  channel plugin (e.g. ``channel_message`` tool).
"""

from flocks.channel.builtin.dingtalk.channel import DingTalkChannel
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
    "DingTalkChannel",
    "build_app_payload",
    "close_http_client",
    "send_message_app",
    "strip_target_prefix",
]

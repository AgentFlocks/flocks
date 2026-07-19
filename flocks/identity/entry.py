from __future__ import annotations

from enum import Enum


class Entry(str, Enum):
    """Neutral labels for the transport that initiated an operation."""

    WEBUI = "webui"
    API = "api"
    CLI = "cli"
    TUI = "tui"
    CHANNEL = "channel"
    HEADLESS = "headless"
    ACP = "acp"
    UNKNOWN = "unknown"

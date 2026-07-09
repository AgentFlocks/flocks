from __future__ import annotations

from enum import Enum


class Entry(str, Enum):
    WEBUI = "webui"
    API = "api"
    CLI = "cli"
    TUI = "tui"
    CHANNEL = "channel"
    HEADLESS = "headless"
    ACP = "acp"
    UNKNOWN = "unknown"


DEFAULT_PERMISSION_MODE_BY_ENTRY: dict[Entry, str] = {
    Entry.WEBUI: "default_interactive",
    Entry.API: "default_interactive",
    Entry.CLI: "default_interactive",
    Entry.TUI: "default_interactive",
    Entry.CHANNEL: "readonly",
    Entry.HEADLESS: "headless_fail_closed",
    Entry.ACP: "default_interactive",
    Entry.UNKNOWN: "default_interactive",
}

"""Shared input-dispatch type helpers."""

from typing import Literal

from flocks.command.command import CommandSurface

InputSourceType = Literal[
    "webui",
    "tui",
    "cli",
    "acp",
    "channel",
    "feishu",
    "wecom",
    "telegram",
]


def surface_for_source(source_type: str) -> CommandSurface:
    """Map a transport/source type onto a command surface."""
    if source_type in {"feishu", "wecom", "telegram", "channel"}:
        return "channel"
    if source_type in {"webui", "tui", "cli", "acp"}:
        return source_type
    return "webui"

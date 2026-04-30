"""Handler for the bundled Hub echo tool."""

from __future__ import annotations


def echo(message: str) -> dict:
    return {
        "message": message,
        "source": "flocks-hub",
    }

"""Configurable tool-output truncation limits.

Mirrors hermes-agent's ``tools/tool_output_limits.py`` design: constants are
centralised here and overridable via ``flocks.json`` (``toolOutput`` section)
so users can tune them without patching source.

Example ``flocks.json``::

    {
      "toolOutput": {
        "readMaxLines": 5000,
        "readMaxBytes": 102400,
        "readMaxLineLength": 4000
      }
    }

The reader is defensive: any error (missing config, invalid value, …) falls
back to the built-in defaults so tools never fail because of a malformed
config entry.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Built-in defaults — match the previous hard-coded values in read.py so
# adding this module is behaviour-preserving for users who don't set
# ``toolOutput`` in flocks.json.
# ---------------------------------------------------------------------------

DEFAULT_READ_MAX_LINES: int = 2_000
DEFAULT_READ_MAX_BYTES: int = 50 * 1024   # 50 KB
DEFAULT_READ_MAX_LINE_LENGTH: int = 2_000


def _coerce_positive_int(value: Any, default: int) -> int:
    """Return ``value`` as a positive int, or ``default`` on any problem."""
    try:
        iv = int(value)
    except (TypeError, ValueError):
        return default
    return iv if iv > 0 else default


def _load_tool_output_section():
    """Return ``ConfigInfo.tool_output`` or ``None``, never raises.

    Tries two paths in order:
    1. ``Config._cached_config`` — already populated after server start-up,
       zero overhead for hot-path tool calls.
    2. Synchronous JSON parse of ``~/.flocks/flocks.json`` — used when the
       tool is called before the async ``Config.get()`` has run (e.g. CLI
       one-shot mode, unit tests).
    """
    try:
        from flocks.config.config import Config
        cached = Config._cached_config
        if cached is not None:
            return cached.tool_output
    except Exception:
        pass

    # Fallback: sync parse of flocks.json
    try:
        import json as _json
        from pathlib import Path as _Path
        config_dir = _Path.home() / ".flocks"
        for fname in ("flocks.json", "flocks.jsonc", "config.json"):
            fp = config_dir / fname
            if fp.exists():
                raw = _json.loads(fp.read_text(encoding="utf-8"))
                section = raw.get("toolOutput") or raw.get("tool_output")
                if isinstance(section, dict):
                    from flocks.config.config import ToolOutputConfig
                    return ToolOutputConfig.model_validate(section)
    except Exception:
        pass

    return None


def get_tool_output_limits() -> dict[str, int]:
    """Return resolved tool-output limits, reading ``toolOutput`` from flocks.json.

    Keys: ``read_max_lines``, ``read_max_bytes``, ``read_max_line_length``.
    Missing or invalid entries fall through to the ``DEFAULT_*`` constants.
    This function NEVER raises.
    """
    section = _load_tool_output_section()

    if section is None:
        return {
            "read_max_lines": DEFAULT_READ_MAX_LINES,
            "read_max_bytes": DEFAULT_READ_MAX_BYTES,
            "read_max_line_length": DEFAULT_READ_MAX_LINE_LENGTH,
        }

    return {
        "read_max_lines": _coerce_positive_int(
            section.read_max_lines, DEFAULT_READ_MAX_LINES
        ),
        "read_max_bytes": _coerce_positive_int(
            section.read_max_bytes, DEFAULT_READ_MAX_BYTES
        ),
        "read_max_line_length": _coerce_positive_int(
            section.read_max_line_length, DEFAULT_READ_MAX_LINE_LENGTH
        ),
    }


def get_read_max_lines() -> int:
    """Shortcut for read-tool callers that only need the line cap."""
    return get_tool_output_limits()["read_max_lines"]


def get_read_max_bytes() -> int:
    """Shortcut for read-tool callers that only need the byte cap."""
    return get_tool_output_limits()["read_max_bytes"]


def get_read_max_line_length() -> int:
    """Shortcut for read-tool callers that only need the per-line cap."""
    return get_tool_output_limits()["read_max_line_length"]

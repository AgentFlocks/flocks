"""Idempotent plugin registration entrypoint."""

from __future__ import annotations

from typing import Any

from flocks_code_security.projection import register_projection
from flocks_code_security.runtime import get_runtime
from flocks_code_security.tools import register_tools


def register(_loader: Any = None) -> None:
    get_runtime()
    register_tools()
    register_projection()

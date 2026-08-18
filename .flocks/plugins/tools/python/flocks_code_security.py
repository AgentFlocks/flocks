"""Filesystem-loader bridge for the project code-security package."""

from __future__ import annotations

import sys
from pathlib import Path


PACKAGE_SOURCE = Path(__file__).resolve().parents[2] / "flocks-code-security" / "src"
if str(PACKAGE_SOURCE) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SOURCE))

from flocks_code_security.entrypoint import register  # noqa: E402


register()

# The generic tool loader checks this attribute after importing the module.
# Tools are registered explicitly by register() so the declarative list is empty.
TOOLS: list[dict] = []

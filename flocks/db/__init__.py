"""Database abstraction: one dialect per backend, one place to choose one.

Stores build statements through a :class:`~flocks.db.dialect.Dialect` and read
the active backend from :mod:`flocks.db.registry`. Nothing else in the codebase
should mention a backend by name.
"""

from __future__ import annotations

from flocks.db.dialect import (
    Dialect,
    PostgresDialect,
    get_dialect,
)
from flocks.db.registry import (
    DatabaseConfigurationError,
    DatabaseSettings,
    configure,
    current,
    dialect,
    load_settings,
)

__all__ = [
    "DatabaseConfigurationError",
    "DatabaseSettings",
    "Dialect",
    "PostgresDialect",
    "configure",
    "current",
    "dialect",
    "get_dialect",
    "load_settings",
]

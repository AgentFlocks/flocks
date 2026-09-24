"""Removed runtime backend; use the explicit SQLite-to-PostgreSQL importer."""

from flocks.db.registry import DatabaseConfigurationError


def __getattr__(name):
    if name.startswith("SQLite") or name.startswith("Sqlite"):
        raise DatabaseConfigurationError("SQLite runtime support was removed; configure PostgreSQL")
    raise AttributeError(name)

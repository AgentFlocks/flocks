"""Which database this process talks to, decided once and read everywhere.

Configuration lives in the environment rather than a config file because the
backend has to be known before anything that reads configuration from the
database can start. These variables configure it:

    FLOCKS_DB_BACKEND   postgres (the only supported runtime backend)
    FLOCKS_DB_DSN       PostgreSQL connection string
    FLOCKS_DB_DSN_FILE  alternatively, a file containing the connection string

With neither connection variable set, the file named by a persisted switch to
PostgreSQL (``dsn_file`` in ``<config dir>/database.json``, see
``flocks.db_backend``) is read instead.

Legacy SQLite files are read only by the explicit offline migration command.
Missing or invalid PostgreSQL configuration must never create a local database.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlsplit

from flocks.db.dialect import Dialect, get_dialect
from flocks.db_backend import BACKEND_ENV, DSN_ENV, DSN_FILE_ENV, BackendConfigurationError, persisted_choice

DEFAULT_BACKEND = "postgres"


class DatabaseConfigurationError(Exception):
    """The requested backend cannot be used as configured."""


@dataclass(frozen=True)
class DatabaseSettings:
    """A resolved, validated choice of backend."""

    backend: str
    dsn: Optional[str] = None

    def __post_init__(self) -> None:
        if self.backend != "postgres" or not self.dsn:
            raise DatabaseConfigurationError(
                "Flocks requires PostgreSQL. Set FLOCKS_DB_DSN or FLOCKS_DB_DSN_FILE; "
                "use the offline migration command for legacy SQLite files."
            )

    @property
    def is_sqlite(self) -> bool:
        """Compatibility attribute for extensions; SQLite is never a runtime backend."""
        return False

    @property
    def dialect(self) -> Dialect:
        return get_dialect(self.backend)

    def describe(self) -> str:
        """Safe for logs: names the backend, never the credentials in the DSN."""
        if self.dsn is None:
            return self.backend
        return f"{self.backend} (dsn configured)"


def _normalise(backend: str) -> str:
    name = backend.strip().lower()
    return "postgres" if name == "postgresql" else name


def load_settings(environ: Optional[Mapping[str, str]] = None) -> DatabaseSettings:
    """Resolve settings from the environment, validating as we go."""
    source = os.environ if environ is None else environ
    backend = _normalise(source.get(BACKEND_ENV, "") or DEFAULT_BACKEND)
    dsn = (source.get(DSN_ENV) or "").strip() or None
    dsn_file = (source.get(DSN_FILE_ENV) or "").strip()
    dsn_file_source = DSN_FILE_ENV
    if backend != "postgres":
        raise DatabaseConfigurationError(
            "Only PostgreSQL is supported. Migrate legacy SQLite data before starting Flocks."
        )
    if dsn and dsn_file:
        raise DatabaseConfigurationError(f"Set only one of {DSN_ENV} and {DSN_FILE_ENV}")
    if not dsn and not dsn_file:
        try:
            persisted = persisted_choice(source)
        except BackendConfigurationError as exc:
            raise DatabaseConfigurationError(str(exc)) from None
        if persisted is not None and persisted.backend == "postgres" and persisted.dsn_file:
            dsn_file, dsn_file_source = persisted.dsn_file, f"the dsn_file named in {persisted.source}"
    if dsn_file:
        try:
            dsn = Path(dsn_file).expanduser().read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            raise DatabaseConfigurationError(f"Cannot read {dsn_file_source}") from None
    if not dsn:
        raise DatabaseConfigurationError(
            f"PostgreSQL requires {DSN_ENV} or {DSN_FILE_ENV} to be set"
        )
    try:
        scheme = urlsplit(dsn).scheme
    except ValueError:
        scheme = ""
    if scheme not in {"postgres", "postgresql"}:
        raise DatabaseConfigurationError(
            "The database connection string must use postgresql:// or postgres://"
        )
    return DatabaseSettings(backend=backend, dsn=dsn)


_settings: Optional[DatabaseSettings] = None


def configure(settings: Optional[DatabaseSettings] = None) -> DatabaseSettings:
    """Set the process-wide choice. Called once during startup."""
    global _settings
    _settings = settings if settings is not None else load_settings()
    return _settings


def current() -> DatabaseSettings:
    """The active settings, resolving from the environment on first use."""
    return _settings if _settings is not None else configure()


def dialect() -> Dialect:
    """Shorthand for the active backend's dialect."""
    return current().dialect


def reset() -> None:
    """Forget the resolved settings. For tests that change the environment."""
    global _settings
    _settings = None


__all__ = [
    "BACKEND_ENV",
    "DSN_ENV",
    "DSN_FILE_ENV",
    "DatabaseConfigurationError",
    "DatabaseSettings",
    "configure",
    "current",
    "dialect",
    "load_settings",
    "reset",
]

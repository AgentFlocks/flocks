"""
Flocks - Flocks Python Implementation

AI-Native SecOps Platform
"""

# The process's .env first (flocks/startup_env.py): what it sets decides, among
# other things, the database backend.  A backend other than SQLite can replace
# runtime modules, which has to be in place before the first of them is
# imported (flocks/db_backend.py).
from flocks import startup_env as _startup_env
from flocks import db_backend as _db_backend

_startup_env.load()
_db_backend.install()

from importlib.metadata import version, PackageNotFoundError

try:
    _from_metadata = version("flocks")
except PackageNotFoundError:
    _from_metadata = None
# Partial/corrupt installs can yield missing Version metadata (None); treat as unknown.
if not _from_metadata:
    # Not installed as a package (e.g. running directly from source tree),
    # or metadata is incomplete — read pyproject.toml in the project root.
    try:
        import tomllib
        from pathlib import Path

        _pyproject = Path(__file__).parent.parent / "pyproject.toml"
        with open(_pyproject, "rb") as _f:
            __version__ = tomllib.load(_f).get("project", {}).get("version") or "unknown"
    except Exception:
        __version__ = "unknown"
else:
    __version__ = _from_metadata

# Strip a leading "v" so callers always get a bare version string.
__version__ = str(__version__).lstrip("v")

__author__ = "Flocks Team"

from flocks.utils.log import Log
from flocks.config.config import Config

__all__ = ["Log", "Config", "__version__"]

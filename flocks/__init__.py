"""
Flocks - Flocks Python Implementation

AI-Native SecOps Platform
"""

import os as _os
from pathlib import Path as _Path

# Point tiktoken at a persistent cache dir so the cl100k_base encoding
# (downloaded from Azure Blob) survives OS tmp-dir cleanups.
# The install script pre-warms this cache; at runtime we just set the env var.
if "TIKTOKEN_CACHE_DIR" not in _os.environ:
    _tiktoken_cache = _Path.home() / ".flocks" / "data" / "tiktoken_cache"
    _tiktoken_cache.mkdir(parents=True, exist_ok=True)
    _os.environ["TIKTOKEN_CACHE_DIR"] = str(_tiktoken_cache)

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

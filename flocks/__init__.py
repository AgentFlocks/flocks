"""
Flocks - Flocks Python Implementation

AI-Native SecOps Platform
"""

import os as _os
import shutil as _shutil
from pathlib import Path as _Path

# Ensure tiktoken can find the cl100k_base encoding without network access.
# Strategy: bundled asset → user-level cache → original download.
_TIKTOKEN_CACHE_KEY = "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"

if "TIKTOKEN_CACHE_DIR" not in _os.environ:
    _tiktoken_cache = _Path.home() / ".flocks" / "data" / "tiktoken_cache"
    _tiktoken_cache.mkdir(parents=True, exist_ok=True)

    # Seed from the bundled asset if the user cache is missing.
    if not (_tiktoken_cache / _TIKTOKEN_CACHE_KEY).exists():
        _bundled = _Path(__file__).parent.parent / ".flocks" / "data" / "tiktoken" / _TIKTOKEN_CACHE_KEY
        if _bundled.exists():
            _shutil.copy2(_bundled, _tiktoken_cache / _TIKTOKEN_CACHE_KEY)

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

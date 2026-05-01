"""Flocks browser runtime package."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AGENT_WORKSPACE = PROJECT_ROOT / ".flocks" / "plugins" / "skills" / "browser-use" / "agent-workspace"
BROWSER_LABEL = "flocks browser"


def get_browser_version() -> str:
    """Return the installed Flocks version without a leading ``v``."""
    try:
        current = version("flocks")
    except PackageNotFoundError:
        current = None

    if not current:
        try:
            import tomllib

            with open(PROJECT_ROOT / "pyproject.toml", "rb") as file_obj:
                current = tomllib.load(file_obj).get("project", {}).get("version")
        except Exception:
            current = None

    return str(current or "unknown").lstrip("v")

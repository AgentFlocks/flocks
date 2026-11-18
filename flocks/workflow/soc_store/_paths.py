"""Where the SQLite files behind the SOC suite are, and the workflow packages its pages name.

``soc.db`` is the suite's own: the triage workflow writes alerts to it and the
SOC pages read them back.  The other three are the runtime's own databases,
read here for the dashboard's workflow, task and token figures.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from flocks.db_backend import data_dir

#: Another file to use instead of the installation's own; tests set these.
SOC_DB: Optional[Path] = None
WORKFLOW_DB: Optional[Path] = None
TASK_DB: Optional[Path] = None
USAGE_DB: Optional[Path] = None
WORKFLOW_ROOTS: Optional[list[Path]] = None


def historical_soc_db() -> Path:
    """Where the suite kept its alerts before it asked the runtime for a place."""
    return Path.home() / ".flocks" / "data" / "soc.db"


def soc_db() -> Path:
    """The alert store: in the data directory, or the historical file while only that one exists."""
    if SOC_DB is not None:
        return Path(SOC_DB)
    current = data_dir() / "soc.db"
    if not current.exists():
        historical = historical_soc_db()
        if historical.is_file():
            return historical
    return current


def workflow_db() -> Path:
    return Path(WORKFLOW_DB) if WORKFLOW_DB is not None else data_dir() / "workflow.db"


def task_db() -> Path:
    return Path(TASK_DB) if TASK_DB is not None else data_dir() / "tasks.db"


def usage_db() -> Path:
    return Path(USAGE_DB) if USAGE_DB is not None else data_dir() / "flocks.db"


def workflow_roots() -> list[Path]:
    """Where workflow packages are read for their names and step counts, the installation's own first.

    The installation's plugins and workspace, then the workflows the release
    bundles: a workflow that has run here but is not installed (any more) is
    still named by the release's manifest.
    """
    if WORKFLOW_ROOTS is not None:
        return [Path(root) for root in WORKFLOW_ROOTS]
    from flocks.config.config import get_user_plugins_root
    from flocks.hub.paths import get_bundled_hub_root
    from flocks.workspace.manager import _get_workspace_dir

    return [
        get_user_plugins_root() / "workflows",
        _get_workspace_dir() / "workflows",
        get_bundled_hub_root() / "plugins" / "workflows",
    ]

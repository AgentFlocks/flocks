"""
CLI Commands module

Exports all CLI command groups for registration in main.py
"""

from flocks.cli.commands.export import export_app
from flocks.cli.commands.import_ import import_app
from flocks.cli.commands.mcp import mcp_app
from flocks.cli.commands.browser import BROWSER_CONTEXT_SETTINGS, browser_command
from flocks.cli.commands.session import session_app
from flocks.cli.commands.skill import skill_app
from flocks.cli.commands.stats import stats_app
from flocks.cli.commands.task import task_app
from flocks.cli.commands.admin import admin_app

__all__ = [
    "session_app",
    "mcp_app",
    "browser_command",
    "BROWSER_CONTEXT_SETTINGS",
    "export_app",
    "import_app",
    "stats_app",
    "task_app",
    "skill_app",
    "admin_app",
]

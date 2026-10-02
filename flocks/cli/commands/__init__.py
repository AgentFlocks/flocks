"""
CLI Commands module

Exports all CLI command groups for registration in main.py
"""

from flocks.cli.commands.export import export_app
from flocks.cli.commands.import_ import import_app
from flocks.cli.commands.mcp import mcp_app
from flocks.cli.commands.browser import BROWSER_CONTEXT_SETTINGS, browser_command
from flocks.cli.commands.doctor import doctor_command
from flocks.cli.commands.session import session_app
from flocks.cli.commands.skill import skill_app
from flocks.cli.commands.stats import stats_app
from flocks.cli.commands.task import task_app
from flocks.cli.commands.admin import admin_app

from flocks.cli.commands.agent import agent_app
from flocks.cli.commands.exec import exec_command
from flocks.cli.commands.workflow import workflow_app
from flocks.cli.commands.device import device_app
from flocks.cli.commands.model import model_app
from flocks.cli.commands.plugin import plugin_app

__all__ = [
    "agent_app",
    "exec_command",
    "workflow_app",
    "device_app",
    "model_app",
    "plugin_app",
    "session_app",
    "mcp_app",
    "browser_command",
    "BROWSER_CONTEXT_SETTINGS",
    "doctor_command",
    "export_app",
    "import_app",
    "stats_app",
    "task_app",
    "skill_app",
    "admin_app",
]

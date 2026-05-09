"""Syslog ingestion for workflow triggers (UDP/TCP listeners)."""

from flocks.syslog.constants import WORKFLOW_SYSLOG_CONFIG_PREFIX
from flocks.syslog.manager import SyslogManager, default_manager

__all__ = ["SyslogManager", "default_manager", "WORKFLOW_SYSLOG_CONFIG_PREFIX"]

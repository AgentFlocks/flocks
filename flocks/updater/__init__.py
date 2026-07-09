"""
Flocks Updater

Provides self-update capability via GitHub releases.
Downloads source archives, backs up the current installation,
and replaces source files — no git binary required at runtime.
"""

from flocks.updater.deploy import DeployMode, detect_deploy_mode
from flocks.updater.models import VersionInfo, UpdateProgress, UpdateStage
from flocks.updater.updater import (
    build_updated_frontend,
    check_update,
    get_current_version,
    get_latest_release,
    perform_pro_bundle_downgrade,
    perform_update,
    perform_pro_bundle_install,
)

__all__ = [
    "DeployMode",
    "detect_deploy_mode",
    "VersionInfo",
    "UpdateProgress",
    "UpdateStage",
    "build_updated_frontend",
    "check_update",
    "get_current_version",
    "get_latest_release",
    "perform_update",
    "perform_pro_bundle_install",
    "perform_pro_bundle_downgrade",
]

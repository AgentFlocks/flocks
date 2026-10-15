"""
Flocks Updater

Provides self-update capability via GitHub releases.
Downloads source archives and delegates source replacement to a detached
handoff — no git binary required at runtime.  An installed extension can add
an update channel of its own (flocks.updater.channels).
"""

from flocks.updater.channels import (
    ChannelRelease,
    InstallPlan,
    UpdateChannel,
    get_update_channel,
    register_update_channel,
    update_channel_names,
)
from flocks.updater.deploy import DeployMode, detect_deploy_mode
from flocks.updater.models import VersionInfo, UpdateProgress, UpdateStage
from flocks.updater.updater import (
    build_updated_frontend,
    check_update,
    get_current_version,
    get_latest_release,
    install_or_repair_source,
    perform_update,
    remove_components,
)

__all__ = [
    "ChannelRelease",
    "DeployMode",
    "InstallPlan",
    "UpdateChannel",
    "detect_deploy_mode",
    "VersionInfo",
    "UpdateProgress",
    "UpdateStage",
    "build_updated_frontend",
    "check_update",
    "get_current_version",
    "get_latest_release",
    "get_update_channel",
    "install_or_repair_source",
    "perform_update",
    "register_update_channel",
    "remove_components",
    "update_channel_names",
]

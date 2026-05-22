"""
Updater data models
"""

from typing import Literal
from pydantic import BaseModel

from flocks.updater.deploy import DeployMode

UpdateStage = Literal[
    "fetching",
    "backing_up",
    "applying",
    "syncing",
    "restarting",
    "done",
    "error",
]


class VersionInfo(BaseModel):
    current_version: str
    latest_version: str | None = None
    has_update: bool = False
    release_notes: str | None = None
    release_url: str | None = None
    zipball_url: str | None = None
    tarball_url: str | None = None
    bundle_sha256: str | None = None
    bundle_format: Literal["zip", "tar.gz"] | None = None
    error: str | None = None
    deploy_mode: DeployMode = "source"
    update_allowed: bool = True


class UpdateProgress(BaseModel):
    stage: UpdateStage
    message: str
    success: bool | None = None

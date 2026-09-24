"""
Deployment mode detection.

Determines whether the current process is running inside a Docker container,
as part of a *managed* release package (an external manager owns upgrades),
from an *offline* installer package (no network upgrades), or from a plain
source (host) installation so that update behaviour can adapt accordingly.
"""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

DeployMode = Literal["docker", "source", "managed", "offline"]

#: Neutral deployment descriptor a package manager/supervisor may drop into the
#: runtime root to declare how this installation is maintained.
DEPLOYMENT_DESCRIPTOR = Path("config") / "deployment.json"
_VALID_MODES = ("docker", "source", "managed", "offline")

DOCKER_UPGRADE_REFUSAL = (
    "In-place upgrade is not supported in Docker deployments. "
    "Please pull the latest image and restart the container."
)
MANAGED_UPGRADE_REFUSAL = (
    "In-place upgrade is disabled for managed release packages: this installation is "
    "maintained by an external manager that owns package upgrades "
    "(受管发布包由核心升级，内置更新器不会修改受管的运行环境)."
)
OFFLINE_UPGRADE_REFUSAL = (
    "此实例由离线安装包部署，不支持联网就地升级：核心升级请用新版 flocks-offline.run 覆盖安装，"
    "Pro 组件从包内 /opt/flocks/bundle 安装（FLOCKS_PRO_BUNDLE_DIR）。"
)


def _runtime_root() -> Path:
    override = (os.environ.get("FLOCKS_ROOT") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".flocks"


def _descriptor_mode() -> DeployMode | None:
    """Mode declared by ``<FLOCKS_ROOT>/config/deployment.json`` (``{"mode": "managed"}``)."""
    path = _runtime_root() / DEPLOYMENT_DESCRIPTOR
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    mode = payload.get("mode") if isinstance(payload, dict) else None
    if isinstance(mode, str) and mode.strip().lower() in _VALID_MODES:
        return mode.strip().lower()  # type: ignore[return-value]
    return None


def _license_enforcement_extension_active() -> bool:
    """A distribution that gates features by license ships its own upgrade channel.

    Evaluated live (not cached): extensions register during startup, after this
    module may already have been imported.
    """
    try:
        from flocks.license import AlwaysOkLicenseChecker, get_checker
    except Exception:  # pragma: no cover - defensive
        return False
    return get_checker() is not AlwaysOkLicenseChecker


@lru_cache(maxsize=1)
def _static_deploy_mode() -> DeployMode | None:
    """Environment/filesystem signals that do not change while the process runs."""
    env_val = os.environ.get("FLOCKS_DEPLOY_MODE", "").strip().lower()
    if env_val in _VALID_MODES:
        return env_val  # type: ignore[return-value]

    # The offline installer sets this in the service environment; like
    # FLOCKS_DEPLOY_MODE it is an explicit statement by whoever launched the
    # process, so it outranks a descriptor file left in the runtime root.
    if os.environ.get("FLOCKS_OFFLINE_INSTALL", "").strip().lower() in ("1", "true", "yes", "on"):
        return "offline"

    declared = _descriptor_mode()
    if declared is not None:
        return declared

    if Path("/.dockerenv").exists():
        return "docker"

    try:
        cgroup_text = Path("/proc/1/cgroup").read_text(encoding="utf-8")
        if "docker" in cgroup_text or "containerd" in cgroup_text:
            return "docker"
    except Exception:
        pass

    return None


def detect_deploy_mode() -> DeployMode:
    """
    Return ``"managed"`` when an external manager owns this installation's
    upgrades, ``"offline"`` for an offline-package installation (no network
    upgrades), ``"docker"`` when the process is running inside a container,
    ``"source"`` otherwise.

    Detection priority:
      1. Explicit ``FLOCKS_DEPLOY_MODE`` environment variable (always wins).
      2. ``FLOCKS_OFFLINE_INSTALL`` set by the offline installer.
      3. ``<FLOCKS_ROOT>/config/deployment.json`` declaring ``{"mode": ...}``.
      4. Presence of ``/.dockerenv`` or a docker/containerd cgroup.
      5. A license-enforcement extension replaced the OSS default in this
         process (managed distribution; evaluated live).

    ``offline`` behaves like ``source`` everywhere except that network-based
    upgrades are refused: the Pro component comes from the bundled local bundle
    and core upgrades come from a newer ``flocks-offline.run``.
    """
    static = _static_deploy_mode()
    if static is not None:
        return static
    if _license_enforcement_extension_active():
        return "managed"
    return "source"


# Keep the historical ``detect_deploy_mode.cache_clear()`` hook for tests.
detect_deploy_mode.cache_clear = _static_deploy_mode.cache_clear  # type: ignore[attr-defined]


def is_managed_deployment() -> bool:
    return detect_deploy_mode() == "managed"


def update_allowed_for_mode(mode: str) -> bool:
    """Whether the built-in updater may fetch and apply a release in *mode*.

    ``offline`` still installs the Pro bundle shipped with the package (see
    ``perform_update(local_bundle_path=...)``); only network-based upgrades are
    refused, which is what this flag reports.
    """
    return mode not in ("docker", "managed", "offline")


def install_modification_refusal(mode: str | None = None) -> str | None:
    """Reason the built-in updater must not touch this installation's tree, else ``None``.

    Docker images are replaced by pulling a new image and managed release
    packages are upgraded by the commercial core; ``uv sync`` / source
    replacement from inside the runtime would corrupt either. An offline
    installation is *not* in this set: its Pro bundle is installed from disk by
    the same code paths, only network-based upgrades are refused
    (``update_refusal_reason``).
    """
    current = mode or detect_deploy_mode()
    if current == "docker":
        return DOCKER_UPGRADE_REFUSAL
    if current == "managed":
        return MANAGED_UPGRADE_REFUSAL
    return None


def update_refusal_reason(mode: str | None = None) -> str | None:
    """Return a user-facing reason when network in-place upgrades are refused, else ``None``."""
    current = mode or detect_deploy_mode()
    if current == "offline":
        return OFFLINE_UPGRADE_REFUSAL
    return install_modification_refusal(current)

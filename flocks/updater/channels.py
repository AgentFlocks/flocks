"""
Update channels an installed extension adds next to the built-in release check.

The built-in updater knows one channel: this project's own releases (GitHub,
Gitee, GitLab).  An extension that ships its releases some other way registers
an :class:`UpdateChannel`; the update routes and ``flocks update --channel``
then check and install through it.  The channel decides where a release comes
from and what in it gets installed; the updater keeps doing the parts every
release shares -- download and verify, back up, replace the source, sync
dependencies, restart -- so an extension never touches the installation
directly.

Channels are found two ways: registered in-process (:func:`register_update_channel`,
from a plugin's bootstrap) or declared under the ``flocks.update_channels``
entry-point group -- a zero-argument factory returning the channel -- which the
CLI loads without starting the server.
"""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Protocol, runtime_checkable

from flocks.updater.models import VersionInfo
from flocks.utils.log import Log

log = Log.create(service="updater.channels")

ENTRY_POINT_GROUP = "flocks.update_channels"


@dataclass(frozen=True)
class InstallPlan:
    """What to install from an extracted release; the channel that offered it decides."""

    #: Source tree that replaces the installation; ``None`` keeps the local source.
    source_root: Path | None
    #: Version recorded once the install succeeded.
    version: str
    #: Wheels installed into the runtime (``--no-deps``) after the dependency sync.
    extra_wheels: tuple[Path, ...] = ()
    #: The release ships its built WebUI; dependencies come from
    #: ``dependency_wheels_dir`` (offline) or are left alone.
    prebuilt: bool = False
    dependency_wheels_dir: Path | None = None
    #: ``(staged file, destination)`` pairs put in place after a successful install.
    success_files: tuple[tuple[Path, Path], ...] = ()


@dataclass(frozen=True)
class ChannelRelease:
    """One release a channel offers, in the terms the updater installs."""

    version: str
    #: ``plan(content_root, current_version, work_dir)``: what to install from the
    #: extracted release.  Raise ``ValueError`` to refuse it.
    plan: Callable[[Path, str, Path], InstallPlan]
    archive_url: str | None = None
    #: A local directory or archive instead of a download (offline installations).
    archive_path: Path | None = None
    #: ``"zip"`` or ``"tar.gz"``; taken from the URL when unset.
    archive_format: str | None = None
    #: SHA-256 of the archive.  A local directory has no archive: its plan checks it.
    sha256: str | None = None
    #: Sent only to the origin of ``archive_url``, never along a redirect elsewhere.
    download_headers: Mapping[str, str] = field(default_factory=dict)
    download_name: str | None = None
    #: How progress messages name what is being installed.
    label: str = "release"
    #: The channel's own version rules apply (e.g. an older release keeps the
    #: newer local source), so the plain "no downgrades" guard does not.
    own_version_rules: bool = False
    release_notes: str | None = None
    release_url: str | None = None


@runtime_checkable
class UpdateChannel(Protocol):
    """A source of releases besides the built-in one."""

    name: str

    async def check(self, *, locale: str | None = None, remote: bool = True) -> VersionInfo:
        """Compare what is installed with what this channel offers now.

        With ``remote`` false (the updater is switched off) report only what is
        installed, without any network round trip.
        """

    async def release(self, *, version: str | None = None) -> ChannelRelease:
        """The release to install; ``version`` is the one the caller was offered."""


@runtime_checkable
class InstalledReleaseChannel(Protocol):
    """A channel that can also say what it installed here (optional).

    The "what's new" notice (``GET /api/update/current-release``) asks such a
    channel first: a release it installed would otherwise be announced with
    the notes of this project's own release of the same core.
    """

    name: str

    def installed_release_key(self) -> str | None:
        """Names what this channel installed here and changes whenever that does
        (the answer is cached under it); ``None`` when it installed nothing."""

    async def installed_release(self, *, remote: bool = True) -> VersionInfo:
        """The installed release and its notes.

        With ``remote`` false (the updater is switched off) only what is
        recorded locally, without any network round trip.
        """


_channels: dict[str, UpdateChannel] = {}
_entry_points_loaded = False


def register_update_channel(channel: UpdateChannel) -> None:
    """Make *channel* available under ``channel.name`` (the last registration wins)."""
    name = str(getattr(channel, "name", "") or "").strip()
    if not name:
        raise ValueError("an update channel needs a name")
    _channels[name] = channel


def unregister_update_channel(name: str) -> None:
    _channels.pop(name, None)


def _load_entry_points() -> None:
    global _entry_points_loaded
    if _entry_points_loaded:
        return
    _entry_points_loaded = True
    try:
        entries = importlib.metadata.entry_points().select(group=ENTRY_POINT_GROUP)
    except Exception as exc:  # broken metadata must not take the updater down
        log.warning("updater.channels.entry_points_unreadable", {"error": str(exc)})
        return
    for entry in entries:
        if entry.name in _channels:
            continue
        try:
            # The entry point names a zero-argument factory returning the channel.
            register_update_channel(entry.load()())
        except Exception as exc:
            log.warning("updater.channels.entry_point_failed", {"name": entry.name, "error": str(exc)})


def get_update_channel(name: str | None) -> UpdateChannel | None:
    """The channel registered as *name*, or ``None`` (also for the built-in one)."""
    key = str(name or "").strip()
    if not key:
        return None
    if key not in _channels:
        _load_entry_points()
    return _channels.get(key)


def update_channel_names() -> list[str]:
    _load_entry_points()
    return sorted(_channels)


def installed_release_channels() -> list[InstalledReleaseChannel]:
    """The registered channels that can say what they installed here, by name."""
    return [
        channel
        for name in update_channel_names()
        if isinstance(channel := _channels.get(name), InstalledReleaseChannel)
    ]


# -- helpers a channel needs to speak the updater's version language --------


def parse_version(version: str) -> tuple[int, ...]:
    """Numeric tuple the updater orders versions by (``v2026.9.1`` -> ``(2026, 9, 1)``)."""
    from flocks.updater.updater import _parse_version

    return _parse_version(version)


def version_label(version: str | None) -> str:
    """``2026.9.1`` -> ``v2026.9.1``; empty stays empty."""
    from flocks.updater.updater import _version_label

    return _version_label(version)


def is_newer_version(latest: str | None, current: str | None) -> bool:
    from flocks.updater.updater import _is_newer_version

    return _is_newer_version(latest, current)


def archive_format_for_url(url: str, declared: str | None = None) -> str:
    """``zip`` or ``tar.gz``: the declared format when valid, else the URL's suffix."""
    from flocks.updater.updater import _archive_format_for_url

    return _archive_format_for_url(url, declared)


def runtime_root() -> Path:
    """The runtime root (``FLOCKS_ROOT``, ``~/.flocks`` by default) the updater writes under."""
    from flocks.updater.updater import _flocks_root

    return _flocks_root()


def reset_update_channels_for_testing() -> None:
    global _entry_points_loaded
    _channels.clear()
    _entry_points_loaded = False


__all__ = [
    "ENTRY_POINT_GROUP",
    "ChannelRelease",
    "InstallPlan",
    "InstalledReleaseChannel",
    "UpdateChannel",
    "archive_format_for_url",
    "get_update_channel",
    "installed_release_channels",
    "is_newer_version",
    "parse_version",
    "register_update_channel",
    "reset_update_channels_for_testing",
    "runtime_root",
    "unregister_update_channel",
    "update_channel_names",
    "version_label",
]

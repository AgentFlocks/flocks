"""``perform_update`` must not silently roll an installation back to an older release.

Regression for the 2026-09 full test pass: ``POST /api/update/apply?target_version=<old tag>``
skipped ``check_update`` and applied whatever tag was given, so a typo or a stale WebUI could
downgrade the source tree (and its dependencies) with no confirmation. Older targets are now
refused unless ``allow_downgrade=True`` is passed explicitly; equal versions stay allowed so
``flocks update --force`` can reinstall the current release.
"""

from __future__ import annotations

import pytest

from flocks.updater import updater
from flocks.updater.models import UpdateProgress


@pytest.fixture(autouse=True)
def _installed_version(monkeypatch):
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.8.17")

    async def _no_config():
        raise AssertionError("config/network must not be touched for a refused downgrade")

    monkeypatch.setattr(updater, "_get_updater_config", _no_config)


@pytest.mark.parametrize("target", ["v2026.8.10", "2026.8.16", "v2025.12.31"])
async def test_older_target_is_refused_before_any_network_access(target: str) -> None:
    events: list[UpdateProgress] = [p async for p in updater.perform_update(target)]

    assert [e.stage for e in events] == ["error"]
    assert events[0].success is False
    assert "older than the installed v2026.8.17" in events[0].message
    assert "downgrades are not applied" in events[0].message


@pytest.mark.parametrize("target", ["v2026.8.17", "2026.8.17", "v2026.9.1"])
def test_equal_or_newer_targets_pass_the_guard(target: str) -> None:
    assert updater._downgrade_refusal(target) is None


def test_allow_downgrade_bypasses_the_guard() -> None:
    assert updater._downgrade_refusal("v2026.8.10", allow_downgrade=True) is None


def test_unknown_current_version_does_not_block() -> None:
    assert updater._downgrade_refusal("v2026.8.10") is not None
    updater.get_current_version = lambda: ""
    assert updater._downgrade_refusal("v2026.8.10") is None


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v2026.10.16", (2026, 10, 16)),
        ("2026.10.16", (2026, 10, 16)),
        ("refs/tags/v2026.10.16", (2026, 10, 16)),
        # A channel's own tags: an edition name in front, with or without the "v".
        ("acme-v2026.10.16", (2026, 10, 16)),
        ("team-2026.10.17", (2026, 10, 17)),
        # Only letters make a prefix: a date written with dashes stays a date.
        ("v2026-10-18", (2026, 10, 18)),
    ],
)
def test_versions_compare_whatever_edition_tagged_them(tag, expected):
    from flocks.updater.updater import _is_newer_version, _parse_version

    assert _parse_version(tag) == expected
    assert _is_newer_version("acme-v2026.10.17", "v2026.10.16")
    assert not _is_newer_version("acme-v2026.10.16", "2026.10.16")

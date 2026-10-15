"""The configured sources the built-in updater reads, and nothing an extension ships."""

import pytest

from flocks.updater.updater import _release_sources


def test_builtin_sources_keep_their_configured_order():
    assert _release_sources(["gitee", "github", "gitlab"]) == ["gitee", "github", "gitlab"]


@pytest.mark.parametrize(
    "configured, expected",
    [
        (["console-manifest"], []),
        (["github", "console-manifest", "gitee"], ["github", "gitee"]),
        (["github", "some-extension-source"], ["github"]),
    ],
)
def test_sources_the_updater_cannot_read_itself_are_ignored(configured, expected):
    # An extension's releases come through its update channel, never through this list.
    assert _release_sources(configured) == expected


@pytest.mark.asyncio
async def test_installed_extension_state_does_not_change_the_sources(monkeypatch, tmp_path):
    from flocks.storage.storage import Storage

    marker = tmp_path / "run" / "pro-bundle-installed.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"bundle_version": "v2026.5.23", "core_version": "v2026.5.23"}', encoding="utf-8")
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    await Storage.set("console:session", {"console_session_token": "token_abc"}, "json")

    assert _release_sources(["github", "gitee"]) == ["github", "gitee"]

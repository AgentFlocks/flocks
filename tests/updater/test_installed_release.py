"""The notice must use the installed Core tag's published notes."""

import httpx
import pytest

from flocks.config.config import UpdaterConfig
from flocks.updater import updater


@pytest.mark.asyncio
async def test_installed_release_uses_exact_tag_and_full_body(monkeypatch):
    body = "### 工作台与场景\n\n* feat(webui): 保留状态 by @jamon-bodhi in #749"
    seen_urls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            seen_urls.append((url, kwargs))
            return httpx.Response(200, json={
                "tag_name": "v2026.9.23",
                "body": body,
                "html_url": "https://github.com/AgentFlocks/flocks/releases/tag/v2026.9.23",
            }, request=httpx.Request("GET", url))

    async def config():
        return UpdaterConfig(sources=["github", "gitee"])

    monkeypatch.setattr(updater, "_get_updater_config", config)
    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr("flocks.updater.deploy.detect_deploy_mode", lambda: "source")

    result = await updater.get_installed_release("2026.9.23")

    assert result.current_version == result.latest_version == "2026.9.23"
    assert result.release_notes == body
    assert result.release_url == "https://github.com/AgentFlocks/flocks/releases/tag/v2026.9.23"
    assert len(seen_urls) == 2
    assert seen_urls[0][0].endswith("/releases/tags/v2026.9.23")


@pytest.mark.asyncio
async def test_installed_release_ignores_mismatched_mirror_and_uses_matching_fallback(monkeypatch):
    requested = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **_kwargs):
            requested.append(url)
            tag = "v2026.9.14" if "api.github.com" in url else "v2026.9.23"
            return httpx.Response(200, json={
                "tag_name": tag,
                "body": f"notes for {tag}",
                "html_url": f"https://example.com/{tag}",
            }, request=httpx.Request("GET", url))

    async def config():
        return UpdaterConfig(sources=["github", "gitee"])

    monkeypatch.setattr(updater, "_get_updater_config", config)
    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr("flocks.updater.deploy.detect_deploy_mode", lambda: "source")

    result = await updater.get_installed_release("v2026.9.23")

    assert result.release_notes == "notes for v2026.9.23"
    assert len(requested) == 2
    assert all(url.endswith("/releases/tags/v2026.9.23") for url in requested)


@pytest.mark.asyncio
async def test_installed_release_fails_closed_when_sources_have_no_matching_notes(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **_kwargs):
            return httpx.Response(200, json={
                "tag_name": "v2026.9.14",
                "body": "outdated notes",
            }, request=httpx.Request("GET", url))

    async def config():
        return UpdaterConfig(sources=["github"])

    monkeypatch.setattr(updater, "_get_updater_config", config)
    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr("flocks.updater.deploy.detect_deploy_mode", lambda: "source")

    result = await updater.get_installed_release("2026.9.23")

    assert result.release_notes is None
    assert result.latest_version == "2026.9.23"


@pytest.mark.asyncio
async def test_installed_release_returns_no_notes_when_all_sources_fail(monkeypatch):
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **_kwargs):
            calls.append(url)
            return httpx.Response(503, request=httpx.Request("GET", url))

    async def config():
        return UpdaterConfig(sources=["github", "gitee"])

    monkeypatch.setattr(updater, "_get_updater_config", config)
    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr("flocks.updater.deploy.detect_deploy_mode", lambda: "source")

    result = await updater.get_installed_release("2026.9.23")

    assert result.release_notes is None
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_installed_release_skips_unknown_version_without_network(monkeypatch):
    def should_not_call(*_args, **_kwargs):
        raise AssertionError("unknown versions must not reach release APIs")

    monkeypatch.setattr(updater, "_get_updater_config", should_not_call)

    result = await updater.get_installed_release("")

    assert result.release_notes is None
    assert result.latest_version is None


class _InstalledChannel:
    name = "bundle"

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.remote_seen: list[bool] = []

    def installed_release_key(self):
        return "installed"

    async def installed_release(self, *, remote=True):
        from flocks.updater.models import VersionInfo

        self.remote_seen.append(remote)
        if self.fail:
            raise RuntimeError("console unreachable")
        return VersionInfo(current_version="v2026.10.16", latest_version="v2026.10.16", release_notes="bundle notes")


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_channel_installed_release_is_named_after_its_channel_and_follows_the_switch(monkeypatch, enabled):
    async def config():
        return UpdaterConfig(enabled=enabled)

    monkeypatch.setattr(updater, "_get_updater_config", config)
    channel = _InstalledChannel()

    result = await updater.get_channel_installed_release(channel)

    assert channel.remote_seen == [enabled]
    assert result.channel == "bundle"
    assert result.release_notes == "bundle notes"


@pytest.mark.asyncio
async def test_channel_installed_release_failure_reports_the_channel_without_notes(monkeypatch):
    async def config():
        return UpdaterConfig()

    monkeypatch.setattr(updater, "_get_updater_config", config)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.10.15")

    result = await updater.get_channel_installed_release(_InstalledChannel(fail=True))

    assert result.channel == "bundle"
    assert result.current_version == "2026.10.15"
    assert result.release_notes is None

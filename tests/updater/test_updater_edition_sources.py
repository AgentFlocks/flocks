import pytest

from flocks.updater.updater import _resolve_sources_for_edition


@pytest.mark.asyncio
async def test_flockspro_env_adds_cloud_manifest(monkeypatch):
    monkeypatch.setenv("FLOCKS_EDITION", "flockspro")
    sources = await _resolve_sources_for_edition(["github", "gitee"])
    assert sources == ["cloud-manifest"]


@pytest.mark.asyncio
async def test_cloud_session_does_not_change_oss_sources(monkeypatch):
    from flocks.storage.storage import Storage

    monkeypatch.delenv("FLOCKS_EDITION", raising=False)
    await Storage.set("cloud:session", {"cloud_session_token": "token_abc"}, "json")

    sources = await _resolve_sources_for_edition(["github", "gitee"])
    assert sources == ["github", "gitee"]

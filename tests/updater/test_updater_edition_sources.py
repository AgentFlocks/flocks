import pytest

from flocks.updater.updater import _resolve_sources_for_edition


@pytest.mark.asyncio
async def test_flockspro_env_with_active_license_uses_console_manifest(monkeypatch):
    monkeypatch.setenv("FLOCKS_EDITION", "flockspro")
    monkeypatch.setattr("flocks.updater.updater._is_flockspro_license_active", lambda: True)
    sources = await _resolve_sources_for_edition(["github", "gitee"])
    assert sources == ["console-manifest"]


@pytest.mark.asyncio
async def test_flockspro_env_without_active_license_keeps_oss_sources(monkeypatch):
    monkeypatch.setenv("FLOCKS_EDITION", "flockspro")
    monkeypatch.setattr("flocks.updater.updater._is_flockspro_license_active", lambda: False)
    sources = await _resolve_sources_for_edition(["github", "gitee"])
    assert sources == ["github", "gitee"]


@pytest.mark.asyncio
async def test_console_session_does_not_change_oss_sources(monkeypatch):
    from flocks.storage.storage import Storage

    monkeypatch.delenv("FLOCKS_EDITION", raising=False)
    await Storage.set("console:session", {"console_session_token": "token_abc"}, "json")

    sources = await _resolve_sources_for_edition(["github", "gitee"])
    assert sources == ["github", "gitee"]

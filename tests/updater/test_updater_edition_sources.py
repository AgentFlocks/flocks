import pytest

from flocks.updater.updater import _resolve_sources_for_edition


@pytest.mark.asyncio
async def test_flockspro_env_adds_cloud_manifest(monkeypatch):
    monkeypatch.setenv("FLOCKS_EDITION", "flockspro")
    sources = await _resolve_sources_for_edition(["github", "gitee"])
    assert sources[0] == "cloud-manifest"
    assert "github" in sources

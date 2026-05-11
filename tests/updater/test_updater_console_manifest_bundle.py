from __future__ import annotations

import pytest

from flocks.updater import updater


@pytest.mark.asyncio
async def test_fetch_console_manifest_release_uses_bundle_url(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "display_version": "v2026.5.10",
                "compare_version": "2026.5.10",
                "bundle_url": "https://cdn.example.com/flockspro-bundle-v2026.5.10.tar.gz",
                "bundle_sha256": "abc123",
                "oss_version": "v2026.5.10",
                "flockspro_component_version": "pro-v2026-5-10",
                "release_notes": "bundle release",
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, follow_redirects=True):
            assert "channel=flockspro" in url
            return _Resp()

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda timeout=15: _Client())
    result = await updater._fetch_console_manifest_release()
    assert result == (
        "2026.5.10",
        "bundle release",
        "https://cdn.example.com/flockspro-bundle-v2026.5.10.tar.gz",
        None,
        "https://cdn.example.com/flockspro-bundle-v2026.5.10.tar.gz",
    )
    info = await updater._fetch_console_manifest_release_info()
    assert info.bundle_sha256 == "abc123"
    assert info.bundle_format == "tar.gz"


@pytest.mark.asyncio
async def test_fetch_console_manifest_release_blocks_frozen_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "display_version": "v2026.5.10",
                "bundle_url": "https://cdn.example.com/flockspro-bundle-v2026.5.10.tar.gz",
                "frozen": True,
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, follow_redirects=True):
            return _Resp()

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda timeout=15: _Client())
    with pytest.raises(ValueError, match="frozen"):
        await updater._fetch_console_manifest_release()


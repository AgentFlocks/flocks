import pytest

from flocks.updater import updater
from flocks.updater.updater import _resolve_sources_for_edition


@pytest.mark.asyncio
async def test_installed_pro_bundle_marker_without_active_license_keeps_oss_sources(monkeypatch, tmp_path):
    marker = tmp_path / "run" / "pro-bundle-installed.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        """{
  "installed_version": "v2026.5.23",
  "flockspro_component_version": "pro-v2026-05-23"
}""",
        encoding="utf-8",
    )
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.delenv("FLOCKS_EDITION", raising=False)
    sources = await _resolve_sources_for_edition(["github", "gitee"])
    assert sources == ["github", "gitee"]


@pytest.mark.asyncio
async def test_flockspro_env_with_active_license_keeps_oss_sources(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv("FLOCKS_EDITION", "flockspro")
    monkeypatch.setattr("flocks.updater.updater._is_flockspro_license_active", lambda: True)
    sources = await _resolve_sources_for_edition(["github", "gitee"])
    assert sources == ["github", "gitee"]


@pytest.mark.asyncio
async def test_flockspro_env_without_active_license_keeps_oss_sources(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv("FLOCKS_EDITION", "flockspro")
    monkeypatch.setattr("flocks.updater.updater._is_flockspro_license_active", lambda: False)
    sources = await _resolve_sources_for_edition(["github", "gitee"])
    assert sources == ["github", "gitee"]


@pytest.mark.asyncio
async def test_console_session_does_not_change_oss_sources(monkeypatch, tmp_path):
    from flocks.storage.storage import Storage

    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.delenv("FLOCKS_EDITION", raising=False)
    await Storage.set("console:session", {"console_session_token": "token_abc"}, "json")

    sources = await _resolve_sources_for_edition(["github", "gitee"])
    assert sources == ["github", "gitee"]


def test_flockspro_license_active_uses_runtime_capability(monkeypatch):
    monkeypatch.setattr(updater.importlib.util, "find_spec", lambda name: object() if name == "flockspro" else None)

    import types
    import sys

    runtime_module = types.ModuleType("flockspro.license.runtime")
    runtime_module.is_pro_feature_enabled = lambda: True
    license_module = types.ModuleType("flockspro.license")
    flockspro_module = types.ModuleType("flockspro")
    monkeypatch.setitem(sys.modules, "flockspro", flockspro_module)
    monkeypatch.setitem(sys.modules, "flockspro.license", license_module)
    monkeypatch.setitem(sys.modules, "flockspro.license.runtime", runtime_module)

    assert updater._is_flockspro_license_active() is True

from __future__ import annotations

import zipfile
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from flocks.updater import updater


@pytest.mark.asyncio
async def test_fetch_console_manifest_release_uses_bundle_url(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from flocks.storage.storage import Storage

    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    license_path = tmp_path / "flockspro" / "license.json"
    license_path.parent.mkdir(parents=True)
    license_path.write_text('{"license_id": "lic_manifest"}', encoding="utf-8")
    await Storage.set("console:session", {"console_session_token": "cs_manifest"}, "json")

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "bundle_version": "v2026.5.10",
                "compare_version": "2026.5.10",
                "bundle_url": "https://cdn.example.com/flockspro-bundle-v2026.5.10.tar.gz",
                "bundle_sha256": "abc123",
                "core_version": "v2026.5.10",
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
            assert "license_id=lic_manifest" in url
            assert headers == {
                "x-license-id": "lic_manifest",
                "Authorization": "Bearer cs_manifest",
            }
            return _Resp()

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda timeout=15: _Client())
    result = await updater._fetch_console_manifest_release()
    assert result == (
        "v2026.5.10",
        "bundle release",
        "https://cdn.example.com/flockspro-bundle-v2026.5.10.tar.gz",
        None,
        "https://cdn.example.com/flockspro-bundle-v2026.5.10.tar.gz",
    )
    info = await updater._fetch_console_manifest_release_info()
    assert info.bundle_sha256 == "abc123"
    assert info.bundle_format == "tar.gz"


@pytest.mark.asyncio
async def test_check_update_uses_pro_marker_bundle_version_and_component_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    marker = tmp_path / "run" / "pro-bundle-installed.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        """{
  "bundle_version": "v2026.5.23",
  "core_version": "v2026.5.23",
  "flockspro_component_version": "pro-v2026-05-23"
}""",
        encoding="utf-8",
    )

    async def _fake_sources(_sources):
        return ["console-manifest"]

    async def _fake_manifest_info():
        return updater.ConsoleManifestRelease(
            version="v2026.5.23",
            release_notes="latest pro",
            release_url="https://cdn.example.com/flockspro-bundle-pro-v2026-05-23.zip",
            bundle_url="https://cdn.example.com/flockspro-bundle-pro-v2026-05-23.zip",
            bundle_sha256=None,
            bundle_format="zip",
            manifest={
                "bundle_version": "v2026.5.23",
                "core_version": "v2026.5.23",
                "flockspro_component_version": "pro-v2026-05-23",
            },
        )

    async def _fake_config():
        return SimpleNamespace(enabled=True, sources=["github"], repo="", token=None)

    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr("flocks.updater.deploy.detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(updater, "_get_updater_config", _fake_config)
    monkeypatch.setattr(updater, "_resolve_sources_for_edition", _fake_sources)
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _fake_manifest_info)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.5.23")

    info = await updater.check_update()
    assert info.current_version == "v2026.5.23"
    assert info.latest_version == "v2026.5.23"
    assert info.current_core_version == "v2026.5.23"
    assert info.latest_core_version == "v2026.5.23"
    assert info.current_bundle_version == "v2026.5.23"
    assert info.latest_bundle_version == "v2026.5.23"
    assert info.current_pro_component_version == "pro-v2026-05-23"
    assert info.latest_pro_component_version == "pro-v2026-05-23"
    assert info.has_update is False


@pytest.mark.asyncio
async def test_check_update_force_console_manifest_uses_bundle_versions(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    marker = tmp_path / "run" / "pro-bundle-installed.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        """{
  "bundle_version": "v2026.5.23",
  "core_version": "v2026.5.23",
  "flockspro_component_version": "pro-v2026-05-23"
}""",
        encoding="utf-8",
    )

    async def _fake_config():
        return SimpleNamespace(enabled=True, sources=["github"], repo="", token=None)

    async def _fake_manifest_info():
        return updater.ConsoleManifestRelease(
            version="v2026.5.24",
            release_notes="latest pro",
            release_url="https://console.example.com/v1/pro-bundles/rel_1/download",
            bundle_url="https://console.example.com/v1/pro-bundles/rel_1/download",
            bundle_sha256="abc123",
            bundle_format="zip",
            manifest={
                "bundle_version": "v2026.5.24",
                "core_version": "v2026.5.23",
                "flockspro_component_version": "pro-v2026-05-24",
            },
        )

    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr("flocks.updater.deploy.detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(updater, "_get_updater_config", _fake_config)
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _fake_manifest_info)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.5.23")

    info = await updater.check_update(force_console_manifest=True)

    assert info.edition == "flockspro"
    assert info.current_version == "v2026.5.23"
    assert info.latest_version == "v2026.5.24"
    assert info.current_core_version == "v2026.5.23"
    assert info.latest_core_version == "v2026.5.23"
    assert info.current_bundle_version == "v2026.5.23"
    assert info.latest_bundle_version == "v2026.5.24"
    assert info.current_pro_component_version == "pro-v2026-05-23"
    assert info.latest_pro_component_version == "pro-v2026-05-24"
    assert info.bundle_sha256 == "abc123"
    assert info.has_update is True


@pytest.mark.asyncio
async def test_check_update_force_console_manifest_detects_component_only_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    marker = tmp_path / "run" / "pro-bundle-installed.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        """{
  "bundle_version": "v2026.6.18",
  "core_version": "v2026.6.18",
  "flockspro_component_version": "v2026.6.1"
}""",
        encoding="utf-8",
    )

    async def _fake_config():
        return SimpleNamespace(enabled=True, sources=["github"], repo="", token=None)

    async def _fake_manifest_info():
        return updater.ConsoleManifestRelease(
            version="v2026.6.18",
            release_notes="latest pro",
            release_url="https://console.example.com/v1/pro-bundles/rel_2/download",
            bundle_url="https://console.example.com/v1/pro-bundles/rel_2/download",
            bundle_sha256="def456",
            bundle_format="zip",
            manifest={
                "bundle_version": "v2026.6.18",
                "core_version": "v2026.6.18",
                "flockspro_component_version": "v2026.6.2",
            },
        )

    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr("flocks.updater.deploy.detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(updater, "_get_updater_config", _fake_config)
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _fake_manifest_info)

    info = await updater.check_update(force_console_manifest=True)

    assert info.current_version == "v2026.6.18"
    assert info.latest_version == "v2026.6.18"
    assert info.current_pro_component_version == "v2026.6.1"
    assert info.latest_pro_component_version == "v2026.6.2"
    assert info.has_update is True


@pytest.mark.asyncio
async def test_check_update_force_console_manifest_reports_stale_product_marker_as_bundle_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    marker = tmp_path / "run" / "pro-bundle-installed.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        """{
  "bundle_version": "v2026.6.22",
  "core_version": "v2026.6.21",
  "flockspro_component_version": "v2026.6.23"
}""",
        encoding="utf-8",
    )

    async def _fake_config():
        return SimpleNamespace(enabled=True, sources=["github"], repo="", token=None)

    async def _fake_manifest_info():
        return updater.ConsoleManifestRelease(
            version="v2026.6.23",
            release_notes="latest pro",
            release_url="https://console.example.com/v1/pro-bundles/rel_3/download",
            bundle_url="https://console.example.com/v1/pro-bundles/rel_3/download",
            bundle_sha256="ghi789",
            bundle_format="zip",
            manifest={
                "bundle_version": "v2026.6.23",
                "core_version": "v2026.6.21",
                "flockspro_component_version": "v2026.6.23",
            },
        )

    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr("flocks.updater.deploy.detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(updater, "_get_updater_config", _fake_config)
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _fake_manifest_info)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.6.21")

    info = await updater.check_update(force_console_manifest=True)

    assert info.current_version == "v2026.6.22"
    assert info.latest_version == "v2026.6.23"
    assert info.current_bundle_version == "v2026.6.22"
    assert info.latest_bundle_version == "v2026.6.23"
    assert info.current_core_version == "v2026.6.21"
    assert info.latest_core_version == "v2026.6.21"
    assert info.current_pro_component_version == "v2026.6.23"
    assert info.latest_pro_component_version == "v2026.6.23"
    assert info.has_update is True


@pytest.mark.asyncio
async def test_check_update_trusts_pro_marker_core_when_global_marker_is_stale(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    marker = tmp_path / "run" / "pro-bundle-installed.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        """{
  "bundle_version": "v2026.7.5",
  "core_version": "v2026.7.4",
  "flockspro_component_version": "v2026.7.4"
}""",
        encoding="utf-8",
    )

    async def _fake_config():
        return SimpleNamespace(enabled=True, sources=["github"], repo="", token=None)

    async def _fake_manifest_info():
        return updater.ConsoleManifestRelease(
            version="v2026.7.5",
            release_notes="latest pro",
            release_url="https://console.example.com/v1/pro-bundles/rel_75/download",
            bundle_url="https://console.example.com/v1/pro-bundles/rel_75/download",
            bundle_sha256="sha75",
            bundle_format="zip",
            manifest={
                "bundle_version": "v2026.7.5",
                "core_version": "v2026.7.4",
                "flockspro_component_version": "v2026.7.4",
            },
        )

    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr("flocks.updater.deploy.detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(updater, "_get_updater_config", _fake_config)
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _fake_manifest_info)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.7.5")

    info = await updater.check_update(force_console_manifest=True)

    assert info.current_version == "v2026.7.5"
    assert info.current_bundle_version == "v2026.7.5"
    assert info.current_core_version == "v2026.7.4"
    assert info.latest_core_version == "v2026.7.4"


def test_console_manifest_release_identity_writes_product_and_core_versions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    merged = updater._merge_console_manifest_release_identity(
        {
            "bundle_version": "v2026.6.21",
            "core_version": "v2026.6.21",
            "flockspro_component_version": "v2026.6.23",
        },
        {
            "release_id": "rel_623",
            "bundle_version": "v2026.6.23",
            "core_version": "v2026.6.21",
            "flockspro_component_version": "v2026.6.23",
            "build_id": "job_623",
        },
    )

    assert merged["bundle_version"] == "v2026.6.23"
    assert merged["core_version"] == "v2026.6.21"
    updater._write_pro_bundle_install_marker(merged, bundle_sha256="sha623")

    marker = json.loads((tmp_path / "run" / "pro-bundle-installed.json").read_text(encoding="utf-8"))
    assert marker["bundle_version"] == "v2026.6.23"
    assert marker["core_version"] == "v2026.6.21"
    assert marker["flockspro_component_version"] == "v2026.6.23"
    assert marker["build_id"] == "job_623"
    pending = json.loads((tmp_path / "run" / "pro-bundle-install-receipt-pending.json").read_text(encoding="utf-8"))
    assert pending["install_result"] == "success"
    assert pending["bundle_version"] == "v2026.6.23"
    assert pending["core_version"] == "v2026.6.21"
    assert pending["flockspro_component_version"] == "v2026.6.23"
    assert "version_info" not in pending


def test_pro_bundle_install_marker_requires_all_runtime_versions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))

    with pytest.raises(ValueError, match="core_version"):
        updater._write_pro_bundle_install_marker(
            {
                "bundle_version": "v2026.7.5",
                "flockspro_component_version": "v2026.7.4",
            }
        )

    assert not (tmp_path / "run" / "pro-bundle-installed.json").exists()


@pytest.mark.asyncio
async def test_uninstall_pro_component_uses_uv_uninstall_without_yes_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    install_root = tmp_path / "install"
    python_path = updater._venv_python_path(install_root)
    python_path.parent.mkdir(parents=True)
    python_path.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    captured: dict[str, object] = {}

    async def _fake_run_async(cmd, cwd=None, timeout=None, env=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        captured["env"] = env
        return 0, "", ""

    monkeypatch.setattr(updater, "_run_async", _fake_run_async)

    error = await updater._uninstall_pro_component(
        uv_path="/usr/bin/uv",
        install_root=install_root,
        env={"UV_NO_PROGRESS": "1"},
    )

    assert error is None
    assert captured["cmd"] == ["/usr/bin/uv", "pip", "uninstall", "--python", str(python_path), "flockspro"]
    assert "-y" not in captured["cmd"]
    assert captured["cwd"] == install_root
    assert captured["timeout"] == 180
    assert captured["env"] == {"UV_NO_PROGRESS": "1"}


@pytest.mark.asyncio
async def test_perform_pro_bundle_downgrade_archives_pending_install_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from flocks.updater import deploy as deploy_mod

    flocks_root = tmp_path / "flocks-root"
    run_dir = flocks_root / "run"
    run_dir.mkdir(parents=True)
    pending_receipt = run_dir / "pro-bundle-install-receipt-pending.json"
    pending_receipt.write_text(
        json.dumps(
            {
                "install_result": "success",
                "bundle_version": "v2026.6.24",
                "core_version": "v2026.6.21",
                "flockspro_component_version": "v2026.6.24-pro",
            }
        ),
        encoding="utf-8",
    )
    install_marker = run_dir / "pro-bundle-installed.json"
    install_marker.write_text(
        json.dumps(
            {
                "bundle_version": "v2026.6.24",
                "core_version": "v2026.6.21",
                "flockspro_component_version": "v2026.6.24-pro",
                "installed_at": "2026-06-24T08:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    uninstall_calls: list[tuple[str, str]] = []
    report_calls: list[str] = []
    version_writes: list[str] = []

    async def _fake_uninstall_pro_component(*, uv_path, install_root, env):
        uninstall_calls.append((uv_path, str(install_root)))
        return None

    async def _after_uninstall():
        report_calls.append("reported")

    monkeypatch.setenv("FLOCKS_ROOT", str(flocks_root))
    monkeypatch.setattr(deploy_mod, "detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path / "install-root")
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.6.24")
    monkeypatch.setattr(updater, "_is_pro_component_installed", lambda: True)
    monkeypatch.setattr(updater, "_find_executable", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(updater, "_uninstall_pro_component", _fake_uninstall_pro_component)
    monkeypatch.setattr(updater, "_write_version_marker", lambda version: version_writes.append(version))
    monkeypatch.setattr(updater, "_refresh_global_cli_entry", lambda _install_root: None)

    progresses = [
        step
        async for step in updater.perform_pro_bundle_downgrade(
            restart=False,
            reason="user_requested",
            after_uninstall=_after_uninstall,
        )
    ]

    assert progresses[-1].stage == "done"
    assert uninstall_calls == [("/usr/bin/uv", str(tmp_path / "install-root"))]
    assert report_calls == ["reported"]
    assert version_writes == ["2026.6.21"]
    assert not pending_receipt.exists()
    assert not install_marker.exists()
    archived_pending = list((run_dir / "archive").glob("pro-bundle-install-receipt-pending-*.json"))
    assert len(archived_pending) == 1
    archived_payload = json.loads(archived_pending[0].read_text(encoding="utf-8"))
    assert archived_payload["install_result"] == "success"
    assert archived_payload["archive_reason"] == "user_requested"
    archived_marker = list((run_dir / "archive").glob("pro-bundle-installed-*.json"))
    assert len(archived_marker) == 1


@pytest.mark.asyncio
async def test_perform_pro_bundle_downgrade_continues_when_report_callback_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from flocks.updater import deploy as deploy_mod

    flocks_root = tmp_path / "flocks-root"
    run_dir = flocks_root / "run"
    run_dir.mkdir(parents=True)
    install_marker = run_dir / "pro-bundle-installed.json"
    install_marker.write_text(
        json.dumps(
            {
                "bundle_version": "v2026.6.24",
                "core_version": "v2026.6.21",
                "flockspro_component_version": "v2026.6.24-pro",
                "installed_at": "2026-06-24T08:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    async def _fake_uninstall_pro_component(*, uv_path, install_root, env):
        return None

    async def _after_uninstall():
        raise RuntimeError("console unavailable")

    monkeypatch.setenv("FLOCKS_ROOT", str(flocks_root))
    monkeypatch.setattr(deploy_mod, "detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path / "install-root")
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.6.21")
    monkeypatch.setattr(updater, "_is_pro_component_installed", lambda: True)
    monkeypatch.setattr(updater, "_find_executable", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(updater, "_uninstall_pro_component", _fake_uninstall_pro_component)
    monkeypatch.setattr(updater, "_refresh_global_cli_entry", lambda _install_root: None)

    progresses = [
        step
        async for step in updater.perform_pro_bundle_downgrade(
            restart=False,
            reason="user_requested",
            after_uninstall=_after_uninstall,
        )
    ]

    assert [step.stage for step in progresses] == ["checking", "downgrading", "reporting", "done"]
    assert progresses[-1].success is True
    assert not install_marker.exists()
    archived_marker = list((run_dir / "archive").glob("pro-bundle-installed-*.json"))
    assert len(archived_marker) == 1


@pytest.mark.asyncio
async def test_load_console_session_token_falls_back_to_shared_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from flocks.storage.storage import Storage

    async def _missing_storage_session(_key):
        return None

    session_path = tmp_path / "run" / "console-session.json"
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        __import__("json").dumps(
            {
                "console_session_token": "cs_shared",
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(Storage, "get", _missing_storage_session)

    assert await updater._load_console_session_token() == "cs_shared"


@pytest.mark.asyncio
async def test_load_console_session_token_prefers_shared_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from flocks.storage.storage import Storage

    async def _stale_storage_session(_key):
        return {"console_session_token": "cs_stale"}

    session_path = tmp_path / "run" / "console-session.json"
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        __import__("json").dumps(
            {
                "console_session_token": "cs_shared",
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(Storage, "get", _stale_storage_session)

    assert await updater._load_console_session_token() == "cs_shared"


@pytest.mark.asyncio
async def test_fetch_console_manifest_release_blocks_frozen_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "bundle_version": "v2026.5.10",
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


@pytest.mark.asyncio
async def test_download_console_bundle_sends_token_only_to_console_origin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    seen_headers: list[dict | None] = []

    class _Resp:
        status_code = 200
        reason_phrase = "OK"

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self, chunk_size=65536):
            yield b"bundle"

    class _Stream:
        def __init__(self, headers):
            self.headers = headers

        async def __aenter__(self):
            seen_headers.append(self.headers)
            return _Resp()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Client:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None):
            return _Stream(headers)

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(updater.httpx, "AsyncClient", _Client)

    await updater._download_console_bundle(
        "https://console.example.com/v1/pro-bundles/rel_1/download",
        "cs_manifest",
        tmp_path,
        "console.zip",
    )
    await updater._download_console_bundle(
        "https://cdn.example.com/flockspro/console.zip",
        "cs_manifest",
        tmp_path,
        "cdn.zip",
    )

    assert seen_headers == [
        {
            "Authorization": "Bearer cs_manifest",
            "x-console-session-token": "cs_manifest",
        },
        {},
    ]


@pytest.mark.asyncio
async def test_download_console_bundle_emits_byte_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    class _Resp:
        status_code = 200
        reason_phrase = "OK"
        headers = {"content-length": "11"}

        async def aiter_bytes(self, chunk_size=65536):
            yield b"hello"
            yield b" "
            yield b"world"

    class _Stream:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Client:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None):
            return _Stream()

    progresses: list[updater.UpdateProgress] = []

    async def _record_progress(progress: updater.UpdateProgress) -> None:
        progresses.append(progress)

    monkeypatch.setattr(updater.httpx, "AsyncClient", _Client)

    await updater._download_console_bundle(
        "https://cdn.example.com/flockspro/console.zip",
        "cs_manifest",
        tmp_path,
        "console.zip",
        progress_callback=_record_progress,
    )

    assert progresses
    assert progresses[-1].stage == "fetching"
    assert progresses[-1].downloaded_bytes == 11
    assert progresses[-1].total_bytes == 11
    assert progresses[-1].percent == 100


def test_absolute_console_url_normalizes_same_host_http_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://portalflocks.threatbook.cn")

    assert (
        updater._absolute_console_url(
            "http://portalflocks.threatbook.cn/v1/pro-bundles/rel_1/download?license_id=lic_1"
        )
        == "https://portalflocks.threatbook.cn/v1/pro-bundles/rel_1/download?license_id=lic_1"
    )


@pytest.mark.asyncio
async def test_download_console_bundle_reports_http_status_and_body(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    seen_urls: list[str] = []

    class _Resp:
        status_code = 401
        reason_phrase = "Unauthorized"

        async def aread(self):
            return b"missing console_session_token"

        async def aiter_bytes(self, chunk_size=65536):
            yield b""

    class _Stream:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Client:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None):
            seen_urls.append(url)
            return _Stream()

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(updater.httpx, "AsyncClient", _Client)

    bundle_url = "https://console.example.com/v1/pro-bundles/rel_1/download?license_id=lic_1"
    with pytest.raises(RuntimeError, match="HTTP 401 Unauthorized: missing console_session_token"):
        await updater._download_console_bundle(bundle_url, "cs_manifest", tmp_path, "console.zip")

    assert seen_urls == [bundle_url]


@pytest.mark.asyncio
async def test_perform_pro_bundle_install_replaces_core_and_installs_wheel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    bundle_root = tmp_path / "bundle-root"
    core_root = bundle_root / "flocks"
    core_root.mkdir(parents=True)
    (core_root / "pyproject.toml").write_text('[project]\nname = "flocks"\n', encoding="utf-8")
    (core_root / "new_core.py").write_text("UPDATED = True\n", encoding="utf-8")
    wheels = bundle_root / "wheels"
    wheels.mkdir()
    wheel = wheels / "flockspro-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"fake-wheel")
    (bundle_root / "manifest.json").write_text(
        """{
  "bundle_version": "v2026.5.11",
  "core_version": "v2026.5.10",
  "flockspro_component_version": "pro-v2026-5-10",
  "flockspro_wheel": "wheels/flockspro-0.1.0-py3-none-any.whl",
  "build_id": "job_test"
}""",
        encoding="utf-8",
    )
    bundle = tmp_path / "flockspro-bundle.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        for path in bundle_root.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(bundle_root).as_posix())

    install_root = tmp_path / "install"
    install_root.mkdir()
    (install_root / "old_core.py").write_text("OLD = True\n", encoding="utf-8")
    venv_bin = install_root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "flocks-root"))
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.5.10")

    async def _fake_manifest_info():
        return updater.ConsoleManifestRelease(
            version="v2026.5.11",
            release_notes=None,
            release_url=str(bundle),
            bundle_url=str(bundle),
            bundle_sha256=None,
            bundle_format="zip",
            manifest={
                "bundle_version": "v2026.5.11",
                "core_version": "v2026.5.10",
                "flockspro_component_version": "pro-v2026-5-10",
                "build_id": "job_test",
            },
        )

    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _fake_manifest_info)
    monkeypatch.setattr(updater, "_download_console_bundle", lambda *_args, **_kwargs: _async_path(bundle))
    monkeypatch.setattr(updater, "_verify_download_sha256", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "_find_executable", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_args, **_kwargs: tmp_path / "backup.tar.gz")
    version_writes: list[str] = []
    monkeypatch.setattr(updater, "_write_version_marker", lambda version: version_writes.append(version))
    monkeypatch.setattr(updater, "_refresh_global_cli_entry", lambda *_args, **_kwargs: None)

    captured: list[list[str]] = []

    async def _fake_run_async(cmd, **_kwargs):
        captured.append(cmd)
        return 0, "", ""

    monkeypatch.setattr(updater, "_run_async", _fake_run_async)

    progresses = [step async for step in updater.perform_pro_bundle_install(restart=False)]
    assert progresses[-1].stage == "done"
    assert (install_root / "new_core.py").is_file()
    assert not (install_root / "old_core.py").exists()
    assert any(cmd[:2] == ["/usr/bin/uv", "sync"] for cmd in captured)
    pip_installs = [cmd for cmd in captured if cmd[:3] == ["/usr/bin/uv", "pip", "install"]]
    assert pip_installs
    assert "--no-deps" in pip_installs[-1]
    assert str(wheel.name) in pip_installs[-1][-1]
    marker = tmp_path / "flocks-root" / "run" / "pro-bundle-installed.json"
    assert marker.is_file()
    marker_payload = __import__("json").loads(marker.read_text(encoding="utf-8"))
    assert version_writes == ["2026.5.10"]
    assert marker_payload["bundle_version"] == "v2026.5.11"
    assert marker_payload["core_version"] == "v2026.5.10"


@pytest.mark.asyncio
async def test_perform_pro_bundle_install_keeps_newer_local_core_when_bundle_core_is_older(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    bundle_root = tmp_path / "bundle-root"
    core_root = bundle_root / "flocks"
    core_root.mkdir(parents=True)
    (core_root / "pyproject.toml").write_text('[project]\nname = "flocks"\n', encoding="utf-8")
    (core_root / "older_core.py").write_text("OLDER = True\n", encoding="utf-8")
    wheels = bundle_root / "wheels"
    wheels.mkdir()
    wheel = wheels / "flockspro-0.2.0-py3-none-any.whl"
    wheel.write_bytes(b"fake-wheel")
    (bundle_root / "manifest.json").write_text(
        """{
  "bundle_version": "v2026.6.13",
  "core_version": "v2026.6.13",
  "flockspro_component_version": "v2026.6.2",
  "flockspro_wheel": "wheels/flockspro-0.2.0-py3-none-any.whl",
  "build_id": "job_new_pro_old_core"
}""",
        encoding="utf-8",
    )
    bundle = tmp_path / "flockspro-bundle.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        for path in bundle_root.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(bundle_root).as_posix())

    install_root = tmp_path / "install"
    install_root.mkdir()
    (install_root / "current_core.py").write_text("CURRENT = True\n", encoding="utf-8")
    venv_bin = install_root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/usr/bin/env python\n", encoding="utf-8")

    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "flocks-root"))
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.6.18")

    async def _fake_manifest_info():
        return updater.ConsoleManifestRelease(
            version="v2026.6.13",
            release_notes="new Pro on older core",
            release_url=str(bundle),
            bundle_url=str(bundle),
            bundle_sha256=None,
            bundle_format="zip",
            manifest={
                "release_id": "rel_new_pro_old_core",
                "bundle_version": "v2026.6.13",
                "core_version": "v2026.6.13",
                "flockspro_component_version": "v2026.6.2",
                "build_id": "job_new_pro_old_core",
            },
        )

    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _fake_manifest_info)
    monkeypatch.setattr(updater, "_download_console_bundle", lambda *_args, **_kwargs: _async_path(bundle))
    monkeypatch.setattr(updater, "_verify_download_sha256", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "_find_executable", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_args, **_kwargs: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_write_version_marker", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "_refresh_global_cli_entry", lambda *_args, **_kwargs: None)

    captured: list[list[str]] = []

    async def _fake_run_async(cmd, **_kwargs):
        captured.append(cmd)
        return 0, "", ""

    monkeypatch.setattr(updater, "_run_async", _fake_run_async)

    progresses = [step async for step in updater.perform_pro_bundle_install(restart=False)]

    assert progresses[-1].stage == "done"
    assert any("Keeping local Flocks v2026.6.18" in step.message for step in progresses)
    assert (install_root / "current_core.py").is_file()
    assert not (install_root / "older_core.py").exists()
    pip_installs = [cmd for cmd in captured if cmd[:3] == ["/usr/bin/uv", "pip", "install"]]
    assert pip_installs
    assert str(wheel.name) in pip_installs[-1][-1]
    marker = tmp_path / "flocks-root" / "run" / "pro-bundle-installed.json"
    marker_payload = __import__("json").loads(marker.read_text(encoding="utf-8"))
    assert marker_payload["release_id"] == "rel_new_pro_old_core"
    assert marker_payload["bundle_version"] == "v2026.6.13"
    assert marker_payload["core_version"] == "v2026.6.18"
    assert marker_payload["flockspro_component_version"] == "v2026.6.2"


@pytest.mark.asyncio
async def test_perform_pro_bundle_install_schedules_restart_before_stream_can_close(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    bundle_root = tmp_path / "bundle-root"
    core_root = bundle_root / "flocks"
    core_root.mkdir(parents=True)
    (core_root / "pyproject.toml").write_text('[project]\nname = "flocks"\n', encoding="utf-8")
    wheels = bundle_root / "wheels"
    wheels.mkdir()
    wheel = wheels / "flockspro-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"fake-wheel")
    (bundle_root / "manifest.json").write_text(
        """{
  "bundle_version": "v2026.5.10",
  "core_version": "v2026.5.10",
  "flockspro_component_version": "pro-v2026-5-10",
  "flockspro_wheel": "wheels/flockspro-0.1.0-py3-none-any.whl",
  "build_id": "job_test"
}""",
        encoding="utf-8",
    )
    bundle = tmp_path / "flockspro-bundle.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        for path in bundle_root.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(bundle_root).as_posix())

    install_root = tmp_path / "install"
    venv_bin = install_root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "flocks-root"))
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.5.10")
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", lambda: _async_manifest_info(bundle))
    monkeypatch.setattr(updater, "_download_console_bundle", lambda *_args, **_kwargs: _async_path(bundle))
    monkeypatch.setattr(updater, "_verify_download_sha256", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "_find_executable", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_args, **_kwargs: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_write_version_marker", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "_refresh_global_cli_entry", lambda *_args, **_kwargs: None)

    async def _fake_run_async(_cmd, **_kwargs):
        return 0, "", ""

    monkeypatch.setattr(updater, "_run_async", _fake_run_async)

    progresses = []
    async for step in updater.perform_pro_bundle_install(restart=True):
        progresses.append(step)
        if step.stage == "restarting":
            break

    assert progresses[-1].stage == "restarting"


async def _async_manifest_info(bundle):
    return updater.ConsoleManifestRelease(
        version="2026.5.10",
        release_notes=None,
        release_url=str(bundle),
        bundle_url=str(bundle),
        bundle_sha256=None,
        bundle_format="zip",
        manifest={
            "bundle_version": "v2026.5.10",
            "core_version": "v2026.5.10",
            "flockspro_component_version": "pro-v2026-5-10",
            "build_id": "job_test",
        },
    )


async def _async_path(path):
    return path

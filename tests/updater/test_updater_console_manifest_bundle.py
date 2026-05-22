from __future__ import annotations

import zipfile
from types import SimpleNamespace

import pytest

from flocks.updater import updater


@pytest.mark.asyncio
async def test_fetch_console_manifest_release_uses_bundle_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from flocks.storage.storage import Storage

    await Storage.set("console:session", {"console_session_token": "cs_manifest"}, "json")

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
            assert headers == {"Authorization": "Bearer cs_manifest"}
            return _Resp()

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda timeout=15: _Client())
    result = await updater._fetch_console_manifest_release()
    assert result == (
        "pro-v2026-5-10",
        "bundle release",
        "https://cdn.example.com/flockspro-bundle-v2026.5.10.tar.gz",
        None,
        "https://cdn.example.com/flockspro-bundle-v2026.5.10.tar.gz",
    )
    info = await updater._fetch_console_manifest_release_info()
    assert info.bundle_sha256 == "abc123"
    assert info.bundle_format == "tar.gz"


@pytest.mark.asyncio
async def test_check_update_uses_pro_marker_and_component_version(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    marker = tmp_path / "run" / "pro-bundle-installed.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        """{
  "installed_version": "v2026.5.23",
  "flockspro_component_version": "pro-v2026-05-23"
}""",
        encoding="utf-8",
    )

    async def _fake_sources(_sources):
        return ["console-manifest"]

    async def _fake_manifest_info():
        return updater.ConsoleManifestRelease(
            version="pro-v2026-05-23",
            release_notes="latest pro",
            release_url="https://cdn.example.com/flockspro-bundle-pro-v2026-05-23.zip",
            bundle_url="https://cdn.example.com/flockspro-bundle-pro-v2026-05-23.zip",
            bundle_sha256=None,
            bundle_format="zip",
            manifest={"flockspro_component_version": "pro-v2026-05-23"},
        )

    async def _fake_config():
        return SimpleNamespace(enabled=True, sources=["github"], repo="", token=None)

    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr("flocks.updater.deploy.detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(updater, "_get_updater_config", _fake_config)
    monkeypatch.setattr(updater, "_resolve_sources_for_edition", _fake_sources)
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _fake_manifest_info)

    info = await updater.check_update()
    assert info.current_version == "pro-v2026-05-23"
    assert info.latest_version == "pro-v2026-05-23"
    assert info.has_update is False


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


@pytest.mark.asyncio
async def test_perform_pro_bundle_install_only_installs_wheel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    bundle_root = tmp_path / "bundle-root"
    wheels = bundle_root / "wheels"
    wheels.mkdir(parents=True)
    wheel = wheels / "flockspro-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"fake-wheel")
    (bundle_root / "manifest.json").write_text(
        """{
  "display_version": "v2026.5.10",
  "oss_version": "v2026.5.10",
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
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "flocks-root"))
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", lambda: _async_manifest_info(bundle))
    monkeypatch.setattr(updater, "_download_console_bundle", lambda *_args, **_kwargs: _async_path(bundle))
    monkeypatch.setattr(updater, "_verify_download_sha256", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "_find_executable", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(
        updater,
        "_replace_install_dir",
        lambda *_args, **_kwargs: pytest.fail("Pro upgrade must not replace OSS core"),
    )

    captured: dict[str, list[str]] = {}

    async def _fake_run_async(cmd, **_kwargs):
        captured["cmd"] = cmd
        return 0, "", ""

    monkeypatch.setattr(updater, "_run_async", _fake_run_async)

    progresses = [step async for step in updater.perform_pro_bundle_install(restart=False)]
    assert progresses[-1].stage == "done"
    assert captured["cmd"][:3] == ["/usr/bin/uv", "pip", "install"]
    assert "--no-deps" in captured["cmd"]
    assert str(wheel.name) in captured["cmd"][-1]


@pytest.mark.asyncio
async def test_perform_pro_bundle_install_schedules_restart_before_stream_can_close(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    bundle_root = tmp_path / "bundle-root"
    wheels = bundle_root / "wheels"
    wheels.mkdir(parents=True)
    wheel = wheels / "flockspro-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"fake-wheel")
    (bundle_root / "manifest.json").write_text(
        """{
  "display_version": "v2026.5.10",
  "oss_version": "v2026.5.10",
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
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", lambda: _async_manifest_info(bundle))
    monkeypatch.setattr(updater, "_download_console_bundle", lambda *_args, **_kwargs: _async_path(bundle))
    monkeypatch.setattr(updater, "_verify_download_sha256", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "_find_executable", lambda name: "/usr/bin/uv" if name == "uv" else None)

    async def _fake_run_async(_cmd, **_kwargs):
        return 0, "", ""

    monkeypatch.setattr(updater, "_run_async", _fake_run_async)
    spawned: dict[str, object] = {}

    def _fake_spawn_detached_process(command, *, cwd, log_path):
        spawned["command"] = command
        spawned["cwd"] = cwd
        spawned["log_path"] = log_path

        class _Process:
            pid = 12345

        return _Process()

    monkeypatch.setattr(updater, "_spawn_detached_process", _fake_spawn_detached_process)
    scheduled: dict[str, object] = {}

    class _Loop:
        def call_later(self, delay, callback):
            scheduled["delay"] = delay
            scheduled["callback"] = callback
            return None

    monkeypatch.setattr(updater.asyncio, "get_running_loop", lambda: _Loop())

    progresses = []
    async for step in updater.perform_pro_bundle_install(restart=True):
        progresses.append(step)
        if step.stage == "restarting":
            break

    assert progresses[-1].stage == "restarting"
    assert scheduled["delay"] == 0.8
    assert callable(scheduled["callback"])
    scheduled["callback"]()
    assert spawned["command"][1:4] == ["-m", "flocks.cli.main", "restart"]
    assert "--no-browser" in spawned["command"]
    assert "--skip-webui-build" in spawned["command"]


async def _async_manifest_info(bundle):
    return updater.ConsoleManifestRelease(
        version="2026.5.10",
        release_notes=None,
        release_url=str(bundle),
        bundle_url=str(bundle),
        bundle_sha256=None,
        bundle_format="zip",
        manifest={
            "display_version": "v2026.5.10",
            "oss_version": "v2026.5.10",
            "flockspro_component_version": "pro-v2026-5-10",
            "build_id": "job_test",
        },
    )


async def _async_path(path):
    return path


"""Releases that ship their built runtime, and installations that cannot download.

An update channel may hand the updater a prebuilt release (the WebUI already
built, dependencies as wheels on disk) and extra wheels to install; an offline
installation refuses every network-based upgrade.  Nothing here may run
``uv sync`` against an index or npm when the release says it is prebuilt.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flocks.updater import restart_handoff, updater


def _write_component(directory: Path) -> Path:
    """A component wheel an update channel would ask the updater to install."""
    wheels = directory / "wheels"
    wheels.mkdir(parents=True)
    wheel = wheels / "component-2026.9.14-py3-none-any.whl"
    wheel.write_bytes(b"fake-component-wheel")
    return wheel


def _prepare_install_root(tmp_path: Path, *, with_dist: bool = True) -> Path:
    install_root = tmp_path / "install"
    venv_bin = install_root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    (install_root / "pyproject.toml").write_text('[project]\nname = "flocks"\n', encoding="utf-8")
    webui = install_root / "webui"
    webui.mkdir()
    (webui / "package.json").write_text("{}", encoding="utf-8")
    if with_dist:
        dist = webui / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    return install_root


def _stub_install_side_effects(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, install_root: Path) -> list[list[str]]:
    captured: list[list[str]] = []

    async def _fake_run_async(cmd, **_kwargs):
        captured.append(list(cmd))
        return 0, "", ""

    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "flocks-root"))
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.9.14")
    monkeypatch.setattr(updater, "_find_executable", lambda name: "/opt/flocks/tools/uv/uv" if name == "uv" else None)
    monkeypatch.setattr(updater, "_refresh_global_cli_entry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "_run_async", _fake_run_async)
    return captured


@pytest.mark.asyncio
async def test_install_or_repair_source_prebuilt_skips_sync_and_frontend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root = _prepare_install_root(tmp_path)
    captured = _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    bundle_dir = tmp_path / "bundle"
    wheel = _write_component(bundle_dir)
    staged_marker = tmp_path / "staged-marker.json"
    staged_marker.write_text('{"installed": true}', encoding="utf-8")
    marker_destination = tmp_path / "flocks-root" / "run" / "component-installed.json"

    async def _frontend_must_not_run(*_args, **_kwargs):
        raise AssertionError("frontend build must not run for prebuilt releases")

    monkeypatch.setattr(updater, "_build_frontend_workspace", _frontend_must_not_run)
    monkeypatch.setattr(updater, "_sync_project_dependencies", _frontend_must_not_run)
    version_writes: list[str] = []
    monkeypatch.setattr(updater, "_write_version_marker", lambda version: version_writes.append(version))

    await updater.install_or_repair_source(
        install_root=install_root,
        uv_path="/opt/flocks/tools/uv/uv",
        version="v2026.9.14",
        extra_wheels=[wheel],
        success_files=[(staged_marker, marker_destination)],
        prebuilt=True,
    )

    assert [cmd[:3] for cmd in captured] == [["/opt/flocks/tools/uv/uv", "pip", "install"]]
    assert version_writes == ["2026.9.14"]
    # What the channel staged is put in place once the install succeeded.
    assert json.loads(marker_destination.read_text(encoding="utf-8")) == {"installed": True}


@pytest.mark.asyncio
async def test_install_or_repair_source_prebuilt_with_dependency_wheels_syncs_offline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root = _prepare_install_root(tmp_path)
    captured = _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    bundle_dir = tmp_path / "bundle"
    wheel = _write_component(bundle_dir)
    deps = bundle_dir / "dependency-wheels"
    deps.mkdir()
    monkeypatch.setattr(updater, "_write_version_marker", lambda *_args, **_kwargs: None)

    await updater.install_or_repair_source(
        install_root=install_root,
        uv_path="/opt/flocks/tools/uv/uv",
        version="v2026.9.14",
        extra_wheels=[wheel],
        prebuilt=True,
        dependency_wheels_dir=deps,
    )

    assert captured[0] == [
        "/opt/flocks/tools/uv/uv",
        "sync",
        "--frozen",
        "--offline",
        "--no-python-downloads",
        "--find-links",
        str(deps),
    ]
    assert captured[1][:3] == ["/opt/flocks/tools/uv/uv", "pip", "install"]


@pytest.mark.asyncio
async def test_install_or_repair_source_prebuilt_requires_webui_dist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root = _prepare_install_root(tmp_path, with_dist=False)
    captured = _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    monkeypatch.delenv("FLOCKS_WEBUI_DIST_DIR", raising=False)
    monkeypatch.setattr("flocks.server.static_webui.resolve_webui_dist_dir", lambda: None)
    bundle_dir = tmp_path / "bundle"
    wheel = _write_component(bundle_dir)

    with pytest.raises(RuntimeError, match="webui/dist"):
        await updater.install_or_repair_source(
            install_root=install_root,
            uv_path="/opt/flocks/tools/uv/uv",
            version="v2026.9.14",
            extra_wheels=[wheel],
            prebuilt=True,
        )

    assert captured == []


@pytest.mark.asyncio
async def test_install_or_repair_source_default_still_syncs_and_builds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Regression guard: the online path is unchanged when ``prebuilt`` is left at its default."""
    install_root = _prepare_install_root(tmp_path)
    captured = _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    bundle_dir = tmp_path / "bundle"
    wheel = _write_component(bundle_dir)
    frontend_calls: list[Path] = []

    async def _fake_frontend(webui_dir, **_kwargs):
        frontend_calls.append(webui_dir)
        return None

    from flocks.cli import service_manager

    monkeypatch.setattr(service_manager, "resolve_npm_executable", lambda: "/usr/bin/npm")
    monkeypatch.setattr(service_manager, "node_version_satisfies_requirement", lambda: True)
    monkeypatch.setattr(updater, "_build_frontend_workspace", _fake_frontend)
    monkeypatch.setattr(updater, "_write_version_marker", lambda *_args, **_kwargs: None)

    await updater.install_or_repair_source(
        install_root=install_root,
        uv_path="/usr/bin/uv",
        version="v2026.9.14",
        extra_wheels=[wheel],
    )

    assert captured[0][:2] == ["/usr/bin/uv", "sync"]
    assert "--offline" not in captured[0]
    assert captured[1][:3] == ["/usr/bin/uv", "pip", "install"]
    assert frontend_calls == [install_root / "webui"]



@pytest.mark.asyncio
async def test_install_or_repair_source_reports_each_step_in_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root = _prepare_install_root(tmp_path)
    _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    wheel = _write_component(tmp_path / "bundle")
    steps: list[str] = []

    async def _fake_frontend(_webui_dir, **_kwargs):
        steps.append("<frontend build>")
        return None

    from flocks.cli import service_manager

    monkeypatch.setattr(service_manager, "resolve_npm_executable", lambda: "/usr/bin/npm")
    monkeypatch.setattr(service_manager, "node_version_satisfies_requirement", lambda: True)
    monkeypatch.setattr(updater, "_build_frontend_workspace", _fake_frontend)
    monkeypatch.setattr(updater, "_write_version_marker", lambda *_args, **_kwargs: None)

    await updater.install_or_repair_source(
        install_root=install_root,
        uv_path="/usr/bin/uv",
        version="v2026.9.14",
        extra_wheels=[wheel],
        on_step=steps.append,
    )

    assert steps == [
        "check_runtime", "sync_dependencies", "install_components", "build_webui", "<frontend build>", "finalize",
    ]


@pytest.mark.asyncio
async def test_install_or_repair_source_reports_npm_check_failure_as_its_own_step(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root = _prepare_install_root(tmp_path)
    _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    steps: list[str] = []

    from flocks.cli import service_manager

    monkeypatch.setattr(service_manager, "resolve_npm_executable", lambda: None)

    with pytest.raises(RuntimeError, match="npm was not found"):
        await updater.install_or_repair_source(
            install_root=install_root,
            uv_path="/usr/bin/uv",
            version="v2026.9.14",
            on_step=steps.append,
        )

    assert steps == ["check_runtime"]


@pytest.mark.asyncio
async def test_install_or_repair_source_survives_a_failing_step_reporter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root = _prepare_install_root(tmp_path)
    _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    wheel = _write_component(tmp_path / "bundle")
    version_writes: list[str] = []
    monkeypatch.setattr(updater, "_write_version_marker", lambda version: version_writes.append(version))

    def broken_reporter(_step: str) -> None:
        raise RuntimeError("progress file is read-only")

    await updater.install_or_repair_source(
        install_root=install_root,
        uv_path="/opt/flocks/tools/uv/uv",
        version="v2026.9.14",
        extra_wheels=[wheel],
        prebuilt=True,
        on_step=broken_reporter,
    )

    assert version_writes == ["2026.9.14"]

def test_build_restart_handoff_argv_adds_prebuilt_flags_only_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from flocks.cli import service_manager

    monkeypatch.setattr(updater, "_handoff_service_config", lambda: service_manager.ServiceConfig())
    base_kwargs = dict(uv_path="uv", sync_timeout=300, version="2026.9.14", current_version="2026.9.14")

    plain = updater._build_restart_handoff_argv(["python"], tmp_path, **base_kwargs)
    assert "--prebuilt" not in plain
    assert "--dependency-wheels-dir" not in plain

    deps = tmp_path / "wheels"
    argv = updater._build_restart_handoff_argv(
        ["python"],
        tmp_path,
        prebuilt=True,
        dependency_wheels_dir=deps,
        **base_kwargs,
    )
    assert "--prebuilt" in argv
    assert argv[argv.index("--dependency-wheels-dir") + 1] == str(deps)
    assert argv.index("--prebuilt") < argv.index("--")


def test_restart_handoff_parses_prebuilt_and_forwards_to_installer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    common = [
        "--backend-host", "127.0.0.1", "--backend-port", "5173",
        "--frontend-host", "127.0.0.1", "--frontend-port", "5173",
        "--install-root", str(tmp_path), "--uv-path", "uv", "--sync-timeout", "300",
        "--version", "2026.9.14", "--current-version", "2026.9.14",
    ]
    args = restart_handoff._parse_args(common + ["--prebuilt", "--dependency-wheels-dir", str(tmp_path / "w"), "--", "python"])
    assert args.prebuilt is True
    assert args.dependency_wheels_dir == str(tmp_path / "w")
    assert args.restart_argv == ["python"]

    plain = restart_handoff._parse_args(common + ["--", "python"])
    assert plain.prebuilt is False
    assert plain.dependency_wheels_dir is None

    forwarded: list[dict] = []

    async def _fake_install(**kwargs):
        forwarded.append(kwargs)

    monkeypatch.setattr(restart_handoff.updater_module, "install_or_repair_source", _fake_install)
    assert restart_handoff._run_upgrade_tasks(args) is None
    assert forwarded[0]["prebuilt"] is True
    assert forwarded[0]["dependency_wheels_dir"] == tmp_path / "w"

    assert restart_handoff._run_upgrade_tasks(plain) is None
    assert forwarded[1]["prebuilt"] is False
    assert forwarded[1]["dependency_wheels_dir"] is None


def test_detect_deploy_mode_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    from flocks.updater import deploy

    deploy.detect_deploy_mode.cache_clear()
    monkeypatch.delenv("FLOCKS_DEPLOY_MODE", raising=False)
    monkeypatch.setenv("FLOCKS_OFFLINE_INSTALL", "1")
    assert deploy.detect_deploy_mode() == "offline"

    deploy.detect_deploy_mode.cache_clear()
    monkeypatch.setenv("FLOCKS_DEPLOY_MODE", "offline")
    monkeypatch.delenv("FLOCKS_OFFLINE_INSTALL", raising=False)
    assert deploy.detect_deploy_mode() == "offline"

    deploy.detect_deploy_mode.cache_clear()
    monkeypatch.setenv("FLOCKS_DEPLOY_MODE", "source")
    monkeypatch.setenv("FLOCKS_OFFLINE_INSTALL", "1")  # explicit mode always wins
    assert deploy.detect_deploy_mode() == "source"
    deploy.detect_deploy_mode.cache_clear()


@pytest.mark.asyncio
async def test_offline_install_refuses_network_source_upgrade(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root = _prepare_install_root(tmp_path)
    captured = _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    monkeypatch.setattr("flocks.updater.deploy.detect_deploy_mode", lambda: "offline")

    async def _must_not_download(*_args, **_kwargs):
        raise AssertionError("nothing may be downloaded on an offline install")

    monkeypatch.setattr(updater, "_download_with_fallback", _must_not_download)
    monkeypatch.setattr(updater, "_download_channel_archive", _must_not_download)
    monkeypatch.setattr(updater, "_get_updater_config", _must_not_download)

    progresses = [step async for step in updater.perform_update("2026.10.1", restart=True)]

    assert [step.stage for step in progresses] == ["error"]
    assert "flocks-offline.run" in progresses[0].message
    assert captured == []
    assert (install_root / "pyproject.toml").is_file()


@pytest.mark.asyncio
async def test_check_update_marks_offline_install_as_not_upgradable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr("flocks.updater.deploy.detect_deploy_mode", lambda: "offline")
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.9.14")

    async def _fake_config():
        return SimpleNamespace(enabled=True, sources=["github"], repo="AgentFlocks/Flocks", token=None)

    async def _fake_release(*_args, **_kwargs):
        raise AssertionError("an offline deployment must not query GitHub / Gitee for the core version")

    monkeypatch.setattr(updater, "_get_updater_config", _fake_config)
    monkeypatch.setattr(updater, "get_latest_release", _fake_release)

    info = await updater.check_update()

    assert info.deploy_mode == "offline"
    assert info.update_allowed is False
    assert info.current_version == "2026.9.14"
    assert info.latest_version is None and info.has_update is False and info.error is None

    # An extension's channel still answers: its endpoint may be the one such a machine reaches.
    from flocks.updater.channels import register_update_channel, unregister_update_channel
    from flocks.updater.models import VersionInfo

    class _Channel:
        name = "offline-test-channel"

        async def check(self, *, locale=None, remote=True):
            return VersionInfo(current_version="v2026.9.14", latest_version="v2026.9.20", has_update=True)

        async def release(self, *, version=None):
            raise AssertionError("checking installs nothing")

    register_update_channel(_Channel())
    try:
        channel_info = await updater.check_update(channel="offline-test-channel")
    finally:
        unregister_update_channel("offline-test-channel")
    assert channel_info.deploy_mode == "offline" and channel_info.update_allowed is False
    assert channel_info.latest_version == "v2026.9.20" and channel_info.channel == "offline-test-channel"


def test_restart_handoff_prebuilt_restart_skips_sync_and_build(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The handoff's restart mode with --prebuilt must not touch uv sync or npm (what an offline downgrade runs)."""
    install_root = _prepare_install_root(tmp_path)
    captured = _stub_install_side_effects(monkeypatch, tmp_path, install_root)

    async def _must_not_run(*_args, **_kwargs):
        raise AssertionError("dependency sync / frontend build must not run in prebuilt restart")

    monkeypatch.setattr(restart_handoff.updater_module, "_sync_project_dependencies", _must_not_run)
    monkeypatch.setattr(restart_handoff.updater_module, "_build_frontend_workspace", _must_not_run)
    monkeypatch.setattr(restart_handoff.updater_module, "_write_version_marker", lambda *_a, **_k: None)

    args = restart_handoff._parse_args(
        [
            "--backend-host", "0.0.0.0", "--backend-port", "5173",
            "--frontend-host", "0.0.0.0", "--frontend-port", "5173",
            "--install-root", str(install_root), "--uv-path", "/opt/flocks/tools/uv/uv",
            "--sync-timeout", "300", "--version", "2026.9.14", "--current-version", "2026.9.14",
            "--prebuilt", "--", "python",
        ]
    )
    assert restart_handoff._run_upgrade_tasks(args) is None
    assert captured == []  # no uv pip install either: nothing to install on a downgrade

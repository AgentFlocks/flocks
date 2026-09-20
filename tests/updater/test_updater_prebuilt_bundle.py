"""Offline (prebuilt / local) Pro bundle installation paths.

The offline installer ships the Pro bundle on disk and points ``FLOCKS_PRO_BUNDLE_DIR``
at it. Nothing here may run ``uv sync`` or npm, and nothing may be downloaded.
Every online default is asserted to be untouched when the variable is absent.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from flocks.updater import restart_handoff, updater


def _write_local_bundle(
    bundle_dir: Path,
    *,
    prebuilt: bool = True,
    with_core: bool = False,
    extra_manifest: dict | None = None,
) -> Path:
    wheels = bundle_dir / "wheels"
    wheels.mkdir(parents=True)
    wheel = wheels / "flockspro-2026.9.14-py3-none-any.whl"
    wheel.write_bytes(b"fake-flockspro-wheel")
    manifest = {
        "bundle_version": "v2026.9.14",
        "core_version": "v2026.9.14",
        "flockspro_component_version": "2026.9.14",
        "flockspro_wheel": "wheels/flockspro-2026.9.14-py3-none-any.whl",
        "build_id": "offline_build_1",
    }
    if prebuilt:
        manifest["prebuilt"] = True
    if extra_manifest:
        manifest.update(extra_manifest)
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if with_core:
        core = bundle_dir / "flocks"
        core.mkdir()
        (core / "pyproject.toml").write_text('[project]\nname = "flocks"\n', encoding="utf-8")
        (core / "new_core.py").write_text("NEW = True\n", encoding="utf-8")
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


async def _no_console(*_args, **_kwargs):
    raise RuntimeError("console unreachable (offline)")


async def _no_download(*_args, **_kwargs):
    raise AssertionError("bundle download must not happen for a local bundle")


# --------------------------------------------------------------------------- #
# bundle content resolution
# --------------------------------------------------------------------------- #


def test_resolve_pro_bundle_content_accepts_prebuilt_bundle_without_core_source(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    wheel = _write_local_bundle(bundle_dir)

    source_root, wheel_path, manifest = updater._resolve_pro_bundle_content(bundle_dir)

    assert source_root == bundle_dir
    assert wheel_path == wheel
    assert manifest["prebuilt"] is True
    assert not (source_root / "pyproject.toml").exists()


def test_resolve_pro_bundle_content_keeps_online_rule_for_non_prebuilt_manifest(tmp_path: Path) -> None:
    """A manifest without ``flocks/`` and without ``prebuilt`` is still not a Pro bundle."""
    bundle_dir = tmp_path / "bundle"
    _write_local_bundle(bundle_dir, prebuilt=False)

    source_root, wheel_path, manifest = updater._resolve_pro_bundle_content(bundle_dir)

    assert source_root == bundle_dir
    assert wheel_path is None
    assert manifest == {}


def test_resolve_pro_bundle_content_prebuilt_with_core_source_uses_flocks_dir(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    wheel = _write_local_bundle(bundle_dir, with_core=True)

    source_root, wheel_path, manifest = updater._resolve_pro_bundle_content(bundle_dir)

    assert source_root == bundle_dir / "flocks"
    assert wheel_path == wheel
    assert manifest["prebuilt"] is True
    assert updater._pro_bundle_root(source_root) == bundle_dir


def test_prebuilt_manifest_flag_parsing() -> None:
    assert updater._is_prebuilt_pro_bundle_manifest({"prebuilt": True}) is True
    assert updater._is_prebuilt_pro_bundle_manifest({"prebuilt": "yes"}) is True
    assert updater._is_prebuilt_pro_bundle_manifest({"prebuilt": "false"}) is False
    assert updater._is_prebuilt_pro_bundle_manifest({}) is False
    assert updater._is_prebuilt_pro_bundle_manifest(None) is False


# --------------------------------------------------------------------------- #
# FLOCKS_PRO_BUNDLE_DIR gating
# --------------------------------------------------------------------------- #


def test_local_pro_bundle_dir_is_only_used_when_env_is_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_local_bundle(bundle_dir)

    monkeypatch.delenv("FLOCKS_PRO_BUNDLE_DIR", raising=False)
    assert updater._local_pro_bundle_dir() is None

    monkeypatch.setenv("FLOCKS_PRO_BUNDLE_DIR", str(tmp_path / "missing"))
    assert updater._local_pro_bundle_dir() is None

    monkeypatch.setenv("FLOCKS_PRO_BUNDLE_DIR", str(bundle_dir))
    assert updater._local_pro_bundle_dir() == bundle_dir


def test_load_local_pro_bundle_release_requires_runtime_versions(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_local_bundle(bundle_dir)
    info = updater._load_local_pro_bundle_release(bundle_dir)
    assert info.version == "v2026.9.14"
    assert info.bundle_format == "dir"
    assert info.bundle_url == str(bundle_dir)

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "manifest.json").write_text('{"prebuilt": true, "bundle_version": "v1"}', encoding="utf-8")
    with pytest.raises(ValueError, match="core_version"):
        updater._load_local_pro_bundle_release(broken)


# --------------------------------------------------------------------------- #
# perform_pro_bundle_install: local bundle
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_perform_pro_bundle_install_uses_local_bundle_without_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "opt-bundle"
    wheel = _write_local_bundle(bundle_dir, extra_manifest={"release_id": "rel_offline_1"})
    install_root = _prepare_install_root(tmp_path)
    captured = _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    monkeypatch.setenv("FLOCKS_PRO_BUNDLE_DIR", str(bundle_dir))
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _no_console)
    monkeypatch.setattr(updater, "_download_console_bundle", _no_download)
    version_writes: list[str] = []
    monkeypatch.setattr(updater, "_write_version_marker", lambda version: version_writes.append(version))

    progresses = [step async for step in updater.perform_pro_bundle_install(restart=False)]

    stages = [step.stage for step in progresses]
    assert stages[0] == "fetching"
    assert "Using local Flocks Pro bundle" in progresses[0].message
    assert progresses[-1].stage == "done"
    assert "backing_up" not in stages
    assert "syncing" not in stages

    # Only the Pro wheel is installed: no uv sync, no npm.
    assert len(captured) == 1
    cmd = captured[0]
    assert cmd[:3] == ["/opt/flocks/tools/uv/uv", "pip", "install"]
    assert "--no-deps" in cmd
    assert cmd[-1].endswith(wheel.name)
    assert not any("sync" in c or "npm" in c[0] for c in captured)

    # Shipped bundle stays intact for the next run; wheel was staged from a copy.
    assert wheel.is_file()
    assert not cmd[-1].startswith(str(bundle_dir))

    marker = json.loads((tmp_path / "flocks-root" / "run" / "pro-bundle-installed.json").read_text(encoding="utf-8"))
    assert marker["release_id"] == "rel_offline_1"
    assert marker["bundle_version"] == "v2026.9.14"
    assert marker["core_version"] == "v2026.9.14"
    assert marker["flockspro_component_version"] == "2026.9.14"
    assert marker["build_id"] == "offline_build_1"
    assert version_writes == ["2026.9.14"]
    assert (install_root / "webui" / "dist" / "index.html").is_file()


@pytest.mark.asyncio
async def test_perform_pro_bundle_install_local_verifies_wheel_sha256(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "opt-bundle"
    wheel = _write_local_bundle(bundle_dir, extra_manifest={"bundle_sha256": "0" * 64})
    install_root = _prepare_install_root(tmp_path)
    captured = _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    monkeypatch.setenv("FLOCKS_PRO_BUNDLE_DIR", str(bundle_dir))
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _no_console)

    progresses = [step async for step in updater.perform_pro_bundle_install(restart=False)]
    assert progresses[-1].stage == "error"
    assert "sha256" in progresses[-1].message.lower()
    assert captured == []

    good_sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
    manifest_path = bundle_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_sha256"] = good_sha
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    progresses = [step async for step in updater.perform_pro_bundle_install(restart=False)]
    assert progresses[-1].stage == "done"
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_perform_pro_bundle_install_local_merges_console_identity_only_when_versions_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "opt-bundle"
    _write_local_bundle(bundle_dir)
    install_root = _prepare_install_root(tmp_path)
    _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    monkeypatch.setenv("FLOCKS_PRO_BUNDLE_DIR", str(bundle_dir))
    monkeypatch.setattr(updater, "_download_console_bundle", _no_download)
    marker_path = tmp_path / "flocks-root" / "run" / "pro-bundle-installed.json"

    async def _console_same_version(*_args, **_kwargs):
        return updater.ConsoleManifestRelease(
            version="v2026.9.14",
            release_notes=None,
            release_url="https://portal.example/bundle.tar.gz",
            bundle_url="https://portal.example/bundle.tar.gz",
            bundle_sha256=None,
            bundle_format="tar.gz",
            manifest={
                "release_id": "rel_console_match",
                "bundle_version": "v2026.9.14",
                "core_version": "v2026.9.14",
                "flockspro_component_version": "2026.9.14",
            },
        )

    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _console_same_version)
    progresses = [step async for step in updater.perform_pro_bundle_install(restart=False)]
    assert progresses[-1].stage == "done"
    assert json.loads(marker_path.read_text(encoding="utf-8"))["release_id"] == "rel_console_match"

    async def _console_newer_version(*_args, **_kwargs):
        return updater.ConsoleManifestRelease(
            version="v2026.10.1",
            release_notes=None,
            release_url="https://portal.example/newer.tar.gz",
            bundle_url="https://portal.example/newer.tar.gz",
            bundle_sha256=None,
            bundle_format="tar.gz",
            manifest={
                "release_id": "rel_console_newer",
                "bundle_version": "v2026.10.1",
                "core_version": "v2026.10.1",
                "flockspro_component_version": "2026.10.1",
            },
        )

    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _console_newer_version)
    progresses = [step async for step in updater.perform_pro_bundle_install(restart=False)]
    assert progresses[-1].stage == "done"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    # Local bundle wins; the newer Console release is neither downloaded nor recorded.
    assert marker["release_id"] is None
    assert marker["bundle_version"] == "v2026.9.14"


@pytest.mark.asyncio
async def test_perform_pro_bundle_install_rejects_local_archive_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle-src"
    _write_local_bundle(bundle_dir)
    archive = tmp_path / "flockspro-offline-bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in bundle_dir.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(bundle_dir).as_posix())
    install_root = _prepare_install_root(tmp_path)
    captured = _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _no_console)
    monkeypatch.setattr(updater, "_download_console_bundle", _no_download)

    # perform_pro_bundle_install needs a directory (manifest.json describes the release
    # before anything is staged); archives are only accepted by perform_update itself.
    progresses = [
        step async for step in updater.perform_pro_bundle_install(restart=False, local_bundle_dir=archive)
    ]

    assert progresses[-1].stage == "error"
    assert "manifest.json" in progresses[-1].message or "invalid" in progresses[-1].message.lower()
    assert captured == []


@pytest.mark.asyncio
async def test_perform_pro_bundle_install_ignores_bundle_on_disk_without_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Regression guard: online installations never consult a local bundle."""
    bundle_dir = tmp_path / "opt-bundle"
    _write_local_bundle(bundle_dir)
    install_root = _prepare_install_root(tmp_path)
    captured = _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    monkeypatch.delenv("FLOCKS_PRO_BUNDLE_DIR", raising=False)
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _no_console)

    progresses = [step async for step in updater.perform_pro_bundle_install(restart=False)]

    assert progresses[-1].stage == "error"
    assert "manifest" in progresses[-1].message.lower()
    assert captured == []


@pytest.mark.asyncio
async def test_perform_pro_bundle_install_prebuilt_core_upgrade_needs_dependency_wheels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "opt-bundle"
    _write_local_bundle(
        bundle_dir,
        with_core=True,
        extra_manifest={"bundle_version": "v2026.10.1", "core_version": "v2026.10.1"},
    )
    install_root = _prepare_install_root(tmp_path)
    captured = _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    monkeypatch.setenv("FLOCKS_PRO_BUNDLE_DIR", str(bundle_dir))
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _no_console)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_args, **_kwargs: tmp_path / "backup.tar.gz")

    progresses = [step async for step in updater.perform_pro_bundle_install(restart=True)]

    assert progresses[-1].stage == "error"
    assert "dependency wheels" in progresses[-1].message
    assert captured == []
    assert not (install_root / "new_core.py").exists()


@pytest.mark.asyncio
async def test_perform_pro_bundle_install_local_restart_handoff_carries_prebuilt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "opt-bundle"
    _write_local_bundle(bundle_dir)
    install_root = _prepare_install_root(tmp_path)
    _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    monkeypatch.setenv("FLOCKS_PRO_BUNDLE_DIR", str(bundle_dir))
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _no_console)
    monkeypatch.setattr(updater, "_download_console_bundle", _no_download)

    spawned: list[list[str]] = []

    class _Proc:
        pid = 4242

    from flocks.cli import service_manager

    monkeypatch.setattr(updater, "_handoff_service_config", lambda: service_manager.ServiceConfig())
    monkeypatch.setattr(updater, "_spawn_restart_handoff", lambda argv, cwd: spawned.append(argv) or _Proc())
    monkeypatch.setattr(updater.os, "_exit", lambda code: None)

    # os._exit is stubbed, so the generator runs to completion after spawning the handoff.
    progresses = [step async for step in updater.perform_pro_bundle_install(restart=True)]

    assert progresses[-1].stage == "restarting"
    assert spawned, "restart handoff was not spawned"
    argv = spawned[0]
    assert "--prebuilt" in argv
    assert "--mode" not in argv  # Pro-only change: no source replacement
    assert "--pro-wheel-path" in argv
    assert "--dependency-wheels-dir" not in argv


# --------------------------------------------------------------------------- #
# install_or_repair_source(prebuilt=...)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_install_or_repair_source_prebuilt_skips_sync_and_frontend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root = _prepare_install_root(tmp_path)
    captured = _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    bundle_dir = tmp_path / "bundle"
    wheel = _write_local_bundle(bundle_dir)

    async def _frontend_must_not_run(*_args, **_kwargs):
        raise AssertionError("frontend build must not run for prebuilt bundles")

    monkeypatch.setattr(updater, "_build_frontend_workspace", _frontend_must_not_run)
    monkeypatch.setattr(updater, "_sync_project_dependencies", _frontend_must_not_run)
    version_writes: list[str] = []
    monkeypatch.setattr(updater, "_write_version_marker", lambda version: version_writes.append(version))

    await updater.install_or_repair_source(
        install_root=install_root,
        uv_path="/opt/flocks/tools/uv/uv",
        version="v2026.9.14",
        pro_wheel_path=wheel,
        pro_bundle_manifest_path=bundle_dir / "manifest.json",
        prebuilt=True,
    )

    assert [cmd[:3] for cmd in captured] == [["/opt/flocks/tools/uv/uv", "pip", "install"]]
    assert version_writes == ["2026.9.14"]
    assert (tmp_path / "flocks-root" / "run" / "pro-bundle-installed.json").is_file()


@pytest.mark.asyncio
async def test_install_or_repair_source_prebuilt_with_dependency_wheels_syncs_offline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root = _prepare_install_root(tmp_path)
    captured = _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    bundle_dir = tmp_path / "bundle"
    wheel = _write_local_bundle(bundle_dir)
    deps = bundle_dir / "dependency-wheels"
    deps.mkdir()
    monkeypatch.setattr(updater, "_write_version_marker", lambda *_args, **_kwargs: None)

    await updater.install_or_repair_source(
        install_root=install_root,
        uv_path="/opt/flocks/tools/uv/uv",
        version="v2026.9.14",
        pro_wheel_path=wheel,
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
    wheel = _write_local_bundle(bundle_dir)

    with pytest.raises(RuntimeError, match="webui/dist"):
        await updater.install_or_repair_source(
            install_root=install_root,
            uv_path="/opt/flocks/tools/uv/uv",
            version="v2026.9.14",
            pro_wheel_path=wheel,
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
    wheel = _write_local_bundle(bundle_dir, prebuilt=False)
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
        pro_wheel_path=wheel,
    )

    assert captured[0][:2] == ["/usr/bin/uv", "sync"]
    assert "--offline" not in captured[0]
    assert captured[1][:3] == ["/usr/bin/uv", "pip", "install"]
    assert frontend_calls == [install_root / "webui"]


# --------------------------------------------------------------------------- #
# restart handoff plumbing
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# offline deploy mode: network in-place upgrades are refused
# --------------------------------------------------------------------------- #


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
    monkeypatch.setattr(updater, "_download_console_bundle", _must_not_download)
    monkeypatch.setattr(updater, "_get_updater_config", _must_not_download)

    progresses = [step async for step in updater.perform_update("2026.10.1", restart=True)]

    assert [step.stage for step in progresses] == ["error"]
    assert "flocks-offline.run" in progresses[0].message
    assert captured == []
    assert (install_root / "pyproject.toml").is_file()


@pytest.mark.asyncio
async def test_offline_install_still_installs_local_pro_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "opt-bundle"
    _write_local_bundle(bundle_dir)
    install_root = _prepare_install_root(tmp_path)
    captured = _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    monkeypatch.setattr("flocks.updater.deploy.detect_deploy_mode", lambda: "offline")
    monkeypatch.setenv("FLOCKS_PRO_BUNDLE_DIR", str(bundle_dir))
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _no_console)
    monkeypatch.setattr(updater, "_download_console_bundle", _no_download)
    monkeypatch.setattr(updater, "_write_version_marker", lambda *_args, **_kwargs: None)

    progresses = [step async for step in updater.perform_pro_bundle_install(restart=False)]

    assert progresses[-1].stage == "done"
    assert len(captured) == 1 and captured[0][1:3] == ["pip", "install"]


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

    async def _fake_sources(_sources):
        return ["github"]

    async def _fake_release(*_args, **_kwargs):
        raise AssertionError("an offline deployment must not query GitHub / Gitee for the core version")

    monkeypatch.setattr(updater, "_get_updater_config", _fake_config)
    monkeypatch.setattr(updater, "_resolve_sources_for_edition", _fake_sources)
    monkeypatch.setattr(updater, "get_latest_release", _fake_release)

    info = await updater.check_update()

    assert info.deploy_mode == "offline"
    assert info.update_allowed is False
    assert info.current_version == "2026.9.14"
    assert info.latest_version is None and info.has_update is False and info.error is None

    # the console-manifest (Pro) check still runs: Console is the endpoint such a machine may reach
    async def _console(*_args, **_kwargs):
        return updater.ConsoleManifestRelease(
            version="v2026.9.14", release_notes=None, release_url="https://portal.example/r",
            bundle_url="https://portal.example/b.tar.gz", bundle_sha256=None, bundle_format="tar.gz",
            manifest={"bundle_version": "v2026.9.14", "core_version": "v2026.9.14", "flockspro_component_version": "2026.9.14"},
        )

    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _console)
    pro_info = await updater.check_update(force_console_manifest=True)
    assert pro_info.deploy_mode == "offline" and pro_info.update_allowed is False
    assert pro_info.latest_version is not None


@pytest.mark.parametrize(
    ("deploy_mode", "expect_prebuilt"),
    [("offline", True), ("source", False)],
)
@pytest.mark.asyncio
async def test_pro_downgrade_handoff_is_prebuilt_only_on_offline_installs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    deploy_mode: str,
    expect_prebuilt: bool,
) -> None:
    """An offline install has no index / npm registry: the downgrade restart must not sync or build."""
    import sys
    from types import SimpleNamespace

    from flocks.cli import service_manager
    from flocks.updater import deploy as deploy_mod

    flocks_root = tmp_path / "flocks-root"
    (flocks_root / "run").mkdir(parents=True)
    (flocks_root / "run" / "pro-bundle-installed.json").write_text(
        json.dumps(
            {
                "bundle_version": "v2026.9.14",
                "core_version": "v2026.9.14",
                "flockspro_component_version": "2026.8.12",
                "installed_at": "2026-09-17T08:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    install_root = _prepare_install_root(tmp_path)
    spawned: list[list[str]] = []

    async def fake_uninstall_pro_component(*, uv_path, install_root, env):
        return None

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setenv("FLOCKS_ROOT", str(flocks_root))
    monkeypatch.setattr(deploy_mod, "detect_deploy_mode", lambda: deploy_mode)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.9.14")
    monkeypatch.setattr(updater, "_is_pro_component_installed", lambda: True)
    monkeypatch.setattr(updater, "_find_executable", lambda name: "/opt/flocks/tools/uv/uv" if name == "uv" else None)
    monkeypatch.setattr(updater, "_uninstall_pro_component", fake_uninstall_pro_component)
    monkeypatch.setattr(updater, "_write_version_marker", lambda _version: None)
    monkeypatch.setattr(updater, "_build_restart_argv", lambda _install_root: [sys.executable])
    monkeypatch.setattr(updater, "_handoff_service_config", lambda: service_manager.ServiceConfig())
    monkeypatch.setattr(updater.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        updater,
        "_spawn_restart_handoff",
        lambda argv, *, cwd: spawned.append(list(argv)) or SimpleNamespace(pid=4321),
    )
    monkeypatch.setattr(updater.os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))

    with pytest.raises(SystemExit, match="0"):
        async for _step in updater.perform_pro_bundle_downgrade():
            pass

    assert spawned, "downgrade must spawn the restart handoff"
    assert ("--prebuilt" in spawned[0]) is expect_prebuilt
    assert "--pro-wheel-path" not in spawned[0]


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


# --------------------------------------------------------------------------- #
# R8: a version match alone must not let Console identity overwrite the local bundle
# --------------------------------------------------------------------------- #


def _console_release(manifest: dict, version: str = "v2026.9.14") -> updater.ConsoleManifestRelease:
    return updater.ConsoleManifestRelease(
        version=version,
        release_notes=None,
        release_url="https://portal.example/bundle.tar.gz",
        bundle_url="https://portal.example/bundle.tar.gz",
        bundle_sha256="a" * 64,  # archive digest: never comparable with the local wheel digest
        bundle_format="tar.gz",
        manifest=manifest,
    )


@pytest.mark.asyncio
async def test_local_bundle_keeps_its_own_identity_when_console_release_was_repacked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "opt-bundle"
    _write_local_bundle(bundle_dir, extra_manifest={"build_id": "local-build"})
    install_root = _prepare_install_root(tmp_path)
    _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    monkeypatch.setenv("FLOCKS_PRO_BUNDLE_DIR", str(bundle_dir))
    monkeypatch.setattr(updater, "_download_console_bundle", _no_download)
    marker_path = tmp_path / "flocks-root" / "run" / "pro-bundle-installed.json"

    async def _console_same_version_other_wheel(*_args, **_kwargs):
        return _console_release(
            {
                "release_id": "rel_repacked",
                "bundle_version": "v2026.9.14",
                "core_version": "v2026.9.14",
                "flockspro_component_version": "2026.9.15",
                "build_id": "remote-build",
            }
        )

    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _console_same_version_other_wheel)
    progresses = [step async for step in updater.perform_pro_bundle_install(restart=False)]
    assert progresses[-1].stage == "done", progresses[-1].message
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    # what was physically installed is the local wheel: its identity must be what the marker records
    assert marker["flockspro_component_version"] == "2026.9.14"
    assert marker["build_id"] == "local-build"
    assert marker["release_id"] is None


@pytest.mark.asyncio
async def test_local_bundle_borrows_only_release_ids_from_a_matching_console_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "opt-bundle"
    _write_local_bundle(bundle_dir, extra_manifest={"build_id": "local-build"})
    install_root = _prepare_install_root(tmp_path)
    _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    monkeypatch.setenv("FLOCKS_PRO_BUNDLE_DIR", str(bundle_dir))
    monkeypatch.setattr(updater, "_download_console_bundle", _no_download)
    marker_path = tmp_path / "flocks-root" / "run" / "pro-bundle-installed.json"

    async def _console_matching(*_args, **_kwargs):
        return _console_release(
            {
                "release_id": "rel_match",
                "bundle_release_id": "brel_match",
                "bundle_version": "v2026.9.14",
                "compare_version": "2026.9.14.99",
                "core_version": "2026.9.14",  # same version, only the "v" prefix differs
                "flockspro_component_version": "2026.9.14",
            }
        )

    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _console_matching)
    progresses = [step async for step in updater.perform_pro_bundle_install(restart=False)]
    assert progresses[-1].stage == "done", progresses[-1].message
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["release_id"] == "rel_match"
    assert marker["bundle_release_id"] == "brel_match"
    assert marker["build_id"] == "local-build"
    assert marker["flockspro_component_version"] == "2026.9.14"


def test_console_identity_for_local_bundle_is_ids_only() -> None:
    local = {"core_version": "v2026.9.14", "flockspro_component_version": "2026.9.14"}
    assert updater._console_identity_for_local_bundle(local, None) is None
    assert updater._console_identity_for_local_bundle(local, {"bundle_version": "v2026.9.14"}) is None
    assert updater._console_identity_for_local_bundle(
        local, {"release_id": "r1", "build_id": "remote-only", "flockspro_component_version": "2026.9.14"}
    ) == {"release_id": "r1"}
    assert updater._console_identity_for_local_bundle(
        {**local, "build_id": "b1"}, {"release_id": "r1", "build_id": "b2"}
    ) is None

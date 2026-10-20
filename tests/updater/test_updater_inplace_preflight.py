"""Checks that refuse an in-place upgrade before anything is downloaded or removed."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from flocks.updater import deploy, updater
from flocks.updater.channels import ChannelRelease, InstallPlan
from tests.updater.test_updater_prebuilt_release import (
    _prepare_install_root,
    _stub_install_side_effects,
    _write_component,
)


@pytest.fixture
def upgrade_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    """A writable fake install where reaching the download step is recorded."""
    install_root = tmp_path / "install-root"
    (install_root / ".venv").mkdir(parents=True)
    calls: list[str] = []

    async def fake_get_updater_config():
        return SimpleNamespace(
            archive_format="zip",
            sources=["github"],
            repo="AgentFlocks/Flocks",
            token=None,
            gitee_token=None,
            backup_retain_count=3,
            base_url=None,
            gitee_repo=None,
        )

    async def fake_download_with_fallback(**_kwargs):
        calls.append("download")
        raise RuntimeError("stop after the download started")

    async def fake_download_channel_archive(*_args, **_kwargs):
        calls.append("channel-download")
        raise RuntimeError("stop after the channel download started")

    def fake_mkdtemp(**_kwargs):
        calls.append("mkdtemp")
        path = tmp_path / f"update-{len(calls)}"
        path.mkdir()
        return str(path)

    monkeypatch.setattr(deploy, "detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.9.23")
    monkeypatch.setattr(updater, "_download_with_fallback", fake_download_with_fallback)
    monkeypatch.setattr(updater, "_download_channel_archive", fake_download_channel_archive)
    monkeypatch.setattr(updater.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(updater, "_record_update_journal", lambda _message: None)
    monkeypatch.setattr(updater, "_running_as_systemd_service_main_process", lambda: False)
    monkeypatch.setattr(updater, "_find_executable", lambda _name: "/usr/bin/uv")
    return SimpleNamespace(install_root=install_root, calls=calls)


def _remote_release() -> ChannelRelease:
    """A channel release still to be downloaded; what it installs is only known from its plan."""
    return ChannelRelease(
        version="v2026.10.13",
        plan=lambda root, current, work: InstallPlan(source_root=None, version=current),
        archive_url="https://releases.example.test/bundle-v2026.10.13.zip",
        label="extension bundle",
        own_version_rules=True,
    )


async def _run(*, channel_release: bool = False, **kwargs) -> list:
    release = _remote_release() if channel_release else None
    return [step async for step in updater.perform_update("2026.10.13", release=release, **kwargs)]


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_release", [False, True], ids=["oss-source", "channel-release"])
async def test_missing_uv_fails_before_download(
    monkeypatch: pytest.MonkeyPatch,
    upgrade_env: SimpleNamespace,
    channel_release: bool,
) -> None:
    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setattr(updater, "_find_executable", lambda _name: None)

    progresses = await _run(channel_release=channel_release)

    assert [step.stage for step in progresses] == ["error"]
    assert "uv is required but was not found" in progresses[0].message
    assert upgrade_env.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_release", [False, True], ids=["oss-source", "channel-release"])
async def test_systemd_main_process_is_refused_before_download(
    monkeypatch: pytest.MonkeyPatch,
    upgrade_env: SimpleNamespace,
    channel_release: bool,
) -> None:
    monkeypatch.setattr(updater, "_running_as_systemd_service_main_process", lambda: True)

    progresses = await _run(channel_release=channel_release)

    assert [step.stage for step in progresses] == ["error"]
    assert progresses[0].message == updater.SYSTEMD_MAIN_PROCESS_REFUSED_MESSAGE
    assert upgrade_env.calls == []


@pytest.mark.asyncio
async def test_systemd_main_process_may_still_install_without_restart(
    monkeypatch: pytest.MonkeyPatch,
    upgrade_env: SimpleNamespace,
) -> None:
    """restart=False never exits this process, so systemd cannot kill the work."""
    monkeypatch.setattr(updater, "_running_as_systemd_service_main_process", lambda: True)

    progresses = await _run(restart=False)

    assert progresses[0].stage == "fetching"
    assert upgrade_env.calls == ["mkdtemp", "download"]


posix_permissions = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
not_root = pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores permission bits")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("read_only", "channel_release"),
    [
        pytest.param("install_root", False, id="source-upgrade-read-only-root"),
        pytest.param("venv", False, id="source-upgrade-read-only-venv"),
        pytest.param("venv", True, id="channel-release-read-only-venv"),
    ],
)
@posix_permissions
@not_root
async def test_read_only_install_is_refused_before_download(
    upgrade_env: SimpleNamespace,
    read_only: str,
    channel_release: bool,
) -> None:
    target = upgrade_env.install_root if read_only == "install_root" else upgrade_env.install_root / ".venv"
    target.chmod(0o555)
    try:
        progresses = await _run(channel_release=channel_release)
    finally:
        target.chmod(0o755)

    assert [step.stage for step in progresses] == ["error"]
    assert str(target) in progresses[0].message
    assert "无法就地升级" in progresses[0].message
    assert upgrade_env.calls == []


@pytest.mark.asyncio
@posix_permissions
@not_root
async def test_channel_release_with_read_only_source_still_reaches_the_download(upgrade_env: SimpleNamespace) -> None:
    """Whether a channel's release touches the source is only known from its plan."""
    upgrade_env.install_root.chmod(0o555)
    try:
        progresses = await _run(channel_release=True)
    finally:
        upgrade_env.install_root.chmod(0o755)

    assert progresses[-1].stage == "error"
    assert "channel download started" in progresses[-1].message
    assert upgrade_env.calls == ["mkdtemp", "channel-download"]


def _local_release(tmp_path: Path, *, replaces_source: bool) -> ChannelRelease:
    """An offline release on disk: a component wheel, and optionally a new source tree."""
    release_dir = tmp_path / "opt-release"
    wheel = _write_component(release_dir)
    if replaces_source:
        source = release_dir / "flocks"
        source.mkdir()
        (source / "pyproject.toml").write_text('[project]\nname = "flocks"\n', encoding="utf-8")
        (source / "new_core.py").write_text("UPDATED = True\n", encoding="utf-8")

    def _plan(content_root: Path, current_version: str, work_dir: Path) -> InstallPlan:
        return InstallPlan(
            source_root=content_root / "flocks" if replaces_source else None,
            version="2026.10.1" if replaces_source else current_version,
            extra_wheels=(content_root / "wheels" / wheel.name,),
            prebuilt=True,
        )

    return ChannelRelease(
        version="v2026.10.1",
        plan=_plan,
        archive_path=release_dir,
        label="extension bundle",
        own_version_rules=True,
    )


@pytest.mark.asyncio
@posix_permissions
@not_root
async def test_component_only_release_installs_when_only_the_venv_is_writable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root = _prepare_install_root(tmp_path)
    captured = _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    monkeypatch.setattr(deploy, "detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(updater, "_write_version_marker", lambda _version: None)
    release = _local_release(tmp_path, replaces_source=False)

    install_root.chmod(0o555)
    try:
        progresses = [step async for step in updater.perform_update("v2026.10.1", release=release, restart=False)]
    finally:
        install_root.chmod(0o755)

    assert progresses[-1].stage == "done", progresses[-1].message
    assert len(captured) == 1
    assert captured[0][:3] == ["/opt/flocks/tools/uv/uv", "pip", "install"]
    assert captured[0][-1].endswith("component-2026.9.14-py3-none-any.whl")


@pytest.mark.asyncio
@posix_permissions
@not_root
async def test_release_replacing_the_source_is_refused_before_backup_when_the_root_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root = _prepare_install_root(tmp_path)
    captured = _stub_install_side_effects(monkeypatch, tmp_path, install_root)
    monkeypatch.setattr(deploy, "detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(updater, "_running_as_systemd_service_main_process", lambda: False)
    backups: list[str] = []
    handoffs: list[list[str]] = []
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_a, **_k: backups.append("backup") or None)
    monkeypatch.setattr(updater, "_spawn_restart_handoff", lambda argv, **_k: handoffs.append(argv))
    release = _local_release(tmp_path, replaces_source=True)

    install_root.chmod(0o555)
    try:
        progresses = [step async for step in updater.perform_update("v2026.10.1", release=release, restart=True)]
    finally:
        install_root.chmod(0o755)

    assert progresses[-1].stage == "error"
    assert str(install_root) in progresses[-1].message
    assert "无法就地升级" in progresses[-1].message
    assert backups == [] and handoffs == [] and captured == []
    assert not (install_root / "new_core.py").exists()


@pytest.mark.asyncio
async def test_writable_install_with_uv_reaches_download(upgrade_env: SimpleNamespace) -> None:
    progresses = await _run()

    assert [step.stage for step in progresses] == ["fetching", "error"]
    assert upgrade_env.calls == ["mkdtemp", "download"]


@pytest.mark.asyncio
async def test_removing_components_refuses_systemd_main_process_before_uninstalling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    uninstalled: list[list[str]] = []

    async def fake_uninstall(packages, **_kwargs):
        uninstalled.append(list(packages))
        return None

    monkeypatch.setattr(deploy, "detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.10.13")
    monkeypatch.setattr(updater, "_find_executable", lambda _name: "/usr/bin/uv")
    monkeypatch.setattr(updater, "_uninstall_packages", fake_uninstall)
    monkeypatch.setattr(updater, "_running_as_systemd_service_main_process", lambda: True)

    progresses = [
        step async for step in updater.remove_components(["extension-component"], version="2026.10.13", restart=True)
    ]

    assert progresses[-1].stage == "error"
    assert progresses[-1].message == updater.SYSTEMD_MAIN_PROCESS_REFUSED_MESSAGE
    assert uninstalled == []


def _fake_proc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, parent_pid: int, parent_name: str | None) -> None:
    proc = tmp_path / "proc"
    if parent_name is not None:
        (proc / str(parent_pid)).mkdir(parents=True)
        (proc / str(parent_pid) / "comm").write_text(f"{parent_name}\n", encoding="utf-8")
    monkeypatch.setattr(updater, "_PROC_ROOT", proc)
    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setattr(updater.os, "getppid", lambda: parent_pid)


@pytest.mark.parametrize(
    ("invocation_id", "parent_pid", "parent_name", "expected"),
    [
        pytest.param("abc123", 1, None, True, id="system-unit-main-process"),
        pytest.param("abc123", 4321, "systemd", True, id="user-unit-main-process"),
        pytest.param("abc123", 4321, "python3", False, id="backend-under-flocks-supervisor"),
        pytest.param("abc123", 4321, None, False, id="parent-already-gone"),
        pytest.param(None, 1, None, False, id="not-started-by-systemd"),
    ],
)
def test_systemd_main_process_detection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invocation_id: str | None,
    parent_pid: int,
    parent_name: str | None,
    expected: bool,
) -> None:
    _fake_proc(monkeypatch, tmp_path, parent_pid=parent_pid, parent_name=parent_name)
    if invocation_id is None:
        monkeypatch.delenv("INVOCATION_ID", raising=False)
    else:
        monkeypatch.setenv("INVOCATION_ID", invocation_id)

    assert updater._running_as_systemd_service_main_process() is expected


def test_systemd_main_process_detection_is_linux_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_proc(monkeypatch, tmp_path, parent_pid=1, parent_name=None)
    monkeypatch.setenv("INVOCATION_ID", "abc123")
    monkeypatch.setattr(updater.sys, "platform", "darwin")

    assert updater._running_as_systemd_service_main_process() is False

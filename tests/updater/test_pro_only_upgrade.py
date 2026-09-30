"""Pro upgrades preserve the deployed branch, including its built UI and Hub.

Only the package installer and network boundary are simulated. Bundle staging,
archive extraction, install dispatch, handoff arguments and markers are real.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from flocks.cli.service_config import ServiceConfig
from flocks.config.config import UpdaterConfig
from flocks.updater import deploy, restart_handoff, updater


CORE_VERSION = "2026.9.23"
PRO_VERSION = "2026.10.1"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _forbidden(*_args, **_kwargs):
    raise AssertionError("A Pro-only update touched the core, frontend or shared runtime")


@pytest.fixture
def deployed_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "deployed-branch"
    runtime = tmp_path / "user-runtime"
    files = {
        "pyproject.toml": f'[project]\nname = "flocks"\nversion = "{CORE_VERSION}"\n',
        "flocks/monitoring/runtime.py": "BRANCH_MONITOR = True\n",
        "webui/package.json": '{"scripts":{"build":"must-not-run"}}',
        "webui/src/pages/SecurityMonitor.tsx": "branch monitor page",
        "webui/dist/index.html": "<html>deployed branch build</html>",
        ".flocks/flockshub/index.json": '{"components":["host-security-monitor"]}',
        ".flocks/flockshub/plugins/components/host-security-monitor/manifest.json": '{"version":"branch"}',
        ".git/HEAD": "ref: refs/heads/codex/security-operations-monitor\n",
        "local-branch-note.txt": "uncommitted local source must remain",
    }
    for name, content in files.items():
        _write(root / name, content)
    _write(updater._venv_python_path(root), "fake runtime: never execute")
    _write(runtime / "plugins" / "installed.json", '{"monitor":"installed"}')
    _write(runtime / "version" / ".current_version", CORE_VERSION + "\n")
    previous_marker = {
        "bundle_version": "v2026.9.22",
        "core_version": "v2026.9.22",
        "flockspro_component_version": "2026.9.22",
        "installed_at": "2026-09-22T00:00:00+00:00",
    }
    marker = runtime / "run" / "pro-bundle-installed.json"
    _write(marker, json.dumps(previous_marker))

    async def _config():
        return UpdaterConfig()

    calls: list[list[str]] = []

    async def _package_install(command, **_kwargs):
        # Fail on any unexpected subprocess instead of allowing uv/npm to run.
        assert command[:3] == ["test-uv", "pip", "install"]
        assert "--no-deps" in command
        assert command[command.index("--python") + 1] == str(updater._venv_python_path(root))
        wheel = Path(command[-1])
        with zipfile.ZipFile(wheel) as archive:
            metadata = archive.read(f"flockspro-{PRO_VERSION}.dist-info/METADATA").decode()
        assert "Name: flockspro\n" in metadata
        calls.append(list(command))
        return 0, "installed simulated Pro wheel", ""

    monkeypatch.setenv("FLOCKS_ROOT", str(runtime))
    monkeypatch.delenv("FLOCKS_PRO_BUNDLE_DIR", raising=False)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: root)
    monkeypatch.setattr(updater, "_VERSION_MARKER_PATH", runtime / "version" / ".current_version")
    monkeypatch.setattr(updater, "_get_updater_config", _config)
    monkeypatch.setattr(updater, "_find_executable", lambda name: "test-uv" if name == "uv" else None)
    monkeypatch.setattr(updater, "_run_async", _package_install)
    monkeypatch.setattr(updater, "_read_local_pro_license_id", lambda: None)
    monkeypatch.setattr(updater, "_record_update_journal", lambda _message: None)
    monkeypatch.setattr(deploy, "detect_deploy_mode", lambda: "source")
    for name in (
        "_sync_project_dependencies", "_sync_prebuilt_dependencies", "_build_frontend_workspace",
        "_refresh_global_cli_entry", "_write_version_marker", "_backup_current_version", "_replace_install_dir",
    ):
        monkeypatch.setattr(updater, name, _forbidden)
    return SimpleNamespace(root=root, runtime=runtime, marker=marker, calls=calls, before=_snapshot(root))


def _bundle(path: Path, core_version: str, *, prebuilt: bool) -> tuple[Path, dict]:
    wheel = path / "wheels" / f"flockspro-{PRO_VERSION}-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("flockspro/__init__.py", "# Test-only Pro distribution\n")
        archive.writestr(
            f"flockspro-{PRO_VERSION}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: flockspro\nVersion: {PRO_VERSION}\n",
        )
        archive.writestr(
            f"flockspro-{PRO_VERSION}.dist-info/WHEEL",
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(f"flockspro-{PRO_VERSION}.dist-info/RECORD", "")
    manifest = {
        "bundle_version": f"v{PRO_VERSION}",
        "core_version": f"v{core_version}",
        "flockspro_component_version": PRO_VERSION,
        "flockspro_wheel": str(wheel.relative_to(path)),
        "prebuilt": prebuilt,
        "dependency_wheels_dir": "dependencies",
        "build_id": "test-pro-only",
    }
    _write(path / "manifest.json", json.dumps(manifest))
    _write(path / "dependencies" / "flocks-replacement.whl", "do not install core dependencies")
    _write(path / "flocks" / "pyproject.toml", f'[project]\nname = "flocks"\nversion = "{core_version}"\n')
    _write(path / "flocks" / "flocks" / "official.py", "release-only core")
    _write(path / "flocks" / "webui" / "dist" / "index.html", "<html>release UI</html>")
    _write(path / "flocks" / ".flocks" / "flockshub" / "index.json", '{"components":[]}')
    return wheel, manifest


def _mock_console(monkeypatch: pytest.MonkeyPatch, bundle: Path, manifest: dict):
    archive_path = bundle.parent / "console-pro.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path in bundle.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(bundle))

    async def _manifest(*_args, **_kwargs):
        return updater.ConsoleManifestRelease(
            version=manifest["bundle_version"], release_notes=None, release_url=None,
            bundle_url="https://console.invalid/pro.zip", bundle_format="zip",
            bundle_sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            manifest=manifest, console_session_token="test-token",
        )

    async def _download(_url, _token, directory, filename, **_kwargs):
        target = directory / filename
        shutil.copyfile(archive_path, target)
        return target

    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _manifest)
    monkeypatch.setattr(updater, "_download_console_bundle", _download)


@pytest.mark.asyncio
@pytest.mark.parametrize("core_version", ["2026.9.22", CORE_VERSION, "2026.10.2"], ids=["older", "same", "newer"])
@pytest.mark.parametrize("prebuilt", [False, True], ids=["source-bundle", "prebuilt-bundle"])
@pytest.mark.parametrize("source", ["local", "console"])
async def test_pro_bundle_preserves_entire_deployed_branch(
    tmp_path, monkeypatch, deployed_branch, core_version, prebuilt, source,
):
    branch = deployed_branch
    bundle = tmp_path / "bundle"
    _wheel, manifest = _bundle(bundle, core_version, prebuilt=prebuilt)
    _mock_console(monkeypatch, bundle, manifest)
    if source == "local":
        monkeypatch.setattr(updater, "_download_console_bundle", _forbidden)
        steps = [step async for step in updater.perform_pro_bundle_install(restart=False, local_bundle_dir=bundle)]
    else:
        steps = [step async for step in updater.perform_pro_bundle_install(restart=False)]

    assert steps[-1].stage == "done", [step.message for step in steps]
    assert len(branch.calls) == 1
    assert _snapshot(branch.root) == branch.before
    assert (branch.runtime / "version" / ".current_version").read_text() == CORE_VERSION + "\n"
    assert (branch.runtime / "plugins" / "installed.json").read_text() == '{"monitor":"installed"}'
    installed = json.loads(branch.marker.read_text())
    assert installed["core_version"] == f"v{CORE_VERSION}"
    assert installed["bundle_version"] == f"v{PRO_VERSION}"
    assert installed["flockspro_component_version"] == PRO_VERSION
    assert all(step.stage != "backing_up" for step in steps)


@pytest.mark.asyncio
@pytest.mark.parametrize("prebuilt", [False, True])
async def test_pro_only_install_does_not_sync_even_when_dependency_wheels_are_supplied(
    tmp_path, deployed_branch, prebuilt,
):
    branch = deployed_branch
    bundle = tmp_path / "bundle"
    wheel, _manifest = _bundle(bundle, "2026.10.2", prebuilt=prebuilt)
    # A Pro-only install also works without any npm project or prebuilt WebUI.
    shutil.rmtree(branch.root / "webui")
    before = _snapshot(branch.root)
    await updater.install_or_repair_source(
        branch.root, version=CORE_VERSION, uv_path="test-uv", pro_only=True, prebuilt=prebuilt,
        pro_wheel_path=wheel, pro_bundle_manifest_path=bundle / "manifest.json",
        dependency_wheels_dir=bundle / "dependencies",
    )
    assert len(branch.calls) == 1
    assert _snapshot(branch.root) == before
    assert json.loads(branch.marker.read_text())["core_version"] == f"v{CORE_VERSION}"


@pytest.mark.asyncio
async def test_failed_pro_install_preserves_existing_markers_and_branch(tmp_path, monkeypatch, deployed_branch):
    branch = deployed_branch
    bundle = tmp_path / "bundle"
    wheel, _manifest = _bundle(bundle, "2026.10.2", prebuilt=False)
    before_runtime = _snapshot(branch.runtime)

    async def _failed_install(_command, **_kwargs):
        return 1, "", "Pro wheel is incompatible with this runtime"

    monkeypatch.setattr(updater, "_run_async", _failed_install)
    with pytest.raises(RuntimeError, match="Pro wheel is incompatible"):
        await updater.install_or_repair_source(
            branch.root, version=CORE_VERSION, uv_path="test-uv", pro_only=True,
            pro_wheel_path=wheel, pro_bundle_manifest_path=bundle / "manifest.json",
        )
    assert _snapshot(branch.runtime) == before_runtime
    assert _snapshot(branch.root) == branch.before


def _handoff(monkeypatch, branch, bundle, wheel):
    config = ServiceConfig(
        backend_host="0.0.0.0", backend_port=9527,
        frontend_host="0.0.0.0", frontend_port=9527,
        legacy_backend_host="127.0.0.1", legacy_backend_port=9123,
    )
    monkeypatch.setattr(updater, "_handoff_service_config", lambda: config)
    return updater._build_restart_handoff_argv(
        [str(updater._venv_python_path(branch.root)), "-m", "flocks.cli.main", "start"],
        branch.root, uv_path="test-uv", sync_timeout=30,
        version=CORE_VERSION, current_version=CORE_VERSION, pro_only=True,
        pro_wheel_path=wheel, pro_bundle_manifest_path=bundle / "manifest.json", wait_for_parent=False,
    )


def test_pro_handoff_retains_ports_skips_frontend_build_and_installs_only_pro(
    tmp_path, monkeypatch, deployed_branch,
):
    branch = deployed_branch
    bundle = tmp_path / "bundle"
    wheel, _manifest = _bundle(bundle, "2026.10.2", prebuilt=False)
    command = _handoff(monkeypatch, branch, bundle, wheel)
    assert "--pro-only" in command
    assert "--content-root" not in command
    assert "--backup-path" not in command
    args = restart_handoff._parse_args(command[3:])
    assert args.mode == "restart"
    assert args.pro_only is True
    assert "--skip-webui-build" in args.restart_argv
    assert args.restart_argv[args.restart_argv.index("--host") + 1] == "0.0.0.0"
    assert args.restart_argv[args.restart_argv.index("--port") + 1] == "9527"
    assert args.restart_argv[args.restart_argv.index("--server-port") + 1] == "9123"
    # Execute the actual child-process installation dispatch, never its service controls.
    assert restart_handoff._run_upgrade_tasks(args) is None
    assert len(branch.calls) == 1
    assert _snapshot(branch.root) == branch.before
    assert json.loads(branch.marker.read_text())["core_version"] == f"v{CORE_VERSION}"


@pytest.mark.parametrize("conflict", [["--mode", "upgrade"], ["--content-root", "unwanted-core"]])
def test_handoff_parser_rejects_pro_only_source_replacement(tmp_path, monkeypatch, deployed_branch, conflict):
    bundle = tmp_path / "bundle"
    wheel, _manifest = _bundle(bundle, "2026.10.2", prebuilt=True)
    command = _handoff(monkeypatch, deployed_branch, bundle, wheel)[3:]
    delimiter = command.index("--")
    command[delimiter:delimiter] = conflict
    with pytest.raises(SystemExit) as error:
        restart_handoff._parse_args(command)
    assert error.value.code == 2
    assert deployed_branch.calls == []


def test_handoff_builder_rejects_pro_only_source_replacement(deployed_branch):
    with pytest.raises(ValueError, match="Pro-only update cannot replace core source"):
        updater._build_restart_handoff_argv(
            [sys.executable], deployed_branch.root, uv_path="test-uv", sync_timeout=30,
            version=CORE_VERSION, current_version=CORE_VERSION,
            pro_only=True, content_root=deployed_branch.root / "unwanted-core",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["explicit-missing", "explicit-core", "fallback-core-only", "ambiguous-pro"])
async def test_invalid_pro_wheel_selection_never_runs_installer(tmp_path, monkeypatch, deployed_branch, case):
    branch = deployed_branch
    bundle = tmp_path / "bundle"
    wheel, manifest = _bundle(bundle, "2026.10.2", prebuilt=True)
    core_wheel = wheel.parent / "flocks-2026.10.2-py3-none-any.whl"
    core_wheel.write_bytes(wheel.read_bytes())
    if case == "explicit-missing":
        # Even when a valid Pro wheel exists, an explicit missing path is an error.
        manifest["flockspro_wheel"] = "wheels/flockspro-missing-py3-none-any.whl"
    elif case == "explicit-core":
        manifest["flockspro_wheel"] = str(core_wheel.relative_to(bundle))
    else:
        manifest.pop("flockspro_wheel")
        if case == "fallback-core-only":
            wheel.unlink()
        else:
            (wheel.parent / "flockspro-2026.10.2-py3-none-any.whl").write_bytes(wheel.read_bytes())
    _write(bundle / "manifest.json", json.dumps(manifest))
    before_runtime = _snapshot(branch.runtime)
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _forbidden)
    steps = [step async for step in updater.perform_pro_bundle_install(restart=False, local_bundle_dir=bundle)]
    assert steps[-1].stage == "error"
    assert branch.calls == []
    assert _snapshot(branch.runtime) == before_runtime
    assert _snapshot(branch.root) == branch.before


@pytest.mark.asyncio
async def test_legacy_wheel_fallback_ignores_bundled_core_package(tmp_path, monkeypatch, deployed_branch):
    bundle = tmp_path / "bundle"
    wheel, manifest = _bundle(bundle, "2026.10.2", prebuilt=True)
    manifest.pop("flockspro_wheel")
    _write(bundle / "manifest.json", json.dumps(manifest))
    (wheel.parent / "flocks-2026.10.2-py3-none-any.whl").write_bytes(b"must not install core")
    _mock_console(monkeypatch, bundle, manifest)
    steps = [step async for step in updater.perform_pro_bundle_install(restart=False, local_bundle_dir=bundle)]
    assert steps[-1].stage == "done", [step.message for step in steps]
    assert len(deployed_branch.calls) == 1
    assert Path(deployed_branch.calls[0][-1]).name == wheel.name
    assert _snapshot(deployed_branch.root) == deployed_branch.before


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [True, False], ids=["missing-wheel", "core-wheel"])
async def test_pro_only_installer_defends_against_invalid_direct_wheel(tmp_path, deployed_branch, missing):
    branch = deployed_branch
    bundle = tmp_path / "bundle"
    wheel, _manifest = _bundle(bundle, "2026.10.2", prebuilt=False)
    if missing:
        wheel.unlink()
    else:
        core_wheel = wheel.with_name("flocks-2026.10.2-py3-none-any.whl")
        wheel.rename(core_wheel)
        wheel = core_wheel
    before_runtime = _snapshot(branch.runtime)
    with pytest.raises(RuntimeError):
        await updater.install_or_repair_source(
            branch.root, version=CORE_VERSION, uv_path="test-uv", pro_only=True,
            pro_wheel_path=wheel, pro_bundle_manifest_path=bundle / "manifest.json",
        )
    assert branch.calls == []
    assert _snapshot(branch.runtime) == before_runtime
    assert _snapshot(branch.root) == branch.before


@pytest.mark.asyncio
async def test_direct_console_update_handoff_keeps_pro_only_across_child_boundary(
    tmp_path, monkeypatch, deployed_branch,
):
    """The generic edition=flockspro endpoint also uses the guarded child path."""
    branch = deployed_branch
    bundle = tmp_path / "bundle"
    wheel, manifest = _bundle(bundle, "2026.10.2", prebuilt=True)
    _mock_console(monkeypatch, bundle, manifest)
    # Configure the real argv builder with the deployment's existing port.
    _handoff(monkeypatch, branch, bundle, wheel)
    handed_off = []

    def _fake_spawn(command, *, cwd):
        assert cwd == branch.root
        args = restart_handoff._parse_args(command[3:])
        handed_off.append(args)

        def _wait():
            # perform_update waits in a worker thread, just like a detached child;
            # no daemon stop/start or actual subprocess is needed for this boundary.
            try:
                return 0 if restart_handoff._run_upgrade_tasks(args) is None else 1
            finally:
                shutil.rmtree(args.cleanup_dir)

        return SimpleNamespace(wait=_wait)

    monkeypatch.setattr(updater, "_spawn_restart_handoff", _fake_spawn)
    steps = [step async for step in updater.perform_update(
        manifest["bundle_version"], force_console_manifest=True, restart=True, wait_for_handoff=True,
    )]
    assert steps[-1].stage == "done", [step.message for step in steps]
    assert len(handed_off) == 1
    assert handed_off[0].mode == "restart"
    assert handed_off[0].pro_only is True
    assert handed_off[0].content_root is None
    assert handed_off[0].backup_path is None
    assert handed_off[0].dependency_wheels_dir is None
    assert "--skip-webui-build" in handed_off[0].restart_argv
    assert len(branch.calls) == 1
    assert _snapshot(branch.root) == branch.before
    assert json.loads(branch.marker.read_text())["core_version"] == f"v{CORE_VERSION}"


@pytest.mark.asyncio
async def test_newer_bundled_core_alone_does_not_offer_pro_update(tmp_path, monkeypatch, deployed_branch):
    bundle = tmp_path / "bundle"
    _wheel, manifest = _bundle(bundle, "2026.10.2", prebuilt=True)
    installed = json.loads(deployed_branch.marker.read_text())
    manifest["bundle_version"] = installed["bundle_version"]
    manifest["flockspro_component_version"] = installed["flockspro_component_version"]
    _mock_console(monkeypatch, bundle, manifest)

    info = await updater.check_update(force_console_manifest=True)

    assert info.has_update is False
    assert info.current_core_version == f"v{CORE_VERSION}"
    assert info.latest_core_version == "v2026.10.2"  # Release metadata only.
    assert deployed_branch.calls == []

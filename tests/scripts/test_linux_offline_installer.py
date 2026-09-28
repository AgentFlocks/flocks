"""Static and dry-run checks for the Linux offline installer (packaging/linux)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGING_DIR = REPO_ROOT / "packaging" / "linux"
INSTALLER_DIR = PACKAGING_DIR / "installer"
BUILD_SCRIPT = PACKAGING_DIR / "build-offline-run.sh"
INSTALL_SCRIPT = INSTALLER_DIR / "install.sh"
WRAPPER_SCRIPT = INSTALLER_DIR / "flocks-cli-wrapper.sh"
SMOKE_SCRIPT = PACKAGING_DIR / "smoke-test.sh"
VERIFY_SCRIPT = PACKAGING_DIR / "verify-install.sh"
DOCKER_BUILD_SCRIPT = PACKAGING_DIR / "build-in-docker.sh"
EXPORT_SCRIPT = PACKAGING_DIR / "export-source.sh"
ENV_TEMPLATE = INSTALLER_DIR / "flocks.env.template"
UNIT_TEMPLATE = INSTALLER_DIR / "flocks.service"
MIGRATE_HELPER = INSTALLER_DIR / "migrate_data.py"
INSTALLER_FILES = ("install.sh", "migrate_data.py", "flocks.service", "flocks.env.template", "flocks-cli-wrapper.sh")
MANIFEST = PACKAGING_DIR / "versions.manifest.json"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "linux-offline-run.yml"

SHELL_SCRIPTS = [BUILD_SCRIPT, INSTALL_SCRIPT, WRAPPER_SCRIPT, SMOKE_SCRIPT, VERIFY_SCRIPT, DOCKER_BUILD_SCRIPT, EXPORT_SCRIPT]
GIT = shutil.which("git")
BASH = shutil.which("bash")


@pytest.fixture(autouse=True)
def _no_previous_flocks_data_from_this_machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """install.sh reads /etc/flocks/flocks.env and looks for a previous installation's data
    (FLOCKS_ROOT, ~/.flocks of local users); tests must never pick up the machine's own install."""
    monkeypatch.setenv("FLOCKS_OFFLINE_HOME_CANDIDATES", "")
    monkeypatch.setenv("FLOCKS_OFFLINE_ENV_FILE", str(tmp_path / "no-such-flocks.env"))
    for name in ("FLOCKS_ROOT", "FLOCKS_OFFLINE_MIGRATE_FROM", "SUDO_USER"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.skipif(BASH is None, reason="bash is required for syntax checks")
@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_shell_scripts_parse(script: Path) -> None:
    completed = subprocess.run([BASH, "-n", str(script)], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_shell_scripts_are_executable_and_strict(script: Path) -> None:
    assert script.stat().st_mode & stat.S_IXUSR, f"{script.name} must be executable"
    text = script.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n")
    assert re.search(r"^set -E?euo pipefail$", text, re.MULTILINE), f"{script.name} must run under set -euo pipefail"


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_shell_scripts_never_recursively_delete(script: Path) -> None:
    """Installer and builder must park old directories, never destroy them."""
    text = script.read_text(encoding="utf-8")
    assert re.search(r"\brm\s+-[a-zA-Z]*r", text) is None, f"{script.name} contains a recursive rm"
    assert "find " not in text or "-delete" not in text


def test_installer_parks_old_program_dirs_and_keeps_data() -> None:
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert '.bak-$STAMP' in text
    assert 'chown -R "$SERVICE_USER:$SERVICE_USER" "$REPO_DIR"' in text
    assert "/var/lib/flocks" in text
    # install.sh only creates/chowns the data root; migrate_data.py renames it only while it holds no user data
    assert 'mv "$DATA_ROOT' not in text
    assert 'mv "$DATA_HOME' not in text


def test_installer_covers_manual_requirements() -> None:
    """Everything the customer manual promises must be done by the installer itself."""
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert 'firewall-cmd --permanent --zone="$zone" --add-port' in text  # http://<ip>:5173 reachable, also after reload
    assert "restorecon -R" in text  # SELinux contexts after moving out of the staging dir
    assert "systemctl enable flocks" in text  # survives reboot
    assert "chronyd" in text  # JWT/OIDC need a sane clock
    assert "/api/health" in text  # only exits after the WebUI answers
    assert "安装完成：http://" in text  # unambiguous last line
    assert 'useradd --system' in text
    assert "install_profile.json" in text and '"Language": "zh-CN"' in text


def test_env_template_pins_offline_behaviour() -> None:
    text = ENV_TEMPLATE.read_text(encoding="utf-8")
    required = {
        "HOME": "@DATA_HOME@",
        "FLOCKS_ROOT": "@DATA_HOME@/.flocks",
        "FLOCKS_INSTALL_ROOT": "@INSTALL_ROOT@",
        "FLOCKS_REPO_ROOT": "@INSTALL_ROOT@/flocks",
        "FLOCKS_NODE_HOME": "@INSTALL_ROOT@/tools/node",
        "FLOCKS_DEPLOY_MODE": "offline",
        "FLOCKS_OFFLINE_INSTALL": "1",
        "FLOCKS_HOST": "0.0.0.0",
        "FLOCKS_PORT": "@PORT@",
        "FLOCKS_CONSOLE_BASE_URL": "https://portalflocks.threatbook.cn",
        "FLOCKS_UPDATE_CHANNEL": "@UPDATE_CHANNEL@",
        "FLOCKS_PRO_BUNDLE_DIR": "@INSTALL_ROOT@/bundle",
        "UV_OFFLINE": "1",
        "UV_NO_PYTHON_DOWNLOADS": "1",
        "UV_CACHE_DIR": "@INSTALL_ROOT@/cache/uv",
        "npm_config_offline": "true",
        "LANG": "C.UTF-8",
    }
    values = {}
    for line in text.splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    for key, expected in required.items():
        assert values.get(key) == expected, f"{key}={values.get(key)!r}, expected {expected!r}"
    assert values["PATH"].startswith("@INSTALL_ROOT@/flocks/.venv/bin:")
    assert "@INSTALL_ROOT@/tools/uv" in values["PATH"]


def test_systemd_unit_matches_updater_restart_model() -> None:
    text = UNIT_TEMPLATE.read_text(encoding="utf-8")
    assert "Type=oneshot" in text
    assert "RemainAfterExit=yes" in text
    assert "User=@SERVICE_USER@" in text
    assert "EnvironmentFile=@ENV_FILE@" in text
    assert "ExecStart=@INSTALL_ROOT@/flocks/.venv/bin/flocks start --no-browser --skip-webui-build" in text
    assert "ExecStop=@INSTALL_ROOT@/flocks/.venv/bin/flocks stop" in text
    assert "WantedBy=multi-user.target" in text
    assert "Restart=" not in text, "oneshot units must not auto-restart; the supervisor owns the backend"


def test_versions_manifest_is_complete() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["install_root"] == "/opt/flocks"
    assert data["python"]["version"].startswith("3.12.")
    assert data["python"]["python_build_standalone_release"].isdigit()
    assert int(data["nodejs"]["version"].split(".")[0]) >= 22
    assert data["uv"]["version"]
    assert data["makeself"]["version"]
    for arch in ("x86_64", "aarch64"):
        assert data["arch"][arch]["triple"]
        assert data["arch"][arch]["node_arch"]
    for key in ("python", "nodejs", "uv"):
        assert "{" in data[key]["archive_template"]


def test_build_script_exports_sources_from_git_not_the_raw_checkout() -> None:
    """R1: a developer checkout may hold .env / .secret.json / logs; only Git content may ship."""
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert 'export-source.sh" "$REPO_ROOT"' in text  # git export for a checkout
    assert 'export-source.sh" --verify-only' in text  # deny-list check for a pre-exported tree
    # a raw copy is only allowed for a tree that export-source.sh already produced
    raw_copy_guard = 'if [[ -f "$REPO_ROOT/.source-export.json" && ! -e "$REPO_ROOT/.git" ]]; then'
    assert raw_copy_guard in text
    assert text.index(raw_copy_guard) < text.index('tar -C "$REPO_ROOT" -cf -')
    assert "--allow-dirty" in text
    assert '"source_dirty"' in text
    docker_text = DOCKER_BUILD_SCRIPT.read_text(encoding="utf-8")
    assert 'export-source.sh" "$REPO_ROOT" "$src_export"' in docker_text  # exported on the host
    assert '-v "$src_export:/src:ro"' in docker_text  # only the exported tree enters the container
    assert '-v "$REPO_ROOT:/src:ro"' not in docker_text


def _make_repo_with_local_secrets(root: Path) -> Path:
    repo = root / "repo"
    (repo / "flocks").mkdir(parents=True)
    (repo / ".flocks").mkdir()
    (repo / "logs").mkdir()
    (repo / "tests").mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "flocks"\n', encoding="utf-8")
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (repo / "flocks" / "__init__.py").write_text("VERSION = 1\n", encoding="utf-8")
    (repo / ".flocks" / ".secret.json.example").write_text("{}\n", encoding="utf-8")
    (repo / "tests" / "test_x.py").write_text("def test(): pass\n", encoding="utf-8")
    # nested directories that share a name with the top-level dev-only dirs: they DO ship
    (repo / "assets").mkdir()
    (repo / "assets" / "top.png").write_bytes(b"top-level asset, dev only")
    (repo / ".flocks" / "plugins" / "skills" / "demo" / "assets").mkdir(parents=True)
    (repo / ".flocks" / "plugins" / "skills" / "demo" / "assets" / "template.md").write_text("nested asset\n", encoding="utf-8")
    (repo / "flocks" / "tool" / "tests").mkdir(parents=True)
    (repo / "flocks" / "tool" / "tests" / "fixture.json").write_text("{}\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".env\n.flocks/.secret.json\nlogs/\n", encoding="utf-8")
    # local, ignored, must never ship
    (repo / ".env").write_text("FAKE_API_KEY=not-a-real-key\n", encoding="utf-8")  # secret-guard: allow (placeholder fixture)
    (repo / ".flocks" / ".secret.json").write_text('{"server_api_token": "FAKE"}\n', encoding="utf-8")
    (repo / "logs" / "backend.log").write_text("fake log line\n", encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run([GIT, "init", "-q"], cwd=repo, check=True, env=env)
    subprocess.run([GIT, "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run([GIT, "commit", "-qm", "init"], cwd=repo, check=True, env=env)
    return repo


def _exported_files(dest: Path) -> set[str]:
    return {str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()}


@pytest.mark.skipif(BASH is None or GIT is None or sys.platform == "win32", reason="requires bash and git")
def test_export_source_never_ships_local_secrets_or_logs(tmp_path: Path) -> None:
    repo = _make_repo_with_local_secrets(tmp_path)
    (repo / "NOTES.md").write_text("untracked scratch\n", encoding="utf-8")

    clean = subprocess.run([BASH, str(EXPORT_SCRIPT), str(repo), str(tmp_path / "clean")], capture_output=True, text=True)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert "mode=clean" in clean.stdout and "dirty=0" in clean.stdout
    clean_files = _exported_files(tmp_path / "clean")
    assert {"pyproject.toml", "uv.lock", "flocks/__init__.py", ".flocks/.secret.json.example", ".gitignore"} <= clean_files
    assert not {".env", ".flocks/.secret.json", "logs/backend.log", "NOTES.md", "tests/test_x.py"} & clean_files

    dirty = subprocess.run([BASH, str(EXPORT_SCRIPT), str(repo), str(tmp_path / "dirty"), "--allow-dirty"], capture_output=True, text=True)
    assert dirty.returncode == 0, dirty.stdout + dirty.stderr
    assert "mode=dirty" in dirty.stdout and "dirty=1" in dirty.stdout
    dirty_files = _exported_files(tmp_path / "dirty")
    assert "NOTES.md" in dirty_files  # untracked but not ignored: included on request
    assert not {".env", ".flocks/.secret.json", "logs/backend.log", "tests/test_x.py"} & dirty_files

    stamp = json.loads((tmp_path / "clean" / ".source-export.json").read_text(encoding="utf-8"))
    assert stamp["mode"] == "clean" and stamp["dirty"] == 0 and stamp["commit"]
    verify = subprocess.run([BASH, str(EXPORT_SCRIPT), "--verify-only", str(tmp_path / "clean")], capture_output=True, text=True)
    assert verify.returncode == 0, verify.stdout + verify.stderr
    (tmp_path / "clean" / ".env").write_text("LEAK=1\n", encoding="utf-8")
    verify = subprocess.run([BASH, str(EXPORT_SCRIPT), "--verify-only", str(tmp_path / "clean")], capture_output=True, text=True)
    assert verify.returncode == 1 and ".env" in verify.stderr


@pytest.mark.skipif(BASH is None or GIT is None or sys.platform == "win32", reason="requires bash and git")
def test_export_source_keeps_nested_dirs_that_share_a_dev_dir_name(tmp_path: Path) -> None:
    """Exclusions are anchored at the repo root: nested assets/ and tests/ dirs ship, top-level ones do not.

    (tar --exclude patterns are unanchored in bsdtar; the first export from a macOS host silently
    dropped all 279 nested hub-skill assets/ files.)
    """
    repo = _make_repo_with_local_secrets(tmp_path)
    for mode, extra in (("clean", []), ("dirty", ["--allow-dirty"])):
        dest = tmp_path / f"out-{mode}"
        completed = subprocess.run([BASH, str(EXPORT_SCRIPT), str(repo), str(dest), *extra], capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
        files = _exported_files(dest)
        assert ".flocks/plugins/skills/demo/assets/template.md" in files, (mode, sorted(files))
        assert "flocks/tool/tests/fixture.json" in files, (mode, sorted(files))
        assert "assets/top.png" not in files
        assert "tests/test_x.py" not in files
        assert "files=%d" % len(files - {".source-export.json"}) in completed.stdout, completed.stdout


@pytest.mark.skipif(BASH is None or GIT is None or sys.platform == "win32", reason="requires bash and git")
def test_export_source_dirty_mode_survives_a_deleted_last_entry(tmp_path: Path) -> None:
    """A tracked file deleted from the working tree (no git rm) is skipped, even when it is the last listed path."""
    repo = _make_repo_with_local_secrets(tmp_path)
    (repo / "zz_last.txt").write_text("gone soon\n", encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run([GIT, "add", "zz_last.txt"], cwd=repo, check=True, env=env)
    subprocess.run([GIT, "commit", "-qm", "last"], cwd=repo, check=True, env=env)
    (repo / "zz_last.txt").unlink()
    dest = tmp_path / "out"
    completed = subprocess.run([BASH, str(EXPORT_SCRIPT), str(repo), str(dest), "--allow-dirty"], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    files = _exported_files(dest)
    assert "zz_last.txt" not in files and "flocks/__init__.py" in files


@pytest.mark.skipif(BASH is None or GIT is None or sys.platform == "win32", reason="requires bash and git")
def test_export_source_refuses_uncommitted_changes_and_tracked_secrets(tmp_path: Path) -> None:
    repo = _make_repo_with_local_secrets(tmp_path)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    (repo / "flocks" / "__init__.py").write_text("VERSION = 2\n", encoding="utf-8")
    modified = subprocess.run([BASH, str(EXPORT_SCRIPT), str(repo), str(tmp_path / "out1")], capture_output=True, text=True)
    assert modified.returncode == 1
    assert "uncommitted" in modified.stderr

    subprocess.run([GIT, "add", "-f", ".env", "flocks/__init__.py"], cwd=repo, check=True, env=env)  # secret-guard: allow (fixture tracks a fake .env on purpose)
    subprocess.run([GIT, "commit", "-qm", "oops"], cwd=repo, check=True, env=env)
    tracked_env_result = subprocess.run([BASH, str(EXPORT_SCRIPT), str(repo), str(tmp_path / "out2")], capture_output=True, text=True)
    assert tracked_env_result.returncode == 1
    assert "refusing to ship" in tracked_env_result.stderr and ".env" in tracked_env_result.stderr


def test_build_script_bakes_channel_and_prebuilt_manifest() -> None:
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert 'UPDATE_CHANNEL="flockspro-offline-${CORE_VERSION_PLAIN}"' in text
    assert '"prebuilt": True' in text
    assert "--frozen" in text and "--no-python-downloads" in text
    assert '--target "$INSTALL_ROOT/.staging"' in text
    assert "--needroot" in text
    assert "sha256sum" in text


def test_workflow_builds_inside_centos_stream9_container() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "quay.io/centos/centos:stream9" in text
    assert "packaging/linux/build-offline-run.sh" in text
    assert "flocks-offline.run" in text
    assert "softprops/action-gh-release" in text
    # both architectures on native runners, arch-specific asset names
    assert "ubuntu-24.04-arm" in text and "ubuntu-latest" in text
    assert "flocks-offline-${{ matrix.arch }}.run" in text


def test_one_click_builder_supports_both_architectures() -> None:
    text = DOCKER_BUILD_SCRIPT.read_text(encoding="utf-8")
    assert 'x86_64) platform="linux/amd64"' in text
    assert 'aarch64) platform="linux/arm64"' in text
    assert 'ARCHES="x86_64 aarch64"' in text  # --arch all
    assert "tonistiigi/binfmt" in text  # cross-arch hint
    assert "--repack" in text


def _fake_payload(tmp_path: Path, *, with_bundle: bool = False, core_version: str = "v2026.9.14") -> Path:
    payload = tmp_path / "payload"
    (payload / "installer").mkdir(parents=True)
    for name in INSTALLER_FILES:
        shutil.copy(INSTALLER_DIR / name, payload / "installer" / name)
    python_dir = payload / "tools" / "python" / "bin"
    python_dir.mkdir(parents=True)
    os.symlink(sys.executable, python_dir / "python3")
    (payload / "flocks").mkdir()
    if with_bundle:
        (payload / "bundle" / "wheels").mkdir(parents=True)
        (payload / "bundle" / "wheels" / "flockspro-2026.8.12-py3-none-any.whl").write_bytes(b"new-wheel")
        (payload / "bundle" / "manifest.json").write_text("{}", encoding="utf-8")
    (payload / "versions.json").write_text(
        json.dumps(
            {
                "product": "flocks",
                "core_version": core_version,
                "arch": os.uname().machine,
                "install_root": str(tmp_path / "opt-flocks"),
                "update_channel": "flockspro-offline-2026.9.14",
                "pro_bundle": with_bundle,
            }
        ),
        encoding="utf-8",
    )
    return payload


def _fake_activated_install(tmp_path: Path, *, old_bundle_core: str | None = "v2026.9.14", wheel_sha_ok: bool = True) -> Path:
    """An existing /opt/flocks with an activated Pro marker and (optionally) its bundle."""
    import hashlib

    install_root = tmp_path / "opt-flocks"
    (install_root / "flocks").mkdir(parents=True)
    (install_root / "flocks" / "pyproject.toml").write_text('[project]\nname = "flocks"\n', encoding="utf-8")
    data_home = tmp_path / "data-home"
    (data_home / ".flocks" / "run").mkdir(parents=True)
    (data_home / ".flocks" / "run" / "pro-bundle-installed.json").write_text('{"bundle_version": "v2026.9.14"}', encoding="utf-8")
    if old_bundle_core is not None:
        wheels = install_root / "bundle" / "wheels"
        wheels.mkdir(parents=True)
        wheel = wheels / "flockspro-2026.8.12-py3-none-any.whl"
        wheel.write_bytes(b"old-wheel")
        sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
        (install_root / "bundle" / "manifest.json").write_text(
            json.dumps(
                {
                    "prebuilt": True,
                    "core_version": old_bundle_core,
                    "flockspro_wheel": "wheels/flockspro-2026.8.12-py3-none-any.whl",
                    "bundle_sha256": sha if wheel_sha_ok else "0" * 64,
                }
            ),
            encoding="utf-8",
        )
    return data_home


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _dry_run(payload: Path, data_home: Path | None = None, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    # an unused port so the "port already taken" preflight never trips on a dev machine; the data
    # home always points into the temp dir so the preflight never reads the real /var/lib/flocks
    env = {**os.environ, "FLOCKS_OFFLINE_DRY_RUN": "1", "FLOCKS_OFFLINE_PORT": str(_free_port())}
    env["FLOCKS_OFFLINE_DATA_HOME"] = str(data_home if data_home is not None else payload.parent / "data-home")
    env.update(extra_env or {})
    return subprocess.run([BASH, str(payload / "installer" / "install.sh")], capture_output=True, text=True, env=env, cwd=payload)


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_installer_refuses_to_drop_activated_pro_without_a_compatible_bundle(tmp_path: Path) -> None:
    """R2: a new package without Pro must not silently turn an activated instance into OSS."""
    payload = _fake_payload(tmp_path, with_bundle=False)

    # no previous bundle to carry over → refuse before changing anything
    data_home = _fake_activated_install(tmp_path, old_bundle_core=None)
    result = _dry_run(payload, data_home)
    assert result.returncode == 1
    assert "已激活 Flocks Pro" in result.stderr and "本次未做任何改动" in result.stderr

    # previous bundle built for another core → refuse
    shutil.rmtree(tmp_path / "opt-flocks")
    shutil.rmtree(tmp_path / "data-home")
    data_home = _fake_activated_install(tmp_path, old_bundle_core="v2026.8.17")
    result = _dry_run(payload, data_home)
    assert result.returncode == 1
    assert "与新版本不匹配" in result.stderr

    # previous bundle whose wheel no longer matches its manifest sha256 → refuse
    shutil.rmtree(tmp_path / "opt-flocks")
    shutil.rmtree(tmp_path / "data-home")
    data_home = _fake_activated_install(tmp_path, wheel_sha_ok=False)
    result = _dry_run(payload, data_home)
    assert result.returncode == 1


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_installer_carries_over_compatible_bundle_or_uses_the_new_one(tmp_path: Path) -> None:
    payload = _fake_payload(tmp_path, with_bundle=False)
    data_home = _fake_activated_install(tmp_path, old_bundle_core="v2026.9.14")
    result = _dry_run(payload, data_home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "carry-over-bundle=1" in result.stdout
    assert "沿用当前已激活的 Pro bundle" in result.stdout

    # new package ships its own bundle → nothing to carry over
    shutil.rmtree(tmp_path / "payload")
    payload = _fake_payload(tmp_path, with_bundle=True)
    result = _dry_run(payload, data_home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "carry-over-bundle=0" in result.stdout

    # not activated → nothing to check
    shutil.rmtree(tmp_path / "data-home")
    (tmp_path / "data-home").mkdir()
    result = _dry_run(payload, tmp_path / "data-home")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "carry-over-bundle=0" in result.stdout


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_installer_dry_run_reads_payload_and_exits_cleanly(tmp_path: Path) -> None:
    """Run install.sh with FLOCKS_OFFLINE_DRY_RUN=1 against a fake payload."""
    payload = tmp_path / "payload"
    (payload / "installer").mkdir(parents=True)
    for name in INSTALLER_FILES:
        shutil.copy(INSTALLER_DIR / name, payload / "installer" / name)
    python_dir = payload / "tools" / "python" / "bin"
    python_dir.mkdir(parents=True)
    os.symlink(sys.executable, python_dir / "python3")
    (payload / "flocks").mkdir()
    (payload / "versions.json").write_text(
        json.dumps(
            {
                "product": "flocks",
                "core_version": "v2026.9.14",
                "arch": os.uname().machine,
                "install_root": str(tmp_path / "opt-flocks"),
                "update_channel": "flockspro-offline-2026.9.14",
                "pro_bundle": True,
            }
        ),
        encoding="utf-8",
    )

    env = {**os.environ, "FLOCKS_OFFLINE_DRY_RUN": "1", "FLOCKS_OFFLINE_PORT": str(_free_port()),
           "FLOCKS_OFFLINE_DATA_HOME": str(tmp_path / "data-home")}
    completed = subprocess.run(
        [BASH, str(payload / "installer" / "install.sh")],
        capture_output=True,
        text=True,
        env=env,
        cwd=payload,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "dry-run" in completed.stdout
    assert "flockspro-offline-2026.9.14" not in completed.stderr
    assert "Pro bundle=true" in completed.stdout
    assert not (tmp_path / "opt-flocks").exists(), "dry-run must not create the install root"


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_installer_refuses_foreign_architecture_package(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    (payload / "installer").mkdir(parents=True)
    shutil.copy(INSTALL_SCRIPT, payload / "installer" / "install.sh")
    python_dir = payload / "tools" / "python" / "bin"
    python_dir.mkdir(parents=True)
    os.symlink(sys.executable, python_dir / "python3")
    (payload / "versions.json").write_text(
        json.dumps({"core_version": "v2026.9.14", "arch": "riscv64", "install_root": str(tmp_path / "opt")}),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [BASH, str(payload / "installer" / "install.sh")],
        capture_output=True,
        text=True,
        env={**os.environ, "FLOCKS_OFFLINE_DRY_RUN": "1", "FLOCKS_OFFLINE_DATA_HOME": str(tmp_path / "data-home")},
        cwd=payload,
    )
    assert completed.returncode == 1
    assert "riscv64" in completed.stderr


# --------------------------------------------------------------------------- #
# review round 2: R4 port persistence, R5 function order, R6 password, R7 firewalld
# --------------------------------------------------------------------------- #


def _bash_function_source(script: Path, name: str) -> str:
    """Extract one top-level `name() { ... }` block so it can be unit-tested with bash."""
    text = script.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL)
    assert match, f"{name}() not found in {script.name}"
    return match.group(0)


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_shell_functions_are_defined_before_first_use(script: Path) -> None:
    """R5: bash resolves functions at call time; a call above the definition is exit 127."""
    lines = script.read_text(encoding="utf-8").splitlines()
    definitions = {}
    for number, line in enumerate(lines, start=1):
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\) \{", line)
        if match:
            definitions[match.group(1)] = number
    call = re.compile(r"(?:^|[;&|(!]|\bif |\buntil |\bwhile |\bthen |\belse |\bdo |\$\()\s*(%s)(?=[\s;)|&]|$)")
    for name, defined_at in definitions.items():
        pattern = re.compile(call.pattern % re.escape(name))
        for number, line in enumerate(lines[: defined_at - 1], start=1):
            stripped = line.split("#", 1)[0]
            if pattern.search(stripped):
                pytest.fail(f"{script.name}:{number} calls {name}() before it is defined at line {defined_at}")


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_installer_rerun_keeps_the_port_of_the_existing_install(tmp_path: Path) -> None:
    """R4: without FLOCKS_OFFLINE_PORT a re-run must use the FLOCKS_PORT already in the env file."""
    payload = _fake_payload(tmp_path)
    # a re-run: an existing install is present, so the "port already taken" preflight does not
    # apply and the result cannot depend on what happens to listen on this dev machine
    (tmp_path / "opt-flocks" / "flocks").mkdir(parents=True)
    (tmp_path / "opt-flocks" / "flocks" / "pyproject.toml").write_text('[project]\nname = "flocks"\n', encoding="utf-8")
    env_file = tmp_path / "flocks.env"
    env_file.write_text("FLOCKS_HOST=0.0.0.0\nFLOCKS_PORT=5273\nFLOCKS_CONSOLE_BASE_URL=https://portal.example\n", encoding="utf-8")
    base = {**os.environ, "FLOCKS_OFFLINE_DRY_RUN": "1", "FLOCKS_OFFLINE_ENV_FILE": str(env_file),
            "FLOCKS_OFFLINE_DATA_HOME": str(tmp_path / "data-home")}
    base.pop("FLOCKS_OFFLINE_PORT", None)

    kept = subprocess.run([BASH, str(payload / "installer" / "install.sh")], capture_output=True, text=True, env=base, cwd=payload)
    assert kept.returncode == 0, kept.stdout + kept.stderr
    assert "端口 5273" in kept.stdout

    override_port = _free_port()
    explicit = subprocess.run(
        [BASH, str(payload / "installer" / "install.sh")],
        capture_output=True, text=True, env={**base, "FLOCKS_OFFLINE_PORT": str(override_port)}, cwd=payload,
    )
    assert explicit.returncode == 0, explicit.stdout + explicit.stderr
    assert f"端口 {override_port}" in explicit.stdout

    bad = subprocess.run(
        [BASH, str(payload / "installer" / "install.sh")],
        capture_output=True, text=True, env={**base, "FLOCKS_OFFLINE_PORT": "http"}, cwd=payload,
    )
    assert bad.returncode == 1
    assert "FLOCKS_OFFLINE_PORT 无效" in bad.stderr

    # a broken value left in the env file falls back to the default instead of aborting the upgrade
    env_file.write_text("FLOCKS_PORT=notaport\n", encoding="utf-8")
    fallback = subprocess.run([BASH, str(payload / "installer" / "install.sh")], capture_output=True, text=True, env=base, cwd=payload)
    assert fallback.returncode == 0, fallback.stdout + fallback.stderr
    assert "端口 5173" in fallback.stdout and "FLOCKS_PORT=notaport 无效" in fallback.stdout


def test_installer_only_rewrites_flocks_port_on_explicit_request() -> None:
    """R4: the env merge keeps FLOCKS_PORT unless the run itself asked for a port."""
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert re.search(r'^MANAGED_KEYS="[^"]*"$', text, re.MULTILINE), "managed key list must stay a single line"
    assert "FLOCKS_PORT" not in re.search(r'^MANAGED_KEYS="([^"]*)"$', text, re.MULTILINE).group(1)
    assert re.search(r'if \[\[ -n "\$PORT_OVERRIDE" \]\]; then\n.*?MANAGED_KEYS="\$MANAGED_KEYS FLOCKS_PORT"', text, re.DOTALL)
    # a broken FLOCKS_PORT in the env file is rewritten, not merely worked around
    assert re.search(r'无效.*\n\s*PORT=""\n\s*PORT_OVERRIDE=5173', text)
    # every consumer of the port reads the one resolved value
    for consumer in ('render_env_template', 'open_firewall_port "$PORT"', '/api/health', '安装完成：http://${SERVER_IP}:${PORT}'):
        assert consumer in text


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_verify_script_password_generation_survives_pipefail(tmp_path: Path) -> None:
    """R6: the generated password must not rely on a truncated infinite pipe (SIGPIPE → exit 141)."""
    source = _bash_function_source(VERIFY_SCRIPT, "gen_password")
    script = f"set -euo pipefail\n{source}\npw=\"$(gen_password)\"\nprintf '%s\\n' \"$pw\"\n"

    with_python = subprocess.run([BASH, "-c", script], capture_output=True, text=True)
    assert with_python.returncode == 0, with_python.stderr
    assert re.fullmatch(r"Verify-[A-Za-z0-9]{16}", with_python.stdout.strip())

    # no python anywhere on PATH: the finite-read fallback must behave the same
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    for tool in ("head", "tr", "cut", "printf", "command"):
        real = shutil.which(tool)
        if real:
            os.symlink(real, empty_bin / tool)
    without_python = subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, env={"PATH": str(empty_bin), "LC_ALL": "C"},
    )
    assert without_python.returncode == 0, without_python.stderr
    assert re.fullmatch(r"Verify-[A-Za-z0-9]{16}", without_python.stdout.strip())


def _fake_firewall_cmd(bin_dir: Path, state_file: Path, *, default_zone: str = "public", active_zones: tuple[str, ...] = ("public",)) -> None:
    """A firewall-cmd stand-in that keeps permanent and runtime port sets per zone in a JSON file."""
    zones_text = "\n".join(f"{zone}\n  interfaces: eth0" for zone in active_zones)
    shim = bin_dir / "firewall-cmd"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f"exec {sys.executable} - \"$@\" <<'PY'\n"
        "import json, sys\n"
        f"state_path = {str(state_file)!r}\n"
        "state = json.load(open(state_path))\n"
        "args = sys.argv[1:]\n"
        "state.setdefault('calls', []).append(' '.join(args))\n"
        "permanent = '--permanent' in args\n"
        f"zone = {default_zone!r}\n"
        "port = None\n"
        "action = None\n"
        "for arg in args:\n"
        "    if arg.startswith('--zone='): zone = arg.split('=', 1)[1]\n"
        "    elif arg.startswith('--query-port='): action, port = 'query', arg.split('=', 1)[1]\n"
        "    elif arg.startswith('--add-port='): action, port = 'add', arg.split('=', 1)[1]\n"
        "table = state['permanent'] if permanent else state['runtime']\n"
        "rc = 0\n"
        "if '--state' in args: print('running')\n"
        f"elif '--get-default-zone' in args: print({default_zone!r})\n"
        f"elif '--get-active-zones' in args: print({zones_text!r})\n"
        "elif '--reload' in args: state['runtime'] = {k: list(v) for k, v in state['permanent'].items()}; state['reloads'] = state.get('reloads', 0) + 1\n"
        "elif action == 'query': rc = 0 if port in table.get(zone, []) else 1\n"
        "elif action == 'add' and state.get('fail_writes'): print('Error: COMMAND_FAILED', file=sys.stderr); rc = 13\n"
        "elif action == 'add': table.setdefault(zone, []); table[zone].append(port) if port not in table[zone] else None; print('success')\n"
        "else: rc = 2\n"
        "json.dump(state, open(state_path, 'w'))\n"
        "sys.exit(rc)\n"
        "PY\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)


def _run_open_firewall_port(tmp_path: Path, state: dict, port: int = 5173, **shim_kwargs) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    state_file = tmp_path / "fw.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    _fake_firewall_cmd(bin_dir, state_file, **shim_kwargs)
    source = _bash_function_source(INSTALL_SCRIPT, "open_firewall_port")
    script = f"set -euo pipefail\nlog() {{ printf '%s\\n' \"$*\"; }}\n{source}\nopen_firewall_port {port}\n"
    completed = subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(state_file.read_text(encoding="utf-8"))
    result["stdout"] = completed.stdout
    return result


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_firewall_rule_is_made_permanent_even_when_a_runtime_rule_exists(tmp_path: Path) -> None:
    """R7: a temporary `--add-port` done by hand must not make the installer skip the permanent rule."""
    result = _run_open_firewall_port(tmp_path, {"permanent": {}, "runtime": {"public": ["5173/tcp"]}})
    assert result["permanent"] == {"public": ["5173/tcp"]}
    assert result["runtime"] == {"public": ["5173/tcp"]}
    assert result.get("reloads") == 1
    assert "已在 firewalld 放行 5173/tcp" in result["stdout"]


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_firewall_write_failure_is_reported_not_fatal(tmp_path: Path) -> None:
    """The program files are already in place when firewalld is configured: a refused write must not abort."""
    result = _run_open_firewall_port(tmp_path, {"permanent": {}, "runtime": {}, "fail_writes": True})
    assert result["permanent"] == {} and result.get("reloads", 0) == 0
    assert "警告: firewalld 未能放行 5173/tcp" in result["stdout"]


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_installer_refuses_to_run_from_its_retained_copy(tmp_path: Path) -> None:
    """/opt/flocks/installer/install.sh is kept for reference; running it would park the install it lives in."""
    install_root = tmp_path / "opt-flocks"
    (install_root / "installer").mkdir(parents=True)
    for name in INSTALLER_FILES:
        shutil.copy(INSTALLER_DIR / name, install_root / "installer" / name)
    python_dir = install_root / "tools" / "python" / "bin"
    python_dir.mkdir(parents=True)
    os.symlink(sys.executable, python_dir / "python3")
    (install_root / "versions.json").write_text(
        json.dumps({"core_version": "v2026.9.14", "arch": os.uname().machine, "install_root": str(install_root)}),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [BASH, str(install_root / "installer" / "install.sh")], capture_output=True, text=True,
        env={**os.environ, "FLOCKS_OFFLINE_DRY_RUN": "1", "FLOCKS_OFFLINE_DATA_HOME": str(tmp_path / "data-home")},
    )
    assert completed.returncode == 1
    assert "不能直接执行" in completed.stderr
    assert (install_root / "versions.json").exists() and (install_root / "installer" / "install.sh").exists()


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_firewall_rule_covers_every_active_zone_and_is_idempotent(tmp_path: Path) -> None:
    result = _run_open_firewall_port(
        tmp_path, {"permanent": {}, "runtime": {}}, port=5273, default_zone="public", active_zones=("internal",),
    )
    assert result["permanent"] == {"internal": ["5273/tcp"], "public": ["5273/tcp"]}
    assert result["runtime"] == {"internal": ["5273/tcp"], "public": ["5273/tcp"]}

    already = _run_open_firewall_port(
        tmp_path, {"permanent": {"public": ["5173/tcp"]}, "runtime": {"public": ["5173/tcp"]}},
    )
    assert already.get("reloads", 0) == 0
    assert not any(call.startswith("--permanent") and "--add-port" in call for call in already["calls"])
    assert "已在 firewalld 放行" not in already["stdout"]


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_installer_rejects_a_bundle_dir_without_wheels(tmp_path: Path) -> None:
    """A package whose bundle/ has no wheels/ is broken; it must not be mistaken for a Pro-less package."""
    payload = _fake_payload(tmp_path, with_bundle=False)
    (payload / "bundle").mkdir()
    (payload / "bundle" / "manifest.json").write_text("{}", encoding="utf-8")
    completed = _dry_run(payload)
    assert completed.returncode == 1
    assert "bundle/ 目录里没有 wheels/" in completed.stderr


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_firewall_falls_back_to_the_default_zone_when_zone_queries_fail(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_file = tmp_path / "fw.json"
    state_file.write_text(json.dumps({"permanent": {}, "runtime": {}}), encoding="utf-8")
    _fake_firewall_cmd(bin_dir, state_file)
    # wrap the shim: zone queries fail (as with a broken D-Bus session), everything else works
    real = bin_dir / "firewall-cmd-real"
    (bin_dir / "firewall-cmd").rename(real)
    (bin_dir / "firewall-cmd").write_text(
        "#!/usr/bin/env bash\n"
        'case " $* " in *" --get-default-zone "*|*" --get-active-zones "*) exit 252 ;; esac\n'
        f'exec "{real}" "$@"\n',
        encoding="utf-8",
    )
    (bin_dir / "firewall-cmd").chmod(0o755)
    source = _bash_function_source(INSTALL_SCRIPT, "open_firewall_port")
    script = f"set -euo pipefail\nlog() {{ printf '%s\\n' \"$*\"; }}\n{source}\nopen_firewall_port 5173\n"
    completed = subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["permanent"] == {"public": ["5173/tcp"]}
    assert state["runtime"] == {"public": ["5173/tcp"]}


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_verify_script_defaults_to_the_installed_port(tmp_path: Path) -> None:
    """--rerun hands the port to the installer as an explicit override, so the default must be the real one."""
    source = _bash_function_source(VERIFY_SCRIPT, "installed_port")
    env_file = tmp_path / "flocks.env"
    env_file.write_text("FLOCKS_HOST=0.0.0.0\nFLOCKS_PORT=5273\n", encoding="utf-8")
    patched = source.replace("/etc/flocks/flocks.env", str(env_file))
    assert patched != source
    script = f"set -euo pipefail\n{patched}\ninstalled_port\n"
    assert subprocess.run([BASH, "-c", script], capture_output=True, text=True).stdout.strip() == "5273"
    env_file.write_text("FLOCKS_PORT=oops\n", encoding="utf-8")
    assert subprocess.run([BASH, "-c", script], capture_output=True, text=True).stdout.strip() == "5173"
    env_file.unlink()
    assert subprocess.run([BASH, "-c", script], capture_output=True, text=True).stdout.strip() == "5173"


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_failed_install_parks_only_its_own_payload(tmp_path: Path) -> None:
    """A preflight failure moves the payload entries into a subdirectory of the extraction dir and
    touches nothing else there: `--target /var/tmp` makes that directory a shared system dir."""
    payload = _fake_payload(tmp_path, core_version="v2026.9.14")
    (payload / "versions.json").write_text(
        json.dumps({"core_version": "v2026.9.14", "arch": "riscv64", "install_root": str(tmp_path / "opt-flocks")}),
        encoding="utf-8",
    )
    (payload / "unrelated-other-service").mkdir()
    (payload / "unrelated-other-service" / "important.txt").write_text("keep me\n", encoding="utf-8")
    # not a dry run: the arch check fails before anything needs root
    completed = subprocess.run(
        [BASH, str(payload / "installer" / "install.sh")], capture_output=True, text=True,
        env={**os.environ, "FLOCKS_OFFLINE_DRY_RUN": "0", "FLOCKS_OFFLINE_DATA_HOME": str(tmp_path / "data-home")},
    )
    assert completed.returncode == 1
    assert "riscv64" in completed.stderr and "已解包的安装文件移到" in completed.stderr
    assert payload.is_dir(), "the extraction directory itself must never be renamed"
    assert (payload / "unrelated-other-service" / "important.txt").read_text(encoding="utf-8") == "keep me\n"
    parked = [d for d in payload.iterdir() if d.name.startswith("flocks-offline-failed-")]
    assert len(parked) == 1, sorted(x.name for x in payload.iterdir())
    assert {x.name for x in parked[0].iterdir()} == {"versions.json", "installer", "tools", "flocks"}
    assert not (payload / "versions.json").exists() and not (payload / "installer").exists()


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_unexpected_command_failure_is_reported_through_fail(tmp_path: Path) -> None:
    """Implicit `set -e` exits go through the ERR trap: the log says what died instead of ending silently."""
    payload = _fake_payload(tmp_path)
    (payload / "versions.json").write_text("{not json", encoding="utf-8")
    completed = _dry_run(payload)
    assert completed.returncode == 1
    assert "错误: 命令失败:" in completed.stderr and "安装未完成" in completed.stderr


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_installer_direct_run_guard_ignores_a_trailing_slash(tmp_path: Path) -> None:
    install_root = tmp_path / "opt-flocks"
    (install_root / "installer").mkdir(parents=True)
    for name in INSTALLER_FILES:
        shutil.copy(INSTALLER_DIR / name, install_root / "installer" / name)
    python_dir = install_root / "tools" / "python" / "bin"
    python_dir.mkdir(parents=True)
    os.symlink(sys.executable, python_dir / "python3")
    (install_root / "versions.json").write_text(
        json.dumps({"core_version": "v2026.9.14", "arch": os.uname().machine, "install_root": str(install_root) + "/"}),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [BASH, str(install_root / "installer" / "install.sh")], capture_output=True, text=True,
        env={**os.environ, "FLOCKS_OFFLINE_DRY_RUN": "1", "FLOCKS_OFFLINE_DATA_HOME": str(tmp_path / "data-home")},
    )
    assert completed.returncode == 1 and "不能直接执行" in completed.stderr


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_failure_inside_a_command_substitution_never_parks_the_live_install(tmp_path: Path) -> None:
    """Retained copy + broken versions.json: json_get fails inside $(...). The subshell must only
    exit; the parent reports the outer command and, with INSTALL_ROOT unset, moves nothing —
    the live instance under /opt/flocks stays exactly as it was."""
    install_root = tmp_path / "opt-flocks"
    (install_root / "installer").mkdir(parents=True)
    for name in INSTALLER_FILES:
        shutil.copy(INSTALLER_DIR / name, install_root / "installer" / name)
    python_dir = install_root / "tools" / "python" / "bin"
    python_dir.mkdir(parents=True)
    os.symlink(sys.executable, python_dir / "python3")
    (install_root / "flocks").mkdir()
    (install_root / "flocks" / "pyproject.toml").write_text('[project]\nname = "flocks"\n', encoding="utf-8")
    (install_root / "versions.json").write_text("{broken json", encoding="utf-8")
    before = sorted(x.name for x in install_root.iterdir())
    completed = subprocess.run(
        [BASH, str(install_root / "installer" / "install.sh")], capture_output=True, text=True,
        env={**os.environ, "FLOCKS_OFFLINE_DRY_RUN": "0", "FLOCKS_OFFLINE_DATA_HOME": str(tmp_path / "data-home")},
    )
    assert completed.returncode == 1
    assert "命令失败" in completed.stderr and "json_get" in completed.stderr
    assert completed.stdout == "", "fail() output must not leak into stdout / command substitutions"
    assert sorted(x.name for x in install_root.iterdir()) == before
    assert not any(x.name.startswith("flocks-offline-failed-") for x in install_root.iterdir())


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_foreign_architecture_is_reported_before_the_bundled_python_is_needed(tmp_path: Path) -> None:
    """On the wrong architecture the bundled python cannot start; the arch message must still be the first thing said."""
    payload = _fake_payload(tmp_path)
    broken_python = payload / "tools" / "python" / "bin" / "python3"
    broken_python.unlink()
    broken_python.write_text("#!/bin/sh\necho 'Exec format error' >&2\nexit 126\n", encoding="utf-8")
    broken_python.chmod(0o755)
    (payload / "versions.json").write_text(
        json.dumps({"core_version": "v2026.9.14", "arch": "riscv64", "install_root": str(tmp_path / "opt-flocks")}, indent=2),
        encoding="utf-8",
    )
    (payload / "leave-me").write_text("x", encoding="utf-8")
    completed = subprocess.run(
        [BASH, str(payload / "installer" / "install.sh")], capture_output=True, text=True,
        env={**os.environ, "FLOCKS_OFFLINE_DRY_RUN": "0", "FLOCKS_OFFLINE_DATA_HOME": str(tmp_path / "data-home")},
    )
    assert completed.returncode == 1
    assert "安装包是 riscv64 版本" in completed.stderr and "命令失败" not in completed.stderr
    # the payload is still parked (install_root read as text), unrelated files untouched
    assert (payload / "leave-me").exists() and not (payload / "versions.json").exists()
    assert any(x.name.startswith("flocks-offline-failed-") for x in payload.iterdir())



# ---------------------------------------------------------------------------
# data of a previous installation (installer/migrate_data.py + install.sh)
# ---------------------------------------------------------------------------


def _load_migrate_helper():
    import importlib.util

    spec = importlib.util.spec_from_file_location("flocks_offline_migrate_data", MIGRATE_HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _previous_data(root: Path, *, pro: bool = False) -> Path:
    """A ~/.flocks of an earlier instance: config with an absolute path, DB, hub record, license, symlink."""
    for sub in ("config", "data/hub", "plugins/skills/my-skill", "flockspro", "run", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "config" / "flocks.json").write_text(
        json.dumps({"mcp": {"local": {"command": f"{root}/plugins/skills/my-skill/run.sh"}}, "other": "/opt/keep"}),
        encoding="utf-8",
    )
    (root / "config" / ".secret.json").write_text('{"api_key": "x"}', encoding="utf-8")
    (root / "config" / ".secret.json").chmod(0o600)
    (root / "data" / "flocks.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 64)
    (root / "data" / "hub" / "installed.json").write_text(
        json.dumps({"skill:my-skill": {"id": "my-skill", "installPath": f"{root}/plugins/skills/my-skill"}}),
        encoding="utf-8",
    )
    (root / "plugins" / "skills" / "my-skill" / "SKILL.md").write_text("# mine\n", encoding="utf-8")
    (root / "flockspro" / "install_id").write_text("inst-1\n", encoding="utf-8")
    os.symlink(root / "plugins" / "skills" / "my-skill", root / "plugins" / "pinned-skill")
    if pro:
        (root / "run" / "pro-bundle-installed.json").write_text('{"bundle_version": "v2026.9.14"}', encoding="utf-8")
    return root


def _tree_fingerprint(root: Path) -> list[tuple[str, int, str]]:
    entries = []
    for path in sorted(root.rglob("*")):
        info = os.lstat(path)
        if path.is_symlink():
            content = os.readlink(path)
        elif path.is_file():
            content = hashlib.sha256(path.read_bytes()).hexdigest() + f"@{info.st_mtime_ns}"
        else:
            content = ""
        entries.append((str(path.relative_to(root)), info.st_mode, content))
    return entries


@pytest.mark.skipif(sys.platform == "win32", reason="posix paths and modes")
def test_migrate_helper_copies_previous_data_and_leaves_the_source_untouched(tmp_path: Path) -> None:
    helper = _load_migrate_helper()
    source = _previous_data(tmp_path / "root-home" / ".flocks", pro=True)
    target = tmp_path / "var-lib-flocks" / ".flocks"
    (target / "config").mkdir(parents=True)
    (target / "config" / "install_profile.json").write_text('{"Language": "zh-CN"}', encoding="utf-8")
    before = _tree_fingerprint(source)

    lines = helper.copy(source, target, "20260928000000")

    assert _tree_fingerprint(source) == before, "the previous installation's directory is only read"
    for rel in ("config/.secret.json", "data/flocks.db", "flockspro/install_id", "run/pro-bundle-installed.json",
                "plugins/skills/my-skill/SKILL.md"):
        assert (target / rel).read_bytes() == (source / rel).read_bytes(), rel
    assert (target / "config" / ".secret.json").stat().st_mode & 0o777 == 0o600
    # absolute paths into the old directory now point at the new one, other paths are left alone
    config = json.loads((target / "config" / "flocks.json").read_text(encoding="utf-8"))
    assert config["mcp"]["local"]["command"] == f"{target}/plugins/skills/my-skill/run.sh"
    assert config["other"] == "/opt/keep"
    records = json.loads((target / "data" / "hub" / "installed.json").read_text(encoding="utf-8"))
    assert records["skill:my-skill"]["installPath"] == f"{target}/plugins/skills/my-skill"
    assert os.readlink(target / "plugins" / "pinned-skill") == f"{target}/plugins/skills/my-skill"
    # the data dir that was already there (no user data) is parked, not deleted; what only it had is kept
    parked = target.with_name(".flocks.pre-migration-20260928000000")
    assert (parked / "config" / "install_profile.json").exists()
    assert (target / "config" / "install_profile.json").exists()
    assert (target / "run" / "offline-migration-20260928000000.json").exists()
    assert not target.with_name(".flocks.migrating-20260928000000").exists()
    assert any("旧目录原样保留" in line for line in lines)


@pytest.mark.skipif(sys.platform == "win32", reason="unix sockets")
def test_migrate_helper_skips_sockets_left_by_the_old_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    helper = _load_migrate_helper()
    source = _previous_data(tmp_path / "old" / ".flocks")
    monkeypatch.chdir(source / "run")  # a relative bind keeps the socket path under the AF_UNIX length limit
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind("control.sock")
        lines = helper.copy(source, tmp_path / "new" / ".flocks", "1")
    assert not os.path.lexists(tmp_path / "new" / ".flocks" / "run" / "control.sock")
    assert (tmp_path / "new" / ".flocks" / "data" / "flocks.db").exists()
    assert any("特殊文件" in line for line in lines)


def test_migrate_helper_plan_order_explicit_then_flocks_root_then_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _load_migrate_helper()
    target = tmp_path / "var-lib-flocks" / ".flocks"
    explicit = _previous_data(tmp_path / "explicit")
    env_root = _previous_data(tmp_path / "env-root")
    default = _previous_data(tmp_path / "home" / "admin" / ".flocks")
    monkeypatch.setenv("FLOCKS_OFFLINE_HOME_CANDIDATES", f"{default}:{tmp_path / 'home' / 'empty' / '.flocks'}")

    assert helper.plan(target, str(explicit), str(env_root), "") == explicit
    assert helper.plan(target, "none", str(env_root), "") is None
    assert helper.plan(target, "", str(env_root), "") == env_root
    # a FLOCKS_ROOT without data falls back to the original default ~/.flocks
    assert helper.plan(target, "", str(tmp_path / "empty-root"), "") == default
    assert helper.plan(target, "", "", "") == default
    # a data root that already holds data is never replaced (an explicit source is then an error)
    (target / "data").mkdir(parents=True)
    (target / "data" / "flocks.db").write_bytes(b"x")
    assert helper.plan(target, "", str(env_root), "") is None
    with pytest.raises(helper.MigrationError, match="已经有数据"):
        helper.plan(target, str(explicit), str(env_root), "")

    with pytest.raises(helper.MigrationError, match="没有 Flocks 数据"):
        helper.plan(tmp_path / "fresh" / ".flocks", str(tmp_path / "nothing"), "", "")
    with pytest.raises(helper.MigrationError, match="绝对路径"):
        helper.plan(tmp_path / "fresh" / ".flocks", "relative/dir", "", "")


def test_migrate_helper_plan_stops_when_several_users_have_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _load_migrate_helper()
    first = _previous_data(tmp_path / "root" / ".flocks")
    second = _previous_data(tmp_path / "home" / "admin" / ".flocks")
    monkeypatch.setenv("FLOCKS_OFFLINE_HOME_CANDIDATES", f"{first}:{second}:{first}")
    with pytest.raises(helper.MigrationError) as excinfo:
        helper.plan(tmp_path / "var-lib-flocks" / ".flocks", "", "", "")
    message = str(excinfo.value)
    assert str(first) in message and str(second) in message
    assert "FLOCKS_OFFLINE_MIGRATE_FROM" in message and "本次未做任何改动" in message


def test_migrate_helper_never_copies_over_existing_data(tmp_path: Path) -> None:
    helper = _load_migrate_helper()
    source = _previous_data(tmp_path / "old" / ".flocks")
    target = _previous_data(tmp_path / "new" / ".flocks")
    before = _tree_fingerprint(target)
    with pytest.raises(helper.MigrationError, match="已经有数据"):
        helper.copy(source, target, "1")
    assert _tree_fingerprint(target) == before
    with pytest.raises(helper.MigrationError, match="重叠"):
        helper.copy(source, source / "nested", "1")


@pytest.mark.skipif(sys.platform == "win32", reason="symlinked fake /proc")
def test_migrate_helper_check_refuses_a_running_instance_and_a_full_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _load_migrate_helper()
    source = _previous_data(tmp_path / "old" / ".flocks")
    target = tmp_path / "new" / ".flocks"
    proc = tmp_path / "proc"
    (proc / "4242" / "fd").mkdir(parents=True)
    (proc / "4242" / "cmdline").write_bytes(b"/root/flocks/.venv/bin/python\0-m\0flocks.cli.main\0serve\0")
    os.symlink(str(source.resolve() / "data" / "flocks.db"), proc / "4242" / "fd" / "7")
    (proc / "4243" / "fd").mkdir(parents=True)
    os.symlink("/dev/null", proc / "4243" / "fd" / "0")
    with pytest.raises(helper.MigrationError) as excinfo:
        helper.check(source, target, proc)
    message = str(excinfo.value)
    assert "PID 4242" in message and "flocks.cli.main serve" in message and "4243" not in message

    idle_proc = tmp_path / "proc-idle"
    idle_proc.mkdir()
    assert helper.check(source, target, idle_proc).endswith("MB")

    class _FullDisk:
        f_bavail = 1
        f_frsize = 4096

    monkeypatch.setattr(helper.os, "statvfs", lambda _path: _FullDisk())
    with pytest.raises(helper.MigrationError, match="磁盘空间不足"):
        helper.check(source, target, idle_proc)


def test_migrate_helper_reads_the_flocks_root_an_installation_runs_with(tmp_path: Path) -> None:
    helper = _load_migrate_helper()
    env_file = tmp_path / "flocks.env"
    assert helper.configured_root(env_file) == ""
    env_file.write_text('HOME=/var/lib/flocks\nFLOCKS_ROOT=/var/lib/flocks/.flocks\n# note\nFLOCKS_ROOT="/data/flocks/"\n', encoding="utf-8")
    assert helper.configured_root(env_file) == "/data/flocks"
    env_file.write_text("export FLOCKS_ROOT=/srv/flocks\n", encoding="utf-8")
    assert helper.configured_root(env_file) == "/srv/flocks"
    env_file.write_text("FLOCKS_ROOT=~/.flocks\n", encoding="utf-8")
    assert helper.configured_root(env_file) == ""


def test_migrate_helper_never_deletes_anything() -> None:
    text = MIGRATE_HELPER.read_text(encoding="utf-8")
    for forbidden in ("rmtree", ".unlink(", "os.remove(", "os.rmdir(", "shutil.move("):
        assert forbidden not in text, forbidden


def test_build_script_ships_the_migrate_helper() -> None:
    assert '"$SCRIPT_DIR/installer/migrate_data.py"' in BUILD_SCRIPT.read_text(encoding="utf-8")


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_installer_plans_to_bring_over_the_previous_installations_data(tmp_path: Path) -> None:
    payload = _fake_payload(tmp_path, with_bundle=True)
    previous = _previous_data(tmp_path / "root-home" / ".flocks")
    result = _dry_run(payload, extra_env={"FLOCKS_OFFLINE_HOME_CANDIDATES": f"{previous}:{tmp_path / 'nobody' / '.flocks'}"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"迁移旧数据={previous}" in result.stdout
    assert f"检测到旧的 Flocks 数据 {previous}" in result.stdout

    # the FLOCKS_ROOT the installer is started with comes before the default dirs; none opts out
    other = _previous_data(tmp_path / "data-disk" / "flocks")
    result = _dry_run(payload, extra_env={"FLOCKS_OFFLINE_HOME_CANDIDATES": str(previous), "FLOCKS_ROOT": str(other)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"迁移旧数据={other}" in result.stdout
    result = _dry_run(payload, extra_env={"FLOCKS_OFFLINE_HOME_CANDIDATES": str(previous), "FLOCKS_OFFLINE_MIGRATE_FROM": "none"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "迁移旧数据=无" in result.stdout


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_installer_stops_before_any_change_when_the_previous_data_is_ambiguous(tmp_path: Path) -> None:
    payload = _fake_payload(tmp_path, with_bundle=True)
    first = _previous_data(tmp_path / "root" / ".flocks")
    second = _previous_data(tmp_path / "home" / "admin" / ".flocks")
    result = _dry_run(payload, extra_env={"FLOCKS_OFFLINE_HOME_CANDIDATES": f"{first}:{second}"})
    assert result.returncode == 1
    assert "发现多个旧的 Flocks 数据目录" in result.stderr
    assert str(first) in result.stderr and str(second) in result.stderr
    assert not (tmp_path / "opt-flocks").exists()


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_installer_keeps_the_flocks_root_an_existing_install_runs_with(tmp_path: Path) -> None:
    payload = _fake_payload(tmp_path, with_bundle=True)
    custom = _previous_data(tmp_path / "data-disk" / ".flocks")
    stray = _previous_data(tmp_path / "root" / ".flocks")
    env_file = tmp_path / "flocks.env"
    env_file.write_text(f"FLOCKS_ROOT={custom}\nFLOCKS_PORT=5273\n", encoding="utf-8")
    result = _dry_run(payload, extra_env={"FLOCKS_OFFLINE_ENV_FILE": str(env_file), "FLOCKS_OFFLINE_HOME_CANDIDATES": str(stray)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"数据目录 {custom}" in result.stdout and "迁移旧数据=无" in result.stdout


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_installer_treats_migrated_or_relocated_pro_data_as_activated(tmp_path: Path) -> None:
    payload = _fake_payload(tmp_path, with_bundle=False)
    # Pro activated in the data about to be migrated, new package without Pro → refuse before changing anything
    previous = _previous_data(tmp_path / "root" / ".flocks", pro=True)
    result = _dry_run(payload, extra_env={"FLOCKS_OFFLINE_HOME_CANDIDATES": str(previous)})
    assert result.returncode == 1
    assert "已激活 Flocks Pro" in result.stderr and "本次未做任何改动" in result.stderr

    # Pro activated under a FLOCKS_ROOT moved off /var/lib/flocks/.flocks → the same check applies
    custom = _previous_data(tmp_path / "data-disk" / ".flocks", pro=True)
    env_file = tmp_path / "flocks.env"
    env_file.write_text(f"FLOCKS_ROOT={custom}\n", encoding="utf-8")
    result = _dry_run(payload, extra_env={"FLOCKS_OFFLINE_ENV_FILE": str(env_file)})
    assert result.returncode == 1
    assert "已激活 Flocks Pro" in result.stderr


def test_migrate_helper_leaves_the_existing_data_dir_alone_when_the_swap_fails(tmp_path: Path) -> None:
    helper = _load_migrate_helper()
    source = _previous_data(tmp_path / "old" / ".flocks")
    shutil.rmtree(source / "run")
    (source / "run").write_text("a file where the target has a directory", encoding="utf-8")
    target = tmp_path / "new" / ".flocks"
    (target / "run").mkdir(parents=True)
    (target / "run" / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(helper.MigrationError, match="旧数据目录没有改动"):
        helper.copy(source, target, "1")
    assert (target / "run" / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not target.with_name(".flocks.pre-migration-1").exists()
    assert target.with_name(".flocks.migrating-1").is_dir(), "the partial copy is kept for inspection, never deleted"


def test_installer_chowns_the_data_root_without_following_links_out_of_it() -> None:
    """chown -R with -H/-L dereferences every symlink in the tree: a venv's bin/python3 → /usr/bin
    would become flocks-owned, and a dangling SingletonLock aborts the install after the swap."""
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.lstrip().startswith("chown") and "-R" in line.split():
            flags = line.split()
            assert not ({"-H", "-L"} & set(flags)) or "-h" in flags, line
    assert 'chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_ROOT/"' in text


@pytest.mark.skipif(sys.platform == "win32", reason="posix symlinks")
def test_migrate_helper_copies_into_a_symlinked_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _load_migrate_helper()
    source = _previous_data(tmp_path / "old" / ".flocks")
    disk = tmp_path / "disk"
    (disk / "flocks-data" / "run").mkdir(parents=True)
    (tmp_path / "var-lib-flocks").mkdir()
    target = tmp_path / "var-lib-flocks" / ".flocks"
    os.symlink(disk / "flocks-data", target)

    seen: list[str] = []
    real_statvfs = os.statvfs

    def _recording_statvfs(path):
        seen.append(str(path))
        return real_statvfs(path)

    with monkeypatch.context() as patched:  # a plain undo() would also revert the autouse fixture
        patched.setattr(helper.os, "statvfs", _recording_statvfs)
        helper.check(source, target, tmp_path / "no-proc")
    assert seen == [str((disk / "flocks-data").resolve().parent)], "free space must be measured where the copy is staged"

    helper.copy(source, target, "7")
    assert target.is_symlink() and os.readlink(target) == str(disk / "flocks-data")
    assert (disk / "flocks-data" / "data" / "flocks.db").is_file()
    assert (disk / "flocks-data.pre-migration-7" / "run").is_dir()
    assert not (tmp_path / "var-lib-flocks" / ".flocks.migrating-7").exists()
    records = json.loads((target / "data" / "hub" / "installed.json").read_text(encoding="utf-8"))
    assert records["skill:my-skill"]["installPath"] == f"{target}/plugins/skills/my-skill"


def test_migrate_helper_refuses_data_dirs_that_are_not_flocks_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _load_migrate_helper()
    source = _previous_data(tmp_path / "old" / ".flocks")
    with pytest.raises(helper.MigrationError, match="不能是 /"):
        helper.plan(Path("/"), "", "", "")
    shared = tmp_path / "data"
    (shared / "lost+found").mkdir(parents=True)
    (shared / "mysql").mkdir()
    (shared / "run").mkdir()
    with pytest.raises(helper.MigrationError, match="不属于 Flocks") as excinfo:
        helper.plan(shared, str(source), "", "")
    assert "mysql" in str(excinfo.value) and "lost+found" not in str(excinfo.value)
    # a fresh ext4 disk mounted as the data dir only has lost+found: a plain install must go on
    fresh_disk = tmp_path / "fresh-disk"
    (fresh_disk / "lost+found").mkdir(parents=True)
    assert helper.plan(fresh_disk, "", "", "") is None
    # an installer skeleton (or any partial Flocks layout) is fine
    skeleton = tmp_path / "skeleton" / ".flocks"
    for sub in ("config", "run", "logs"):
        (skeleton / sub).mkdir(parents=True)
    (skeleton / "config" / "install_profile.json").write_text("{}", encoding="utf-8")
    assert helper.plan(skeleton, str(source), "", "") == source

    real_skeleton = skeleton.resolve()
    monkeypatch.setattr(helper.os.path, "ismount", lambda path: os.path.realpath(path) == str(real_skeleton))
    with pytest.raises(helper.MigrationError, match="挂载点"):
        helper.check(source, skeleton, tmp_path / "no-proc")


def test_migrate_helper_explicit_source_never_silently_skipped(tmp_path: Path) -> None:
    helper = _load_migrate_helper()
    source = _previous_data(tmp_path / "old" / ".flocks")
    target = _previous_data(tmp_path / "new" / ".flocks")
    with pytest.raises(helper.MigrationError, match="已经有数据"):
        helper.plan(target, str(source), "", "")
    assert helper.plan(target, "none", "", "") is None
    assert helper.plan(target, "", str(source), "") is None  # an inherited FLOCKS_ROOT is only a hint


@pytest.mark.skipif(sys.platform == "win32", reason="posix symlinks")
def test_migrate_helper_copies_a_data_dir_moved_to_another_disk_and_never_writes_back(tmp_path: Path) -> None:
    helper = _load_migrate_helper()
    source = _previous_data(tmp_path / "old" / ".flocks")
    external = tmp_path / "bigdisk" / "flocks-data"
    external.parent.mkdir()
    shutil.move(str(source / "data"), str(external))
    os.symlink(external, source / "data")
    (external / "hub" / "extra.json").write_text(json.dumps({"backupPath": f"{external}/hub/backups/x"}), encoding="utf-8")
    # a directory link inside the tree that points back into it by absolute path
    os.symlink(source / "plugins" / "skills", source / "plugins" / "skills-link")
    source_before, external_before = _tree_fingerprint(source), _tree_fingerprint(external)

    proc = tmp_path / "proc"
    (proc / "31337" / "fd").mkdir(parents=True)
    (proc / "31337" / "cmdline").write_bytes(b"flocks serve\0")
    os.symlink(str(external.resolve() / "flocks.db"), proc / "31337" / "fd" / "4")
    with pytest.raises(helper.MigrationError, match="PID 31337"):
        helper.check(source, tmp_path / "new" / ".flocks", proc)

    target = tmp_path / "new" / ".flocks"
    lines = helper.copy(source, target, "3")
    assert _tree_fingerprint(source) == source_before and _tree_fingerprint(external) == external_before
    assert not (target / "data").is_symlink() and (target / "data" / "flocks.db").is_file()
    assert any("拷成实体目录" in line for line in lines)
    records = json.loads((target / "data" / "hub" / "installed.json").read_text(encoding="utf-8"))
    assert records["skill:my-skill"]["installPath"] == f"{target}/plugins/skills/my-skill"
    extra = json.loads((target / "data" / "hub" / "extra.json").read_text(encoding="utf-8"))
    assert extra["backupPath"] == f"{target}/data/hub/backups/x"
    assert os.readlink(target / "plugins" / "skills-link") == f"{target}/plugins/skills"


@pytest.mark.skipif(sys.platform == "win32", reason="posix symlinks")
def test_migrate_helper_refuses_an_external_link_that_contains_the_new_data_dir(tmp_path: Path) -> None:
    """workspace -> /data with the new data dir under /data: copying /data would copy the copy."""
    helper = _load_migrate_helper()
    source = _previous_data(tmp_path / "old" / ".flocks")
    data_disk = tmp_path / "data"
    (data_disk / "projects").mkdir(parents=True)
    os.symlink(data_disk, source / "workspace")
    target = data_disk / "flocks" / ".flocks"
    with pytest.raises(helper.MigrationError, match="拷贝本身再拷进去"):
        helper.check(source, target, tmp_path / "no-proc")
    with pytest.raises(helper.MigrationError, match="拷贝本身再拷进去"):
        helper.copy(source, target, "9")
    assert not (data_disk / "flocks").exists()
    # a sibling layout (/data/flocks-old next to /data/flocks) is fine
    os.rename(source / "workspace", tmp_path / "old-link")
    sibling = data_disk / "flocks-old"
    sibling.mkdir()
    os.symlink(sibling, source / "workspace")
    assert helper.check(source, data_disk / "flocks" / ".flocks", tmp_path / "no-proc").endswith("MB")

"""Static and dry-run checks for the Linux offline installer (packaging/linux)."""

from __future__ import annotations

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
MANIFEST = PACKAGING_DIR / "versions.manifest.json"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "linux-offline-run.yml"

SHELL_SCRIPTS = [BUILD_SCRIPT, INSTALL_SCRIPT, WRAPPER_SCRIPT, SMOKE_SCRIPT, VERIFY_SCRIPT, DOCKER_BUILD_SCRIPT, EXPORT_SCRIPT]
GIT = shutil.which("git")
BASH = shutil.which("bash")


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
    # the data root is only ever created/chowned, never moved or removed
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
    for name in ("install.sh", "flocks.service", "flocks.env.template", "flocks-cli-wrapper.sh"):
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


def _dry_run(payload: Path, data_home: Path | None = None) -> subprocess.CompletedProcess:
    # an unused port so the "port already taken" preflight never trips on a dev machine; the data
    # home always points into the temp dir so the preflight never reads the real /var/lib/flocks
    env = {**os.environ, "FLOCKS_OFFLINE_DRY_RUN": "1", "FLOCKS_OFFLINE_PORT": str(_free_port())}
    env["FLOCKS_OFFLINE_DATA_HOME"] = str(data_home if data_home is not None else payload.parent / "data-home")
    return subprocess.run([BASH, str(payload / "installer" / "install.sh")], capture_output=True, text=True, env=env, cwd=payload)


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_installer_refuses_to_drop_activated_pro_without_a_compatible_bundle(tmp_path: Path) -> None:
    """R2: a new package without Pro must not silently turn an activated instance into OSS."""
    payload = _fake_payload(tmp_path, with_bundle=False)

    # no previous bundle to carry over → refuse before changing anything
    data_home = _fake_activated_install(tmp_path, old_bundle_core=None)
    result = _dry_run(payload, data_home)
    assert result.returncode == 1
    assert "已激活 Flocks Pro" in result.stdout and "本次未做任何改动" in result.stdout

    # previous bundle built for another core → refuse
    shutil.rmtree(tmp_path / "opt-flocks")
    shutil.rmtree(tmp_path / "data-home")
    data_home = _fake_activated_install(tmp_path, old_bundle_core="v2026.8.17")
    result = _dry_run(payload, data_home)
    assert result.returncode == 1
    assert "与新版本不匹配" in result.stdout

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
    shutil.copy(INSTALL_SCRIPT, payload / "installer" / "install.sh")
    for name in ("flocks.service", "flocks.env.template", "flocks-cli-wrapper.sh"):
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
    assert "riscv64" in completed.stdout


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
    assert "FLOCKS_OFFLINE_PORT 无效" in bad.stdout

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
    for name in ("install.sh", "flocks.service", "flocks.env.template", "flocks-cli-wrapper.sh"):
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
    assert "不能直接执行" in completed.stdout
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
    assert "bundle/ 目录里没有 wheels/" in completed.stdout


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
    assert "riscv64" in completed.stdout and "已解包的安装文件移到" in completed.stdout
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
    assert "错误: 命令失败:" in completed.stdout and "安装未完成" in completed.stdout


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_installer_direct_run_guard_ignores_a_trailing_slash(tmp_path: Path) -> None:
    install_root = tmp_path / "opt-flocks"
    (install_root / "installer").mkdir(parents=True)
    for name in ("install.sh", "flocks.service", "flocks.env.template", "flocks-cli-wrapper.sh"):
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
    assert completed.returncode == 1 and "不能直接执行" in completed.stdout


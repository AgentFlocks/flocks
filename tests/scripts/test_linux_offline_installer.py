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
ENV_TEMPLATE = INSTALLER_DIR / "flocks.env.template"
UNIT_TEMPLATE = INSTALLER_DIR / "flocks.service"
MANIFEST = PACKAGING_DIR / "versions.manifest.json"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "linux-offline-run.yml"

SHELL_SCRIPTS = [BUILD_SCRIPT, INSTALL_SCRIPT, WRAPPER_SCRIPT, SMOKE_SCRIPT, VERIFY_SCRIPT, DOCKER_BUILD_SCRIPT]
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
    assert "set -euo pipefail" in text


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
    assert "firewall-cmd --permanent --add-port" in text  # http://<ip>:5173 reachable
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

    env = {**os.environ, "FLOCKS_OFFLINE_DRY_RUN": "1", "FLOCKS_OFFLINE_PORT": "0"}
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
        env={**os.environ, "FLOCKS_OFFLINE_DRY_RUN": "1"},
        cwd=payload,
    )
    assert completed.returncode == 1
    assert "riscv64" in completed.stdout

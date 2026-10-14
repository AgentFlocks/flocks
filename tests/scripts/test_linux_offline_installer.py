"""Static and dry-run checks for the Linux offline installer (packaging/linux)."""

from __future__ import annotations

import hashlib
import json
import importlib.util
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGING_DIR = REPO_ROOT / "packaging" / "linux"
INSTALLER_DIR = PACKAGING_DIR / "installer"
BUILD_SCRIPT = PACKAGING_DIR / "build-offline-run.sh"
INSTALL_SCRIPT = INSTALLER_DIR / "install.sh"
WRAPPER_SCRIPT = INSTALLER_DIR / "flocks-cli-wrapper.sh"
RELOCATE_SCRIPT = INSTALLER_DIR / "relocate.py"
SMOKE_SCRIPT = PACKAGING_DIR / "smoke-test.sh"
VERIFY_SCRIPT = PACKAGING_DIR / "verify-install.sh"
DOCKER_BUILD_SCRIPT = PACKAGING_DIR / "build-in-docker.sh"
EXPORT_SCRIPT = PACKAGING_DIR / "export-source.sh"
ENV_TEMPLATE = INSTALLER_DIR / "flocks.env.template"
UNIT_TEMPLATE = INSTALLER_DIR / "flocks.service"
MIGRATE_HELPER = INSTALLER_DIR / "migrate_data.py"
INSTALLER_FILES = (
    "install.sh", "migrate_data.py", "flocks.service", "flocks.env.template", "flocks-cli-wrapper.sh",
    "relocate.py", "postgres_preflight.py",
)
MANIFEST = PACKAGING_DIR / "versions.manifest.json"
GLIBC_FLOOR = PACKAGING_DIR / "glibc_floor.py"
INTRANET_SCRIPT = PACKAGING_DIR / "intranet-sim.sh"
INTRANET_ALLOWLIST = PACKAGING_DIR / "intranet-allowlist.txt"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "linux-offline-run.yml"

SHELL_SCRIPTS = [BUILD_SCRIPT, INSTALL_SCRIPT, WRAPPER_SCRIPT, SMOKE_SCRIPT, VERIFY_SCRIPT, DOCKER_BUILD_SCRIPT, EXPORT_SCRIPT, INTRANET_SCRIPT]
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
def test_shell_variables_are_not_glued_to_non_ascii_text(script: Path) -> None:
    """bash 3.2 (macOS /bin/sh) reads the lead byte of a following UTF-8 character into the name:
    "（$root）" prints "（\\xbc\\x89" with the value gone. Messages write ${name} next to CJK text."""
    glued = re.compile(r"\\?\$[A-Za-z_][A-Za-z0-9_]*(?=[^\x00-\x7f])")
    for number, line in enumerate(script.read_text(encoding="utf-8").splitlines(), start=1):
        match = glued.search(line)
        assert match is None, f"{script.name}:{number}: {match.group(0)} is followed by non-ASCII text, write it as ${{...}}"


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
        # offline with a Pro bundle, source for an open-source package (see test_env_follows_upgrade_mode)
        "FLOCKS_DEPLOY_MODE": "@DEPLOY_MODE@",
        "FLOCKS_OFFLINE_INSTALL": "1",
        "FLOCKS_HOST": "0.0.0.0",
        "FLOCKS_PORT": "@PORT@",
        "FLOCKS_CONSOLE_BASE_URL": "https://portalflocks.threatbook.cn",
        "FLOCKS_UPDATE_CHANNEL": "@UPDATE_CHANNEL@",
        "FLOCKS_PRO_BUNDLE_DIR": "@INSTALL_ROOT@/bundle",
        "UV_OFFLINE": "1",
        # skills run `uv run python -c ...`: without it uv tries to rebuild flocks and fetch hatchling
        "UV_NO_SYNC": "1",
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


OFFLINE_ONLY_KEYS = ("FLOCKS_OFFLINE_INSTALL", "FLOCKS_UPDATE_CHANNEL", "FLOCKS_PRO_BUNDLE_DIR", "UV_OFFLINE", "npm_config_offline")


def _render_env(tmp_path: Path, upgrade_mode: str) -> dict[str, str]:
    """Run install.sh's render_env_template for one upgrade mode and parse what it writes."""
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    keys_re = re.search(r"^OFFLINE_ONLY_KEYS_RE='([^']+)'$", text, re.MULTILINE)
    assert keys_re, "OFFLINE_ONLY_KEYS_RE must stay a single-quoted one-line assignment"
    src_root = tmp_path / f"payload-{upgrade_mode}"
    (src_root / "installer").mkdir(parents=True)
    shutil.copy(ENV_TEMPLATE, src_root / "installer" / "flocks.env.template")
    deploy_mode = "source" if upgrade_mode == "online" else "offline"
    script = (
        "set -Eeuo pipefail\n"
        f"OFFLINE_ONLY_KEYS_RE='{keys_re.group(1)}'\n"
        f"SRC_ROOT='{src_root}'; INSTALL_ROOT=/opt/flocks; DATA_HOME=/var/lib/flocks; PORT=5273\n"
        # The template points FLOCKS_DB_DSN_FILE next to the env file.
        "ENV_FILE=/etc/flocks/flocks.env\n"
        f"UPDATE_CHANNEL=flockspro-offline-2026.9.23; UPGRADE_MODE={upgrade_mode}; DEPLOY_MODE={deploy_mode}\n"
        f"{_bash_function_source(INSTALL_SCRIPT, 'render_env_template')}render_env_template\n"
    )
    completed = subprocess.run([BASH, "-c", script], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    values = {}
    for line in completed.stdout.splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_env_follows_upgrade_mode(tmp_path: Path) -> None:
    """Open-source package: upgrades go online like a source install, so no offline pin may be written."""
    online = _render_env(tmp_path, "online")
    assert online["FLOCKS_DEPLOY_MODE"] == "source"
    for key in OFFLINE_ONLY_KEYS:
        assert key not in online, f"{key} must not be written for an open-source package"
    assert online["FLOCKS_CONSOLE_BASE_URL"] == "https://portalflocks.threatbook.cn"
    assert online["UV_PYTHON"] == "/opt/flocks/tools/python/bin/python3"
    assert online["FLOCKS_PORT"] == "5273"

    offline = _render_env(tmp_path, "offline")
    assert offline["FLOCKS_DEPLOY_MODE"] == "offline"
    assert offline["FLOCKS_OFFLINE_INSTALL"] == "1"
    assert offline["UV_OFFLINE"] == "1" and offline["npm_config_offline"] == "true"
    assert offline["FLOCKS_UPDATE_CHANNEL"] == "flockspro-offline-2026.9.23"
    assert offline["FLOCKS_PRO_BUNDLE_DIR"] == "/opt/flocks/bundle"
    # the two modes differ in exactly the offline pins (and the deploy mode value)
    assert set(offline) - set(online) == set(OFFLINE_ONLY_KEYS)
    assert set(online) <= set(offline)


def test_installer_switches_an_existing_env_between_upgrade_modes() -> None:
    """Re-running a package of the other kind must move an existing env file over, not keep the old mode."""
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    managed = re.search(r'^MANAGED_KEYS="([^"]*)"$', text, re.MULTILINE).group(1).split()
    assert "FLOCKS_DEPLOY_MODE" in managed
    keys_re = re.search(r"^OFFLINE_ONLY_KEYS_RE='([^']+)'$", text, re.MULTILINE).group(1)
    for key in OFFLINE_ONLY_KEYS:
        assert re.match(keys_re, f"{key}=x"), f"{key} missing from OFFLINE_ONLY_KEYS_RE"
    assert not re.match(keys_re, "FLOCKS_OFFLINE_INSTALLED=1"), "the key pattern must be anchored on '='"
    # online mode strips the pins an earlier Pro-bundle install left behind (the merge keeps unmanaged keys)
    assert 'sed -i -E "/$OFFLINE_ONLY_KEYS_RE/d" "$ENV_FILE"' in text
    # the upgrade mode is decided before the dry-run exit, from the payload and the carried-over bundle
    decision = text.index('if [[ -d "$SRC_ROOT/bundle" || "$CARRY_OVER_BUNDLE" -eq 1 ]]; then')
    assert decision < text.index('if [[ "$DRY_RUN" == "1" ]]; then')


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
def test_export_source_refuses_credential_like_content_and_drops_webui_tests(tmp_path: Path) -> None:
    # customers scan what they receive: no credential-shaped strings and no internal host names may
    # ship, not even as examples. (Built at runtime so this file itself trips no secret scanner.)
    internal_host = "onesig-test-131.threatbook-inc" + ".cn"
    repo = _make_repo_with_local_secrets(tmp_path)
    (repo / "webui" / "src" / "pages").mkdir(parents=True)
    (repo / "webui" / "src" / "test").mkdir()
    (repo / "webui" / "src" / "main.tsx").write_text("export {};\n", encoding="utf-8")
    (repo / "webui" / "src" / "pages" / "Tool.test.tsx").write_text(f"const url = 'https://{internal_host}';\n", encoding="utf-8")
    (repo / "webui" / "src" / "pages" / "Tool.spec.ts").write_text(f"// {internal_host}\n", encoding="utf-8")
    (repo / "webui" / "src" / "test" / "setup.ts").write_text(f"// {internal_host}\n", encoding="utf-8")
    # AWS's own documentation sample key id is published as never valid: every scanner lets it through
    (repo / ".flocks" / "plugins" / "skills" / "demo" / "SKILL.md").write_text("aws_access_key_id = AKIA" + "IOSFODNN7EXAMPLE\n", encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run([GIT, "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run([GIT, "commit", "-qm", "webui"], cwd=repo, check=True, env=env)

    ok = subprocess.run([BASH, str(EXPORT_SCRIPT), str(repo), str(tmp_path / "ok")], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    files = _exported_files(tmp_path / "ok")
    assert {"webui/src/main.tsx", ".flocks/plugins/skills/demo/SKILL.md"} <= files
    assert not {f for f in files if f.startswith("webui/src/test/") or ".test." in f or ".spec." in f}, sorted(files)

    offending = {
        "an AWS access key id": "key = AKIA" + "Z" * 16,
        "a JWT": "token = eyJ" + "a" * 12 + ".eyJ" + "b" * 12 + "." + "c" * 12,
        "a private key block": "-----BEGIN " + "RSA PRIVATE KEY-----",
        "an internal host name": f"base_url = https://{internal_host}/api",
    }
    for index, (what, line) in enumerate(offending.items()):
        leak = repo / "flocks" / f"leak_{index}.py"
        leak.write_text(f"# {line}\n", encoding="utf-8")
        refused = subprocess.run([BASH, str(EXPORT_SCRIPT), str(repo), str(tmp_path / f"out-{index}"), "--allow-dirty"],
                                 capture_output=True, text=True)
        assert refused.returncode == 1, (what, refused.stdout, refused.stderr)
        assert "credential-like strings or internal host names" in refused.stderr and f"flocks/leak_{index}.py" in refused.stderr, what
        leak.unlink()
    # the same rule guards an already exported tree (build-offline-run.sh re-verifies the copy it packs)
    (tmp_path / "ok" / "flocks" / "late.md").write_text("AKIA" + "Q" * 16 + "\n", encoding="utf-8")
    verify = subprocess.run([BASH, str(EXPORT_SCRIPT), "--verify-only", str(tmp_path / "ok")], capture_output=True, text=True)
    assert verify.returncode == 1 and "flocks/late.md" in verify.stderr
    (tmp_path / "ok" / "flocks" / "late.md").unlink()

    # the build outputs a refreshed staging root holds are skipped, by their path only: a directory
    # further down that merely shares the name ships, so it is scanned
    for build_output in (".venv/lib/site.py", "webui/dist/assets/index.js", "webui/node_modules/pkg/index.js"):
        path = tmp_path / "ok" / build_output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("AKIA" + "R" * 16 + "\n", encoding="utf-8")
    verify = subprocess.run([BASH, str(EXPORT_SCRIPT), "--verify-only", str(tmp_path / "ok")], capture_output=True, text=True)
    assert verify.returncode == 0, verify.stdout + verify.stderr
    for nested_name in ("dist", "node_modules"):
        nested = tmp_path / "ok" / ".flocks" / "plugins" / "skills" / "demo" / "assets" / nested_name / "fixture.txt"
        nested.parent.mkdir(parents=True)
        nested.write_text(f"see https://{internal_host}\n", encoding="utf-8")
        verify = subprocess.run([BASH, str(EXPORT_SCRIPT), "--verify-only", str(tmp_path / "ok")], capture_output=True, text=True)
        assert verify.returncode == 1, (nested_name, verify.stdout, verify.stderr)
        assert f".flocks/plugins/skills/demo/assets/{nested_name}/fixture.txt" in verify.stderr
        nested.unlink()

    # a file the check could not read is not a clean file (root reads everything: nothing to show then)
    if os.geteuid() != 0:
        unreadable = tmp_path / "ok" / "flocks" / "unreadable.py"
        unreadable.write_text("x = 1\n", encoding="utf-8")
        unreadable.chmod(0)
        try:
            verify = subprocess.run([BASH, str(EXPORT_SCRIPT), "--verify-only", str(tmp_path / "ok")], capture_output=True, text=True)
        finally:
            unreadable.chmod(0o644)
        assert verify.returncode == 1 and "could not read every file" in verify.stderr, verify.stdout + verify.stderr


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


def test_workflow_builds_inside_the_manifest_build_image() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    image = json.loads(MANIFEST.read_text(encoding="utf-8"))["build_image"]
    assert f"image: {image}\n" in text
    assert "packaging/linux/build-offline-run.sh" in text
    assert "flocks-offline.run" in text
    assert "softprops/action-gh-release" in text
    # both architectures on native runners, arch-specific asset names
    assert "ubuntu-24.04-arm" in text and "ubuntu-latest" in text
    assert "flocks-offline-${{ matrix.arch }}.run" in text


def test_package_is_built_for_rhel8_glibc() -> None:
    # built on RHEL 8.10 so every wheel is picked for glibc 2.28; a CentOS Stream 9 build picked
    # cryptography's manylinux_2_34 wheel, which fails on RHEL 8 / Kylin / UOS with GLIBC_2.30 not found
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["build_image"] == "registry.access.redhat.com/ubi8/ubi:8.10"
    floor = data["glibc_floor"]
    assert (floor["GLIBC"], floor["GLIBCXX"], floor["CXXABI"]) == ("2.28", "3.4.25", "1.3.11")
    # the one-click builder and the smoke test default to that image instead of hard-coding one
    for script in (DOCKER_BUILD_SCRIPT, SMOKE_SCRIPT):
        text = script.read_text(encoding="utf-8")
        assert '"build_image"' in text and "versions.manifest.json" in text
        assert "stream9" not in text
    # the build runs the check on what ships and stops when it fails
    build = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert re.search(
        r'^"\$PYTHON_BIN" "\$SCRIPT_DIR/glibc_floor\.py" --manifest "\$MANIFEST" "\$INSTALL_ROOT/tools" "\$INSTALL_ROOT/flocks" \\\n\s+\|\| fail ',
        build,
        re.M,
    )


def _elf_needing(path: Path, needs: dict[str, list[str]], *, bits: int = 64, endian: str = "<") -> Path:
    """Write a minimal ELF file (ELF64 or ELF32, either byte order) whose .gnu.version_r asks for these versions."""
    import struct

    strtab = b"\0"

    def add(text: str) -> int:
        nonlocal strtab
        offset = len(strtab)
        strtab += text.encode() + b"\0"
        return offset

    if bits == 64:
        header_fmt, section_fmt, header_size, section_size = "HHIQQQIHHHHHH", "IIQQQQIIQQ", 64, 64
    else:
        header_fmt, section_fmt, header_size, section_size = "HHIIIIIHHHHHH", "IIIIIIIIII", 52, 40
    entries = [(add(library), [add(version) for version in versions]) for library, versions in needs.items()]
    verneed = b""
    for index, (library_off, version_offs) in enumerate(entries):
        count = len(version_offs)
        next_off = 16 + 16 * count if index < len(entries) - 1 else 0
        verneed += struct.pack(endian + "HHIII", 1, count, library_off, 16, next_off)
        for number, version_off in enumerate(version_offs):
            verneed += struct.pack(endian + "IHHII", 0, 0, 2 + number, version_off, 16 if number < count - 1 else 0)
    shstrtab = b"\0.dynstr\0.gnu.version_r\0.shstrtab\0"
    off_str = header_size
    off_vn = off_str + len(strtab)
    off_shs = off_vn + len(verneed)
    off_sh = off_shs + len(shstrtab)
    header = b"\x7fELF" + bytes([2 if bits == 64 else 1, 1 if endian == "<" else 2, 1]) + b"\0" * 9
    header += struct.pack(endian + header_fmt, 3, 62, 1, 0, 0, off_sh, 0, header_size, 0, 0, section_size, 4, 3)
    sections = struct.pack(endian + section_fmt, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    sections += struct.pack(endian + section_fmt, 1, 3, 0, 0, off_str, len(strtab), 0, 0, 1, 0)
    sections += struct.pack(endian + section_fmt, 9, 0x6FFFFFFE, 0, 0, off_vn, len(verneed), 1, len(entries), 4, 0)
    sections += struct.pack(endian + section_fmt, 24, 3, 0, 0, off_shs, len(shstrtab), 0, 0, 1, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + strtab + verneed + shstrtab + sections)
    return path


def _run_glibc_floor(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(GLIBC_FLOOR), *args], capture_output=True, text=True)


def test_glibc_floor_flags_binaries_that_need_more_than_rhel8(tmp_path: Path) -> None:
    root = tmp_path / "payload"
    _elf_needing(root / "ok" / "_speedups.so", {
        "libc.so.6": ["GLIBC_2.2.5", "GLIBC_2.17", "GLIBC_PRIVATE"],
        "libstdc++.so.6": ["GLIBCXX_3.4.21", "CXXABI_1.3.9", "CXXABI_TM_1"],
        "libgcc_s.so.1": ["GCC_3.0"],
    })
    # what a CentOS Stream 9 build shipped: cryptography's manylinux_2_34 wheel
    _elf_needing(root / "cryptography" / "_rust.abi3.so", {"libc.so.6": ["GLIBC_2.17", "GLIBC_2.34"]})
    _elf_needing(root / "newcxx" / "ext.so", {"libstdc++.so.6": ["GLIBCXX_3.4.29"]})
    # a wheel that vendors its own (renamed) libstdc++ brings those versions along: not a violation
    _elf_needing(root / "vendored" / "ext.so", {"libstdc++-a1b2c3d4.so.6.0.32": ["GLIBCXX_3.4.32"], "libc.so.6": ["GLIBC_2.28"]})
    # the other ELF layout: 32-bit, big-endian
    _elf_needing(root / "be32" / "lib.so", {"libc.so.6": ["GLIBC_2.0", "GLIBC_2.30"]}, bits=32, endian=">")
    (root / "notes.txt").write_text("not an ELF file\n", encoding="utf-8")
    (root / "link.so").symlink_to(root / "cryptography" / "_rust.abi3.so")

    failed = _run_glibc_floor("--manifest", str(MANIFEST), str(root))
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert "cryptography/_rust.abi3.so: needs GLIBC_2.34 (target has GLIBC_2.28)" in failed.stderr
    assert "newcxx/ext.so: needs GLIBCXX_3.4.29 (target has GLIBCXX_3.4.25)" in failed.stderr
    assert "be32/lib.so: needs GLIBC_2.30 (target has GLIBC_2.28)" in failed.stderr
    assert "vendored" not in failed.stderr and "_speedups" not in failed.stderr and "link.so" not in failed.stderr
    assert "5 ELF files checked" in failed.stdout

    (root / "link.so").unlink()
    (root / "be32" / "lib.so").unlink()
    (root / "cryptography" / "_rust.abi3.so").unlink()
    (root / "newcxx" / "ext.so").unlink()
    clean = _run_glibc_floor("--manifest", str(MANIFEST), str(root))
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert "highest GLIBC_2.28 needed by" in clean.stdout and "vendored" in clean.stdout
    assert "highest GLIBCXX_3.4.21 needed by" in clean.stdout

    # --floor overrides the manifest, e.g. to check against an even older target
    stricter = _run_glibc_floor("--manifest", str(MANIFEST), "--floor", "GLIBC=2.17", str(root))
    assert stricter.returncode == 1 and "needs GLIBC_2.28 (target has GLIBC_2.17)" in stricter.stderr
    assert _run_glibc_floor("--manifest", str(MANIFEST), "--floor", "GLIBC=two", str(root)).returncode != 0
    assert _run_glibc_floor("--manifest", str(MANIFEST), str(tmp_path / "missing")).returncode == 2


def test_glibc_floor_fails_on_elf_files_it_cannot_read_completely(tmp_path: Path) -> None:
    # a truncated or inconsistent ELF must not pass the gate as "needs nothing"
    import struct

    def fixture(name: str) -> tuple[Path, int, int]:
        path = _elf_needing(tmp_path / name / "_rust.abi3.so", {"libc.so.6": ["GLIBC_2.17", "GLIBC_2.34"]})
        data = path.read_bytes()
        sh_offset = struct.unpack_from("<Q", data, 40)[0]  # e_shoff
        vn_offset = struct.unpack_from("<Q", data, sh_offset + 2 * 64 + 24)[0]  # .gnu.version_r sh_offset
        return path, sh_offset, vn_offset

    def patch(path: Path, offset: int, data: bytes) -> None:
        blob = bytearray(path.read_bytes())
        blob[offset:offset + len(data)] = data
        path.write_bytes(bytes(blob))

    path, _, _ = fixture("intact")
    intact = _run_glibc_floor("--manifest", str(MANIFEST), str(path.parent))
    assert intact.returncode == 1 and "needs GLIBC_2.34" in intact.stderr

    expected = {}
    path, sh_offset, _ = fixture("truncated-table")
    path.write_bytes(path.read_bytes()[: sh_offset + 2 * 64 + 10])
    expected["truncated-table"] = "truncated section header table"
    path, sh_offset, _ = fixture("dangling-link")
    patch(path, sh_offset + 2 * 64 + 40, struct.pack("<I", 9))  # sh_link of .gnu.version_r
    expected["dangling-link"] = "version requirements link to missing section 9"
    path, _, vn_offset = fixture("short-chain")
    patch(path, vn_offset + 2, struct.pack("<H", 3))  # vn_cnt says 3 names, the list holds 2
    expected["short-chain"] = "version name list shorter than its count"
    path, _, vn_offset = fixture("bad-string")
    patch(path, vn_offset + 16 + 8, struct.pack("<I", 0xFFFF))  # first vna_name beyond the string table
    expected["bad-string"] = "string offset outside its string table"
    path, _, vn_offset = fixture("aux-on-itself")
    patch(path, vn_offset + 8, struct.pack("<I", 0))  # vn_aux = 0: the Verneed would be read as its own Vernaux
    expected["aux-on-itself"] = "overlapping version records (version name entry at offset 0)"
    path, _, vn_offset = fixture("aux-overlap")
    patch(path, vn_offset + 16 + 12, struct.pack("<I", 8))  # first vna_next lands inside the first Vernaux
    expected["aux-overlap"] = "overlapping version records (version name entry at offset 24)"
    path = _elf_needing(tmp_path / "loop" / "_rust.abi3.so", {"libc.so.6": ["GLIBC_2.34"], "libm.so.6": ["GLIBC_2.29"]})
    data = path.read_bytes()
    vn_loop = struct.unpack_from("<Q", data, struct.unpack_from("<Q", data, 40)[0] + 2 * 64 + 24)[0]
    patch(path, vn_loop + 12, struct.pack("<I", 16))  # first vn_next points into its own Vernaux
    expected["loop"] = "overlapping version records (version requirement entry at offset 16)"
    for size in (4, 15):  # ELF magic, but cut inside the 16-byte identification
        cut = tmp_path / f"ident-{size}" / "_rust.abi3.so"
        cut.parent.mkdir(parents=True)
        cut.write_bytes(fixture(f"ident-src-{size}")[0].read_bytes()[:size])
        expected[f"ident-{size}"] = "truncated ELF identification"
    for name, message in expected.items():
        run = _run_glibc_floor("--manifest", str(MANIFEST), str(tmp_path / name))
        assert run.returncode == 1, (name, run.stdout, run.stderr)
        assert f"{name}/_rust.abi3.so: cannot read its version requirements ({message})" in run.stderr

    def headerless(path: Path, p_type: int) -> None:
        header = b"\x7fELF" + bytes([2, 1, 1]) + b"\0" * 9
        header += struct.pack("<HHIQQQIHHHHHH", 3, 62, 1, 0, 64, 0, 0, 64, 56, 1, 64, 0, 0)
        path.parent.mkdir(parents=True)
        path.write_bytes(header + struct.pack("<IIQQQQQQ", p_type, 5, 0, 0, 0, 0, 0, 0x1000))

    headerless(tmp_path / "static" / "tool", 1)  # PT_LOAD only: a static binary, nothing to check
    assert _run_glibc_floor("--manifest", str(MANIFEST), str(tmp_path / "static")).returncode == 0
    headerless(tmp_path / "dynamic" / "tool", 2)  # PT_DYNAMIC without section headers: cannot be checked
    dynamic = _run_glibc_floor("--manifest", str(MANIFEST), str(tmp_path / "dynamic"))
    assert dynamic.returncode == 1 and "dynamically linked but has no section headers" in dynamic.stderr


def _intranet(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([BASH, str(INTRANET_SCRIPT), *args], capture_output=True, text=True, env=env)


def test_intranet_allowlist_is_the_manuals_server_rows() -> None:
    # the offline manual opens portalflocks.threatbook.cn (106.75.19.23, TCP 443) for the server;
    # passport.threatbook.cn is opened for the administrator's PC only
    rows = [
        line.split()
        for line in INTRANET_ALLOWLIST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert rows == [["portalflocks.threatbook.cn", "106.75.19.23", "443"]]
    rendered = _intranet("render")
    assert rendered.returncode == 0, rendered.stderr
    assert "passport" not in rendered.stdout


def test_intranet_sim_renders_dns_and_egress_rules_from_the_allowlist(tmp_path: Path) -> None:
    allowlist = tmp_path / "allow.txt"
    allowlist.write_text(
        "# comment\n\nportal.example.cn 10.0.0.1,10.0.0.2 443  # trailing comment\nmirror.example.cn 10.0.0.9 8443\n",
        encoding="utf-8",
    )
    result = _intranet("render", "--allowlist", str(allowlist))
    assert result.returncode == 0, result.stderr
    script = result.stdout
    for record in ("host-record=portal.example.cn,10.0.0.1", "host-record=portal.example.cn,10.0.0.2",
                   "host-record=mirror.example.cn,10.0.0.9"):
        assert record in script
    assert "address=/#/" in script and "no-resolv" in script and "listen-address=127.0.0.1" in script
    assert "printf 'nameserver 127.0.0.1\\n' > /etc/resolv.conf" in script
    accepts = [line for line in script.splitlines() if line.startswith("iptables -A OUTPUT -p tcp")]
    assert accepts == [
        "iptables -A OUTPUT -p tcp -d 10.0.0.1 --dport 443 -j ACCEPT  # portal.example.cn",
        "iptables -A OUTPUT -p tcp -d 10.0.0.2 --dport 443 -j ACCEPT  # portal.example.cn",
        "iptables -A OUTPUT -p tcp -d 10.0.0.9 --dport 8443 -j ACCEPT  # mirror.example.cn",
    ]
    lines = script.splitlines()
    # the DROP policy comes after loopback, replies and the allowlist, for both address families
    assert lines.index("iptables -P OUTPUT DROP") > max(lines.index(line) for line in accepts)
    assert lines.index("iptables -A OUTPUT -o lo -j ACCEPT") < lines.index("iptables -P OUTPUT DROP")
    assert "iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT" in lines
    assert lines[-1] == "ip6tables -P OUTPUT DROP"
    # the rendered gateway script is valid POSIX sh
    syntax = subprocess.run(["sh", "-n"], input=script, capture_output=True, text=True)
    assert syntax.returncode == 0, syntax.stderr


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("portal.example.cn 300.1.1.1 443\n", "not an IPv4 address: 300.1.1.1"),
        ("portal.example.cn 10.0.0.1 0\n", "not a TCP port: 0"),
        ("portal.example.cn 10.0.0.1 70000\n", "not a TCP port: 70000"),
        ("portal.example.cn 10.0.0.1\n", "expected '<domain> <ipv4>[,<ipv4>...] <tcp port>'"),
        ("portal.example.cn 10.0.0.1 443 tcp\n", "expected '<domain> <ipv4>[,<ipv4>...] <tcp port>'"),
        ("portal_example 10.0.0.1 443\n", "not a domain name: portal_example"),
        ("# nothing allowed\n", "allows nothing"),
    ],
)
def test_intranet_sim_refuses_a_malformed_allowlist(tmp_path: Path, content: str, message: str) -> None:
    allowlist = tmp_path / "allow.txt"
    allowlist.write_text(content, encoding="utf-8")
    result = _intranet("render", "--allowlist", str(allowlist))
    assert result.returncode == 2
    assert message in result.stderr
    assert "iptables" not in result.stdout


def _probe_stubs(bin_dir: Path, *, leaks: str = "", udp_leak: str = "") -> None:
    """getent / timeout / curl stand-ins: the allowlisted endpoint answers, everything else is dead,
    except what `leaks` names (a public name that resolves or an address that connects) and the
    resolver `udp_leak` names (answers a UDP DNS question)."""
    bin_dir.mkdir()
    scripts = {
        "getent": f"""#!/bin/sh
case "$2" in
  portalflocks.threatbook.cn) echo "106.75.19.23    STREAM portalflocks.threatbook.cn"; exit 0 ;;
  {leaks or "never.invalid"}) echo "1.2.3.4 STREAM $2"; exit 0 ;;
esac
exit 2
""",
        # tcp_open runs: timeout 6 bash -c "exec 3<>/dev/tcp/<ip>/<port>"
        "timeout": f"""#!/bin/sh
case "$4" in
  *"/dev/tcp/106.75.19.23/443") exit 0 ;;
  *"/dev/tcp/{leaks or "never.invalid"}") exit 0 ;;
  *"/dev/udp/{udp_leak or "never.invalid"}/53 "*) printf x; exit 0 ;;
esac
exit 124
""",
        "curl": "#!/bin/sh\nprintf 502\n",
    }
    for name, body in scripts.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)


@pytest.mark.parametrize(
    ("leaks", "udp_leak", "failure"),
    [
        ("", "", None),
        ("pypi.org", "", "FAIL  pypi.org resolves"),
        ("106.75.36.224/443", "", "FAIL  106.75.36.224:443 reachable"),
        ("106.75.19.23/80", "", "FAIL  106.75.19.23:80 reachable but not allowlisted"),
        ("", "8.8.8.8", "FAIL  UDP DNS to 8.8.8.8 answered"),
    ],
)
def test_intranet_check_fails_on_any_leak(tmp_path: Path, leaks: str, udp_leak: str, failure: str | None) -> None:
    probe = _intranet("render-check")
    assert probe.returncode == 0, probe.stderr
    assert "PASS  UDP DNS to" not in probe.stdout  # the verdicts are computed in the container
    _probe_stubs(tmp_path / "bin", leaks=leaks, udp_leak=udp_leak)
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run([BASH, "-s"], input=probe.stdout, capture_output=True, text=True, env=env)
    assert "PASS  portalflocks.threatbook.cn resolves to 106.75.19.23" in result.stdout
    assert "PASS  106.75.19.23:443 (portalflocks.threatbook.cn) reachable" in result.stdout
    assert "PASS  https://portalflocks.threatbook.cn/ answers (HTTP 502)" in result.stdout
    if failure is None:
        assert result.returncode == 0, result.stdout
        assert "FAIL" not in result.stdout
    else:
        assert result.returncode == 1, result.stdout
        assert [line for line in result.stdout.splitlines() if line.startswith("FAIL")] == [failure]


def test_intranet_check_never_probes_a_port_the_allowlist_opens(tmp_path: Path) -> None:
    allowlist = tmp_path / "allow.txt"
    allowlist.write_text("portal.example.cn 10.0.0.1 443\nweb.example.cn 10.0.0.1 80\n", encoding="utf-8")
    probe = _intranet("render-check", "--allowlist", str(allowlist))
    assert probe.returncode == 0, probe.stderr
    blocked_probes = re.findall(r"if tcp_open 10\.0\.0\.1 (\d+); then fail", probe.stdout)
    assert blocked_probes and not {"80", "443"} & set(blocked_probes), blocked_probes


@pytest.mark.parametrize(
    ("family", "rules", "verdict"),
    [
        ("4", "-P OUTPUT DROP\n-A OUTPUT -o lo -j ACCEPT\n-A OUTPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT\n"
              "-A OUTPUT -d 106.75.19.23/32 -p tcp -m tcp --dport 443 -j ACCEPT\n", None),
        ("6", "-P OUTPUT DROP\n-A OUTPUT -o lo -j ACCEPT\n-A OUTPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT\n", None),
        ("4", "-P OUTPUT ACCEPT\n-A OUTPUT -o lo -j ACCEPT\n", "FAIL  IPv4 OUTPUT policy is ACCEPT, not DROP"),
        ("4", "-P OUTPUT DROP\n-A OUTPUT -p udp -m udp --dport 53 -j ACCEPT\n",
         "FAIL  IPv4 OUTPUT rule not written by intranet-sim: -A OUTPUT -p udp -m udp --dport 53 -j ACCEPT"),
        ("4", "-P OUTPUT DROP\n-A OUTPUT -d 106.75.19.23/32 -j ACCEPT\n",
         "FAIL  IPv4 OUTPUT rule not written by intranet-sim: -A OUTPUT -d 106.75.19.23/32 -j ACCEPT"),
        ("4", "-P OUTPUT DROP\n-A OUTPUT -d 106.75.19.23/32 -p tcp -m tcp --dport 80 -j ACCEPT\n",
         "FAIL  IPv4 OUTPUT rule not written by intranet-sim: -A OUTPUT -d 106.75.19.23/32 -p tcp -m tcp --dport 80 -j ACCEPT"),
        ("4", "-P OUTPUT DROP\n-A OUTPUT -m conntrack --ctstate NEW,RELATED,ESTABLISHED -j ACCEPT\n",
         "FAIL  IPv4 OUTPUT rule not written by intranet-sim: -A OUTPUT -m conntrack --ctstate NEW,RELATED,ESTABLISHED -j ACCEPT"),
        ("6", "-P OUTPUT DROP\n-A OUTPUT -d ::/0 -p tcp -m tcp --dport 443 -j ACCEPT\n",
         "FAIL  IPv6 OUTPUT rule not written by intranet-sim: -A OUTPUT -d ::/0 -p tcp -m tcp --dport 443 -j ACCEPT"),
        ("6", "", "FAIL  IPv6 OUTPUT policy is missing, not DROP"),
        # an unconditional ACCEPT, a jump or goto to another chain, a comment that looks like a DROP
        ("4", "-P OUTPUT DROP\n-A OUTPUT -j ACCEPT\n", "FAIL  IPv4 OUTPUT rule not written by intranet-sim: -A OUTPUT -j ACCEPT"),
        ("6", "-P OUTPUT DROP\n-A OUTPUT -j ACCEPT\n", "FAIL  IPv6 OUTPUT rule not written by intranet-sim: -A OUTPUT -j ACCEPT"),
        ("4", "-P OUTPUT DROP\n-A OUTPUT -j LEAK\n", "FAIL  IPv4 OUTPUT rule not written by intranet-sim: -A OUTPUT -j LEAK"),
        ("4", "-P OUTPUT DROP\n-A OUTPUT -g LEAK\n", "FAIL  IPv4 OUTPUT rule not written by intranet-sim: -A OUTPUT -g LEAK"),
        ("4", '-P OUTPUT DROP\n-A OUTPUT -m comment --comment " -j DROP " -j ACCEPT\n',
         'FAIL  IPv4 OUTPUT rule not written by intranet-sim: -A OUTPUT -m comment --comment " -j DROP " -j ACCEPT'),
        ("4", '-P OUTPUT DROP\n-A OUTPUT -m comment --comment " -j DROP " -g LEAK\n',
         'FAIL  IPv4 OUTPUT rule not written by intranet-sim: -A OUTPUT -m comment --comment " -j DROP " -g LEAK'),
        ("6", '-P OUTPUT DROP\n-A OUTPUT -m comment --comment " -j REJECT " -j LEAK\n',
         'FAIL  IPv6 OUTPUT rule not written by intranet-sim: -A OUTPUT -m comment --comment " -j REJECT " -j LEAK'),
        ("4", "-P OUTPUT DROP\n-A OUTPUT -j DROP -j LEAK\n", "FAIL  IPv4 OUTPUT rule not written by intranet-sim: -A OUTPUT -j DROP -j LEAK"),
        ("4", "-P OUTPUT DROP\n-A OUTPUT -m comment --comment drop -j DROP\n",
         "FAIL  IPv4 OUTPUT rule not written by intranet-sim: -A OUTPUT -m comment --comment drop -j DROP"),
        # rules that can only take traffic away are fine
        ("4", "-P OUTPUT DROP\n-A OUTPUT -p udp -j REJECT --reject-with icmp-port-unreachable\n-A OUTPUT -d 1.1.1.1/32 -j DROP\n", None),
    ],
)
def test_intranet_rule_check_accepts_only_the_rendered_rules(family: str, rules: str, verdict: str | None) -> None:
    result = subprocess.run([BASH, str(INTRANET_SCRIPT), "verify-rules", "--family", family], input=rules,
                            capture_output=True, text=True)
    if verdict is None:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "FAIL" not in result.stdout
    else:
        assert result.returncode == 1, result.stdout + result.stderr
        assert verdict in result.stdout.splitlines()


def test_smoke_cleanup_never_removes_a_gateway_it_did_not_create(tmp_path: Path) -> None:
    # an existing intranet-sim gateway with the run's name (e.g. kept by an earlier SMOKE_KEEP=1 run
    # with the same PID) makes `up` refuse; the EXIT trap must then remove nothing at all
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "docker-calls.log"
    docker = bin_dir / "docker"
    docker.write_text(f"""#!/bin/sh
echo "$*" >> {calls}
if [ "$1" = inspect ]; then
  case "$*" in *Labels*) echo 1 ;; esac
  exit 0
fi
exit 0
""", encoding="utf-8")
    docker.chmod(0o755)
    run_file = tmp_path / "flocks-offline.run"
    run_file.write_text("not a real package\n", encoding="utf-8")
    # DOCKER too: intranet-sim.sh honours it, and a real CLI must never be reached from here
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "DOCKER": str(docker)}
    env.pop("SMOKE_KEEP", None)
    env.pop("SMOKE_NETWORK", None)
    result = subprocess.run([BASH, str(SMOKE_SCRIPT), str(run_file), "some/image"], capture_output=True, text=True,
                            env=env, timeout=60)
    assert result.returncode != 0
    assert "already exists" in result.stderr
    logged = calls.read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("inspect ") and line.endswith("-gw") for line in logged), logged
    assert not [line for line in logged if line.startswith(("rm", "run", "create", "start", "stop", "kill"))], logged


def test_smoke_cleanup_removes_what_it_created_even_when_it_never_started(tmp_path: Path) -> None:
    # the server container is created but does not start: the run fails, and the cleanup still
    # removes that container and the gateway this run brought up, leaving nothing behind
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls, state = tmp_path / "docker-calls.log", tmp_path / "docker-state"
    state.write_text("", encoding="utf-8")
    docker = bin_dir / "docker"
    docker.write_text(f"""#!/bin/bash
echo "$*" >> {calls}
case "$1" in
  inspect)
    name="${{@: -1}}"
    grep -qx "$name" {state} || exit 1
    case "$*" in *Labels*) echo 1 ;; esac
    exit 0 ;;
  create)
    shift
    while [ "$#" -gt 0 ]; do if [ "$1" = --name ]; then echo "$2" >> {state}; break; fi; shift; done
    exit 0 ;;
  start)
    case "$2" in *-gw) exit 0 ;; *) echo "start failed" >&2; exit 1 ;; esac ;;
  rm)
    name="${{@: -1}}"
    grep -vx "$name" {state} > {state}.new || true
    mv {state}.new {state}
    exit 0 ;;
esac
exit 0
""", encoding="utf-8")
    docker.chmod(0o755)
    run_file = tmp_path / "flocks-offline.run"
    run_file.write_text("not a real package\n", encoding="utf-8")
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "DOCKER": str(docker)}
    env.pop("SMOKE_KEEP", None)
    env.pop("SMOKE_NETWORK", None)
    result = subprocess.run([BASH, str(SMOKE_SCRIPT), str(run_file), "some/image"], capture_output=True, text=True,
                            env=env, timeout=60)
    assert result.returncode != 0
    logged = calls.read_text(encoding="utf-8").splitlines()
    created = [line.split("--name ", 1)[1].split()[0] for line in logged if line.startswith("create ")]
    assert len(created) == 2 and created[0].endswith("-gw"), logged  # gateway, then the server
    removed = [line.split()[-1] for line in logged if line.startswith("rm ")]
    assert set(removed) == set(created), logged
    assert state.read_text(encoding="utf-8").strip() == "", "left behind: " + state.read_text(encoding="utf-8")


def test_intranet_sim_stops_on_a_malformed_allowlist_before_touching_docker(tmp_path: Path) -> None:
    # a good first line must not carry the run: the whole file is parsed before anything happens
    allowlist = tmp_path / "allow.txt"
    allowlist.write_text("portalflocks.threatbook.cn 106.75.19.23 443\nbroken.example.cn 10.0.0.1\n", encoding="utf-8")
    calls = tmp_path / "docker-calls.log"
    docker = tmp_path / "docker"
    docker.write_text(f"#!/bin/sh\necho \"$*\" >> {calls}\nexit 0\n", encoding="utf-8")
    docker.chmod(0o755)
    env = {**os.environ, "DOCKER": str(docker)}
    for args in (["check", "server"], ["up", "--name", "gw"], ["render-check"], ["render"]):
        result = _intranet(*args, "--allowlist", str(allowlist), env=env)
        assert result.returncode == 2, (args, result.stdout, result.stderr)
        assert f"{allowlist}:2: expected" in result.stderr
    rules = subprocess.run([BASH, str(INTRANET_SCRIPT), "verify-rules", "--family", "4", "--allowlist", str(allowlist)],
                           input="-P OUTPUT DROP\n", capture_output=True, text=True, env=env)
    assert rules.returncode == 2 and "PASS" not in rules.stdout
    assert not calls.exists()


def test_smoke_test_runs_behind_the_simulated_intranet_by_default() -> None:
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert 'NETWORK_MODE="${SMOKE_NETWORK:-intranet}"' in text
    assert 'intranet) NET_ARGS=(--network "container:$GATEWAY") ;;' in text
    assert 'none) NET_ARGS=(--network none) ;;' in text
    assert 'intranet-sim.sh" up --name "$GATEWAY"' in text
    assert 'intranet-sim.sh" check "$NAME"' in text
    assert 'intranet-sim.sh" down --name "$GATEWAY"' in text
    # both ways of starting the server use the chosen network, never a hard-coded one
    assert text.count('"${NET_ARGS[@]}"') == 2 and "--network none --" not in text


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
    installer = payload / "installer" / "install.sh"
    installer.write_text(installer.read_text().replace(
        'LOG_FILE="/var/log/flocks-offline-install.log"', f'LOG_FILE="{tmp_path / "installer.log"}"',
    ))
    # what the build's relocate scan writes; nothing in this fake payload names the build root
    (payload / "installer" / "relocate.list").write_text(
        f"# flocks relocate list, root {tmp_path / 'opt-flocks'}\n", encoding="utf-8"
    )
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
    # A port the installer's "port already taken" preflight will find free. Not one the OS hands out
    # for outgoing connections (ephemeral range, 32768+ on Linux, 49152+ on macOS): on a busy machine
    # such a port is reused before the dry run gets to probe it. Checked the way port_in_use does.
    import random
    import socket

    for _ in range(50):
        port = random.randint(20000, 29999)
        with socket.socket() as sock:
            try:
                sock.bind(("0.0.0.0", port))
            except OSError:
                continue
        return port
    raise RuntimeError("no free port in 20000-29999")


def _dry_run(
    payload: Path,
    data_home: Path | None = None,
    env_file: Path | None = None,
    *,
    base: str | None = None,
    wrapper: Path | None = None,
    unit: Path | None = None,
    derive_paths: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    # an unused port so the "port already taken" preflight never trips on a dev machine; the data
    # home and the env file point into the temp dir so the preflight never reads the real
    # /var/lib/flocks or /etc/flocks/flocks.env, except where a test checks the paths the installer
    # derives from a base directory (base= or derive_paths=). The CLI wrapper and the systemd unit an
    # earlier install leaves behind are looked up in the temp dir as well.
    env = {**os.environ, "FLOCKS_OFFLINE_DRY_RUN": "1", "FLOCKS_OFFLINE_PORT": str(_free_port())}
    for key in ("FLOCKS_OFFLINE_BASE", "FLOCKS_OFFLINE_DATA_HOME", "FLOCKS_OFFLINE_ENV_FILE"):
        env.pop(key, None)
    if base is not None:
        env["FLOCKS_OFFLINE_BASE"] = base
    if data_home is not None or (base is None and not derive_paths):
        env["FLOCKS_OFFLINE_DATA_HOME"] = str(data_home if data_home is not None else payload.parent / "data-home")
    if env_file is not None or (base is None and not derive_paths):
        env["FLOCKS_OFFLINE_ENV_FILE"] = str(env_file if env_file is not None else payload.parent / "etc-flocks" / "flocks.env")
    env["FLOCKS_OFFLINE_WRAPPER"] = str(wrapper if wrapper is not None else payload.parent / "usr-local-bin-flocks")
    env["FLOCKS_OFFLINE_UNIT_FILE"] = str(unit if unit is not None else payload.parent / "flocks.service")
    env.update(extra_env or {})
    return subprocess.run([BASH, str(payload / "installer" / "install.sh")], capture_output=True, text=True, env=env, cwd=payload)


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_installer_refuses_to_drop_activated_pro_without_a_compatible_bundle(tmp_path: Path) -> None:
    """R2: a new package without Pro must not silently turn an activated instance into OSS."""
    payload = _fake_payload(tmp_path, with_bundle=False)

    # no previous bundle to carry over (Pro was upgraded online on an open-source package) → refuse
    # before changing anything, and point at the online upgrade instead of a package with Pro
    data_home = _fake_activated_install(tmp_path, old_bundle_core=None)
    result = _dry_run(payload, data_home)
    assert result.returncode == 1
    assert "已激活 Flocks Pro" in result.stderr and "本次未做任何改动" in result.stderr
    assert "在线升级" in result.stderr

    # same state on an offline (Pro-bundle) install whose bundle went missing: it cannot upgrade online,
    # so the refusal must point at the matching Pro package instead
    env_file = tmp_path / "offline.env"
    env_file.write_text("FLOCKS_DEPLOY_MODE=offline\nFLOCKS_PRO_BUNDLE_DIR=/opt/flocks/bundle\n", encoding="utf-8")
    result = _dry_run(payload, data_home, env_file=env_file)
    assert result.returncode == 1
    assert "带 Pro 组件的配套安装包" in result.stderr and "本次未做任何改动" in result.stderr
    assert "在线升级" not in result.stderr

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
def test_installer_refuses_to_downgrade_an_install_that_upgraded_online(tmp_path: Path) -> None:
    """Online upgrades are allowed now, so an older package must not be laid over newer code and data."""
    def existing_install(version: str) -> None:
        repo = tmp_path / "opt-flocks" / "flocks"
        repo.mkdir(parents=True, exist_ok=True)
        (repo / "pyproject.toml").write_text(f'[project]\nname = "flocks"\nversion = "{version}"\n', encoding="utf-8")

    payload = _fake_payload(tmp_path, core_version="v2026.9.23")
    existing_install("v2026.9.30")  # upgraded online after the first install
    result = _dry_run(payload)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "v2026.9.30" in result.stderr and "v2026.9.23" in result.stderr and "本次未做任何改动" in result.stderr

    for installed in ("v2026.9.23", "v2026.9.14", "v2026.8.12"):  # same version (repair) and older ones
        existing_install(installed)
        result = _dry_run(payload)
        assert result.returncode == 0, f"{installed}: {result.stdout}{result.stderr}"

    # calendar versions compare numerically, not as strings (2026.10.x is newer than 2026.9.x)
    existing_install("v2026.10.1")
    result = _dry_run(payload)
    assert result.returncode == 1 and "v2026.10.1" in result.stderr


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_installer_lets_an_older_package_recover_a_failed_online_upgrade(tmp_path: Path) -> None:
    """The handoff swaps the source in before uv sync / npm and does not roll back: the package is the way back."""
    repo = tmp_path / "opt-flocks" / "flocks"
    repo.mkdir(parents=True)
    (repo / "pyproject.toml").write_text('[project]\nname = "flocks"\nversion = "v2026.9.30"\n', encoding="utf-8")
    run_dir = tmp_path / "data-home" / ".flocks" / "run"
    run_dir.mkdir(parents=True)
    result_file = run_dir / "upgrade-result.json"
    payload = _fake_payload(tmp_path, core_version="v2026.9.23")

    for phase, stage in (("failed", "install"), ("running", None)):  # running: the helper died half way (reboot, OOM)
        record = {"phase": phase, "version": "v2026.9.30", "current_version": "2026.9.23"}
        if stage:
            record["failed_stage"] = stage
        result_file.write_text(json.dumps(record), encoding="utf-8")
        result = _dry_run(payload)
        assert result.returncode == 0, f"{phase}: {result.stdout}{result.stderr}"
        assert "没有完成" in result.stdout
        # a helper that may have started the new version gets the data warning, a failed install does not
        assert ("启动过" in result.stdout) == (phase == "running")

    refused = [
        {"phase": "done", "version": "v2026.9.30", "current_version": "2026.9.23"},  # the newer version finished
        {"phase": "failed", "version": "v2026.9.28", "current_version": "2026.9.23"},  # a different upgrade
        {"phase": "failed", "version": "v2026.9.30"},  # no idea what ran before it
        {"phase": "failed", "version": "v2026.9.30", "current_version": "2026.9.26"},  # package older than that
        [1, 2],  # not a record at all
    ]
    for record in refused:
        result_file.write_text(json.dumps(record), encoding="utf-8")
        result = _dry_run(payload)
        assert result.returncode == 1, f"{record}: {result.stdout}{result.stderr}"
        assert "FLOCKS_OFFLINE_ALLOW_DOWNGRADE=1" in result.stderr
        assert "Traceback" not in result.stderr

    # an explicit operator decision lets it through, with a warning
    env = {**os.environ, "FLOCKS_OFFLINE_DRY_RUN": "1", "FLOCKS_OFFLINE_PORT": str(_free_port()),
           "FLOCKS_OFFLINE_DATA_HOME": str(tmp_path / "data-home"),
           "FLOCKS_OFFLINE_ENV_FILE": str(tmp_path / "etc-flocks" / "flocks.env"),
           "FLOCKS_OFFLINE_ALLOW_DOWNGRADE": "1"}
    result = subprocess.run([BASH, str(payload / "installer" / "install.sh")], capture_output=True, text=True, env=env, cwd=payload)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FLOCKS_OFFLINE_ALLOW_DOWNGRADE=1" in result.stdout and "警告" in result.stdout


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_installer_upgrade_mode_follows_the_pro_bundle(tmp_path: Path) -> None:
    """Open-source package → online upgrades; a Pro bundle on the machine (shipped or kept) → offline."""
    payload = _fake_payload(tmp_path, with_bundle=False)
    result = _dry_run(payload)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "upgrade=online" in result.stdout

    shutil.rmtree(payload)
    payload = _fake_payload(tmp_path, with_bundle=True)
    result = _dry_run(payload)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "upgrade=offline" in result.stdout

    # open-source package over an activated offline instance: its bundle is carried over, so it stays offline
    shutil.rmtree(payload)
    payload = _fake_payload(tmp_path, with_bundle=False)
    data_home = _fake_activated_install(tmp_path, old_bundle_core="v2026.9.14")
    result = _dry_run(payload, data_home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "carry-over-bundle=1" in result.stdout and "upgrade=offline" in result.stdout


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
    # IR is the install root the script read from /usr/local/bin/flocks; no bundled python there here
    script = f"set -euo pipefail\nIR=/nonexistent-install-root\n{source}\npw=\"$(gen_password)\"\nprintf '%s\\n' \"$pw\"\n"

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
    # ENV_FILE is what the script read from /usr/local/bin/flocks (default layout or below a base)
    assert '"$ENV_FILE"' in source
    script = f"set -euo pipefail\nENV_FILE='{env_file}'\n{source}\ninstalled_port\n"
    assert subprocess.run([BASH, "-c", script], capture_output=True, text=True).stdout.strip() == "5273"
    env_file.write_text("FLOCKS_PORT=oops\n", encoding="utf-8")
    assert subprocess.run([BASH, "-c", script], capture_output=True, text=True).stdout.strip() == "5173"
    env_file.unlink()
    assert subprocess.run([BASH, "-c", script], capture_output=True, text=True).stdout.strip() == "5173"


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_verify_script_reads_the_data_home_with_or_without_quotes(tmp_path: Path) -> None:
    """HOME="..." is as valid in the EnvironmentFile as the unquoted line the installer writes."""
    source = _bash_function_source(VERIFY_SCRIPT, "installed_data_home")
    env_file = tmp_path / "flocks.env"
    script = f"set -euo pipefail\nENV_FILE='{env_file}'\n{source}\ninstalled_data_home\n"
    for line, expected in (
        ("HOME=/data/flocks/data", "/data/flocks/data"),
        ('HOME="/data/flocks/data"', "/data/flocks/data"),
        ("HOME='/data/flocks/data'", "/data/flocks/data"),
        ("FLOCKS_PORT=5273", "/var/lib/flocks"),  # no HOME line: the default layout
    ):
        env_file.write_text(f"FLOCKS_HOST=0.0.0.0\n{line}\n", encoding="utf-8")
        completed = subprocess.run([BASH, "-c", script], capture_output=True, text=True)
        assert completed.returncode == 0 and completed.stdout.strip() == expected, (line, completed.stdout, completed.stderr)


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
    # readable for the text reads up front, broken for json_get
    (payload / "versions.json").write_text(
        f'{{"install_root": "{tmp_path / "opt-flocks"}", "arch": "{os.uname().machine}", not json', encoding="utf-8"
    )
    completed = _dry_run(payload)
    assert completed.returncode == 1
    assert "错误: 命令失败:" in completed.stderr and "json_get" in completed.stderr and "安装未完成" in completed.stderr


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
    """Retained copy + broken versions.json: whatever fails first, nothing may be parked before the
    script knows it is not running inside an install root — the live instance under /opt/flocks
    stays exactly as it was, and fail() says nothing on stdout (it may run inside a $(...))."""
    install_root = tmp_path / "opt-flocks"
    (install_root / "installer").mkdir(parents=True)
    for name in INSTALLER_FILES:
        shutil.copy(INSTALLER_DIR / name, install_root / "installer" / name)
    python_dir = install_root / "tools" / "python" / "bin"
    python_dir.mkdir(parents=True)
    os.symlink(sys.executable, python_dir / "python3")
    (install_root / "flocks").mkdir()
    (install_root / "flocks" / "pyproject.toml").write_text('[project]\nname = "flocks"\n', encoding="utf-8")
    for broken, says in (
        ("{broken json", "install_root 无效"),  # not even the build root can be read
        (f'{{"install_root": "{install_root}", "arch": "{os.uname().machine}", broken', "不能直接执行"),
    ):
        (install_root / "versions.json").write_text(broken, encoding="utf-8")
        before = sorted(x.name for x in install_root.iterdir())
        completed = subprocess.run(
            [BASH, str(install_root / "installer" / "install.sh")], capture_output=True, text=True,
            env={**os.environ, "FLOCKS_OFFLINE_DRY_RUN": "0", "FLOCKS_OFFLINE_DATA_HOME": str(tmp_path / "data-home")},
        )
        assert completed.returncode == 1
        assert says in completed.stderr
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


@pytest.fixture
def pg_preflight_module():
    if sys.platform == "win32":
        pytest.skip("Linux installer helper requires Unix file ownership")
    spec = importlib.util.spec_from_file_location("postgres_preflight", INSTALLER_DIR / "postgres_preflight.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_postgres_preflight_is_packaged_and_precedes_service_mutations():
    source = INSTALL_SCRIPT.read_text()
    call = source.index('"$SRC_ROOT/installer/postgres_preflight.py" prepare')
    assert call < source.index('useradd --system')
    assert call < source.index('systemctl stop flocks')
    assert call < source.index('mv "$INSTALL_ROOT/$dir"')
    assert '"$SCRIPT_DIR/installer/postgres_preflight.py"' in BUILD_SCRIPT.read_text()
    assert 'FLOCKS_DB_DSN_FILE=@ENV_FILE@.postgres-dsn' in ENV_TEMPLATE.read_text()


def _postgres_install_attempt(tmp_path, *, database_env=None, existing=""):
    payload = _fake_payload(tmp_path)
    import asyncpg
    vendored = payload / "flocks" / ".venv" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"
    vendored.mkdir(parents=True)
    (vendored / "site-packages").symlink_to(Path(asyncpg.__file__).resolve().parents[1], target_is_directory=True)
    installed = tmp_path / "opt-flocks" / "flocks"
    cli = installed / ".venv" / "bin" / "flocks"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/sh\nexit 0\n")
    cli.chmod(0o755)
    (installed / "pyproject.toml").write_text("existing installation\n")
    env_file = tmp_path / "flocks.env"
    env_file.write_text("FLOCKS_PORT=5173\n" + existing)
    original = env_file.read_bytes()
    events = tmp_path / "events"
    stubs = tmp_path / "stub-bin"
    stubs.mkdir()
    bodies = {
        "id": "printf '0\\n'", "getent": "exit 0", "chown": "exit 0",
        "useradd": "exit 92", "systemctl": "exit 93",
        "runuser": "printf 'stop\\n' >> \"$INSTALLER_EVENTS\"; exit 0",
        # Stop the successful branch immediately after its controlled stop;
        # never mutate the old installation or run any real service command.
        "mv": "printf 'move\\n' >> \"$INSTALLER_EVENTS\"; exit 94",
    }
    for name, body in bodies.items():
        executable = stubs / name
        executable.write_text("#!/bin/sh\n" + body + "\n")
        executable.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if not key.startswith("FLOCKS_")}
    # The FLOCKS_* filter above also drops FLOCKS_OFFLINE_HOME_CANDIDATES: put it back so the
    # installer never picks up (and tries to migrate) a real ~/.flocks on this machine.
    env.update(FLOCKS_OFFLINE_DATA_HOME=str(tmp_path / "service-home"), FLOCKS_OFFLINE_ENV_FILE=str(env_file),
               FLOCKS_OFFLINE_HOME_CANDIDATES="",
               FLOCKS_OFFLINE_SKIP_SYSTEMD="1", FLOCKS_OFFLINE_SKIP_FIREWALL="1",
               HOME=str(tmp_path), USERPROFILE=str(tmp_path), PYTHON_DOTENV_DISABLED="1",
               PATH=str(stubs) + os.pathsep + env.get("PATH", ""), INSTALLER_EVENTS=str(events))
    env.update(database_env or {})
    result = subprocess.run([BASH, str(payload / "installer" / "install.sh")],
                            env=env, cwd=payload, capture_output=True, text=True, timeout=25)
    assert env_file.read_bytes() == original
    assert (installed / "pyproject.toml").read_text() == "existing installation\n"
    return result, events.read_text().splitlines() if events.exists() else [], payload, env_file


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_postgres_missing_configuration_fails_before_stopping_old_service(tmp_path):
    result, events, _, _ = _postgres_install_attempt(tmp_path)
    assert result.returncode != 0
    assert "必须配置 FLOCKS_DB_DSN" in result.stderr
    assert "stop" not in events


@pytest.mark.skipif(BASH is None or not os.getenv("FLOCKS_TEST_PG_DSN"), reason="requires bash and isolated PostgreSQL")
def test_postgres_unreachable_connection_fails_before_stopping_old_service(tmp_path):
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    from uuid import uuid4
    parsed = urlsplit(os.environ["FLOCKS_TEST_PG_DSN"])
    # A socket directory that does not exist is unreachable whether the test DSN
    # names a TCP host and port (CI) or a local socket. Kept short: a socket path
    # over the platform limit fails with a different error.
    query = dict(parse_qsl(parsed.query))
    query["host"] = f"/tmp/flocks-no-postgres-{uuid4().hex[:12]}"
    userinfo = parsed.netloc.rpartition("@")[0]
    netloc = userinfo + "@" if userinfo else ""
    bad_dsn = urlunsplit(parsed._replace(netloc=netloc, query=urlencode(query)))
    result, events, _, _ = _postgres_install_attempt(tmp_path, database_env={"FLOCKS_DB_DSN": bad_dsn})
    assert result.returncode != 0 and "stop" not in events
    assert "PostgreSQL" in result.stderr
    assert "FileNotFoundError" in result.stderr
    assert bad_dsn not in result.stdout + result.stderr


@pytest.mark.skipif(BASH is None or not os.getenv("FLOCKS_TEST_PG_DSN"), reason="requires bash and isolated PostgreSQL")
@pytest.mark.parametrize("source", ["environment", "existing", "file"])
def test_postgres_valid_candidate_passes_before_controlled_stop(tmp_path, source):
    dsn = os.environ["FLOCKS_DB_DSN"]
    database_env = {"FLOCKS_DB_DSN": dsn} if source == "environment" else {}
    existing = "FLOCKS_DB_DSN='" + dsn + "'\n" if source == "existing" else ""
    if source == "file":
        credential = tmp_path / "private dsn"
        credential.write_text(dsn)
        credential.chmod(0o600)
        database_env = {"FLOCKS_DB_DSN_FILE": str(credential)}
    result, events, payload, _ = _postgres_install_attempt(tmp_path, database_env=database_env, existing=existing)
    assert "PostgreSQL 连接预检通过" in result.stdout, result.stdout + result.stderr
    assert events[0] == "stop"
    assert dsn not in result.stdout + result.stderr
    snapshots = list((payload / "installer").glob(".postgres-preflight.*/dsn"))
    assert len(snapshots) == 1 and snapshots[0].read_text().strip() == dsn
    assert stat.S_IMODE(snapshots[0].stat().st_mode) == 0o600


@pytest.mark.parametrize("case", ["both", "sqlite", "world-readable", "relative", "symlink", "empty"])
def test_postgres_invalid_config_is_rejected_without_evaluating_it(tmp_path, monkeypatch, pg_preflight_module, case):
    helper = pg_preflight_module
    for key in helper.DB_KEYS:
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / "flocks.env"
    env_file.write_text("")
    credential = tmp_path / "dsn"
    credential.write_text("postgresql://test@/db")
    credential.chmod(0o600)
    values = {
        "both": {"FLOCKS_DB_DSN": "postgresql://test@/db", "FLOCKS_DB_DSN_FILE": str(credential)},
        "sqlite": {"FLOCKS_DB_BACKEND": "sqlite", "FLOCKS_DB_DSN": "postgresql://test@/db"},
        "world-readable": {"FLOCKS_DB_DSN_FILE": str(credential)},
        "relative": {"FLOCKS_DB_DSN_FILE": "dsn"},
        "empty": {},
    }
    if case == "world-readable":
        credential.chmod(0o644)
    if case == "symlink":
        link = tmp_path / "link"
        link.symlink_to(credential)
        values[case] = {"FLOCKS_DB_DSN_FILE": str(link)}
    for key, value in values[case].items():
        monkeypatch.setenv(key, value)
    with pytest.raises(helper.ConfigurationError):
        helper.resolve(env_file)


def test_postgres_env_values_are_data_and_credential_install_is_readable_by_service(tmp_path, monkeypatch, pg_preflight_module):
    import pwd
    from types import SimpleNamespace

    helper = pg_preflight_module
    for key in helper.DB_KEYS:
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / "service $literal.env"
    sentinel = tmp_path / "command-ran"
    dsn = f"postgresql://user:$(touch {sentinel})@localhost/db"
    env_file.write_text("FLOCKS_DB_DSN='" + dsn + "'\nCUSTOM_VALUE=keep\n")
    assert helper.resolve(env_file) == dsn and not sentinel.exists()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "dsn").write_text(dsn)
    args = SimpleNamespace(env_file=env_file, snapshot=snapshot, service_user=pwd.getpwuid(os.getuid()).pw_name)
    helper.install(args)
    assert dsn not in env_file.read_text()
    assert "CUSTOM_VALUE=keep" in env_file.read_text()
    credential = env_file.with_name(env_file.name + ".postgres-dsn")
    assert stat.S_IMODE(credential.stat().st_mode) == 0o640
    assert credential.read_text() == dsn
    assert helper.resolve(env_file) == dsn  # Reinstall reads the escaped path identically.
    assert list(tmp_path.glob("*.pg-bak-*"))  # The previous environment was preserved.
    completed = subprocess.run([BASH, "-c", 'set -a; . "$1"; printf "%s" "$FLOCKS_DB_DSN_FILE"', "test", str(env_file)],
                               capture_output=True, text=True, timeout=5)
    assert completed.returncode == 0 and completed.stdout == str(credential)
    assert not sentinel.exists()


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
@pytest.mark.parametrize("selected", ["FLOCKS_DB_DSN", "FLOCKS_DB_DSN_FILE"])
def test_postgres_service_command_uses_only_installed_settings(tmp_path, selected):
    env_file = tmp_path / "installed.env"
    env_file.write_text("FLOCKS_DB_BACKEND=postgres\n" + selected + "='installed-value'\n")
    runuser = tmp_path / "runuser"
    runuser.write_text('#!/bin/sh\nshift 3\nexec "$@"\n')
    runuser.chmod(0o755)
    function = _bash_function_source(INSTALL_SCRIPT, "run_as_service_user")
    script = 'set -euo pipefail\nSERVICE_USER=stub\nENV_FILE="$1"\n' + function + '\nrun_as_service_user "$2" -c \'import json,os; print(json.dumps({key:value for key,value in os.environ.items() if key.startswith("FLOCKS_DB_")}))\'\n'
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "FLOCKS_DB_BACKEND": "wrong-backend", "FLOCKS_DB_DSN": "candidate-value", "FLOCKS_DB_DSN_FILE": "candidate-file"}
    result = subprocess.run([BASH, "-c", script, "test", str(env_file), sys.executable],
                            env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"FLOCKS_DB_BACKEND": "postgres", selected: "installed-value"}


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_migration_report_gives_a_working_import_command_for_copied_sqlite_data(tmp_path):
    # Spaces in every path: the printed command must still pass each one as a single argument.
    data_root = tmp_path / "data home" / ".flocks"
    (data_root / "data").mkdir(parents=True)
    (data_root / "data" / "flocks.db").write_bytes(b"SQLite format 3\x00")
    repo_root = tmp_path / "app root"
    repo_root.mkdir()
    env_file = tmp_path / "etc flocks" / "flocks.env"
    env_file.parent.mkdir()
    env_file.write_text(
        f"FLOCKS_DB_BACKEND=postgres\nFLOCKS_DB_DSN='postgresql://installed'\nFLOCKS_REPO_ROOT='{repo_root}'\n"
    )
    venv_bin = tmp_path / "venv bin"
    venv_bin.mkdir()
    python = venv_bin / "python"
    python.write_text(
        "#!/bin/sh\n"
        'echo "DSN=${FLOCKS_DB_DSN-unset} FILE=${FLOCKS_DB_DSN_FILE-unset} BACKEND=${FLOCKS_DB_BACKEND-unset}"\n'
        'echo "CWD=$(pwd)"\n'
        'printf "%s\\n" "$@"\n'
    )
    python.chmod(0o755)
    script = (
        'set -euo pipefail\nlog() { printf "%s\\n" "$*"; }\n'
        f"MIGRATE_FROM=/home/old/.flocks\nDATA_ROOT='{data_root}'\nSERVICE_USER=flocks\n"
        f"ENV_FILE='{env_file}'\nVENV_BIN='{venv_bin}'\n"
        + _bash_function_source(INSTALL_SCRIPT, "report_migration") + "report_migration\n"
    )
    report = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=5)
    assert report.returncode == 0, report.stderr
    lines = report.stdout.splitlines()
    assert "没有导入 PostgreSQL" in lines[0]
    command = lines[1].split("：", 1)[1]
    assert command.startswith("runuser -u flocks -- ")
    # Pasted as printed (runuser has switched users by then), from an operator shell that carries
    # other database settings: only the installed ones reach the explicit migration.
    ambient = {**os.environ, "FLOCKS_DB_DSN_FILE": "/root/other.dsn", "FLOCKS_DB_DSN": "postgresql://ambient",
               "FLOCKS_DB_BACKEND": "sqlite"}
    inventory = subprocess.run([BASH, "-c", command.removeprefix("runuser -u flocks -- ")],
                               capture_output=True, text=True, timeout=5, env=ambient, cwd=tmp_path)
    assert inventory.returncode == 0, inventory.stderr
    assert inventory.stdout.splitlines() == [
        "DSN=postgresql://installed FILE=unset BACKEND=postgres", f"CWD={repo_root}", "-m", "flocks.db.migrate",
        "--source", str(data_root / "data" / "flocks.db"), "--report-dir", str(data_root / "db-migration"),
    ]
    assert "--apply" in lines[2] and "原样保留" in lines[3]
    # Without a SQLite database only the note about the old directory remains.
    (data_root / "data" / "flocks.db").unlink()
    plain = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=5)
    assert plain.stdout.splitlines() == [lines[3]]


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
@pytest.mark.parametrize("selected", ["FLOCKS_DB_DSN", "FLOCKS_DB_DSN_FILE"])
def test_postgres_cli_wrapper_uses_only_installed_settings(tmp_path, selected):
    env_file = tmp_path / "installed.env"
    env_file.write_text("FLOCKS_DB_BACKEND=postgres\n" + selected + "='installed-value'\n")
    wrapper = tmp_path / "flocks-wrapper"
    wrapper.write_text(WRAPPER_SCRIPT.read_text().replace("@ENV_FILE@", str(env_file))
                       .replace("@FLOCKS_CLI@", sys.executable).replace("@SERVICE_USER@", "root"))
    env = {**os.environ, "FLOCKS_DB_BACKEND": "wrong-backend", "FLOCKS_DB_DSN": "candidate-value", "FLOCKS_DB_DSN_FILE": "candidate-file"}
    result = subprocess.run([BASH, str(wrapper), "-c",
                             'import os,json; print(json.dumps({key:value for key,value in os.environ.items() if key.startswith("FLOCKS_DB_")}))'],
                            env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"FLOCKS_DB_BACKEND": "postgres", selected: "installed-value"}


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN") or sys.platform == "win32", reason="requires isolated PostgreSQL and Unix")
async def test_postgres_installed_credential_initializes_application_storage(tmp_path, monkeypatch, pg_preflight_module):
    import asyncio
    import pwd
    from types import SimpleNamespace
    from urllib.parse import parse_qsl, urlsplit
    from flocks.db import registry
    from flocks.storage import Storage

    dsn = os.environ["FLOCKS_DB_DSN"]
    schema = dict(parse_qsl(urlsplit(dsn).query))["search_path"].split(",")[0]
    env_file = tmp_path / "flocks.env"
    env_file.write_text("CUSTOM_VALUE=preserved\n")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    args = SimpleNamespace(env_file=env_file, snapshot=snapshot, payload_root=tmp_path,
                           service_user=pwd.getpwuid(os.getuid()).pw_name)
    await asyncio.to_thread(pg_preflight_module.prepare, args)
    pg_preflight_module.install(args)
    for key in pg_preflight_module.DB_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in pg_preflight_module._environment(env_file).items():
        monkeypatch.setenv(key, value)
    registry.reset()
    assert registry.load_settings().dsn == dsn
    await Storage.init()
    async with Storage.transaction() as db:
        assert await db.fetch_one("SELECT current_schema()") == (schema,)
    await Storage.set("installer/credential-probe", {"ok": True})
    assert await Storage.read("installer/credential-probe") == {"ok": True}


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
    # systemd and the env merge only honour plain FLOCKS_ROOT= lines; an export line changes nothing
    env_file.write_text("export FLOCKS_ROOT=/srv/flocks\n", encoding="utf-8")
    assert helper.configured_root(env_file) == ""
    env_file.write_text("FLOCKS_ROOT=/data/flocks\nexport FLOCKS_ROOT=/srv/flocks\n", encoding="utf-8")
    assert helper.configured_root(env_file) == "/data/flocks"
    env_file.write_text("FLOCKS_ROOT=~/.flocks\n", encoding="utf-8")
    with pytest.raises(helper.MigrationError, match="不是绝对路径"):
        helper.configured_root(env_file)


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
def test_installer_refuses_to_migrate_data_a_newer_version_already_used(tmp_path: Path) -> None:
    """The downgrade rule holds for data brought over from another installation as well."""
    payload = _fake_payload(tmp_path, core_version="v2026.9.23")
    previous = _previous_data(tmp_path / "root-home" / ".flocks")
    (previous / "version").mkdir()
    marker = previous / "version" / ".current_version"
    candidates = {"FLOCKS_OFFLINE_HOME_CANDIDATES": str(previous)}

    marker.write_text("2026.10.1\n", encoding="utf-8")  # upgraded online there: newer than the package
    result = _dry_run(payload, extra_env=candidates)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "2026.10.1" in result.stderr and "v2026.9.23" in result.stderr and "本次未做任何改动" in result.stderr
    assert "FLOCKS_OFFLINE_ALLOW_DOWNGRADE=1" in result.stderr

    result = _dry_run(payload, extra_env={**candidates, "FLOCKS_OFFLINE_ALLOW_DOWNGRADE": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "警告" in result.stdout and f"迁移旧数据={previous}" in result.stdout

    for version in ("2026.9.23", "2026.9.14", ""):  # same, older, or an empty marker: the usual migration
        marker.write_text(version + "\n", encoding="utf-8")
        result = _dry_run(payload, extra_env=candidates)
        assert result.returncode == 0, f"{version}: {result.stdout}{result.stderr}"
        assert f"迁移旧数据={previous}" in result.stdout and "警告" not in result.stdout


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
    # the migrated copy is handed over by the helper before its rename; the live data root is never re-owned
    assert '--owner "$SERVICE_USER"' in text
    assert not re.search(r'chown\s+-R[^\n]*"\$DATA_ROOT', text), "no recursive chown of the data root"


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


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_installer_reads_flocks_root_the_way_the_service_gets_it(tmp_path: Path) -> None:
    """An export line never reaches the service (the merge appends FLOCKS_ROOT=, systemd skips export),
    so the installer must not migrate into or chown that directory."""
    payload = _fake_payload(tmp_path, with_bundle=True)
    custom = _previous_data(tmp_path / "data-disk" / ".flocks")
    env_file = tmp_path / "flocks.env"
    env_file.write_text(f"export FLOCKS_ROOT={custom}\n", encoding="utf-8")
    result = _dry_run(payload, extra_env={"FLOCKS_OFFLINE_ENV_FILE": str(env_file)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"数据目录 {tmp_path / 'data-home' / '.flocks'}" in result.stdout
    assert "不会生效" in result.stderr

    env_file.write_text("FLOCKS_ROOT=relative/flocks\n", encoding="utf-8")
    result = _dry_run(payload, extra_env={"FLOCKS_OFFLINE_ENV_FILE": str(env_file)})
    assert result.returncode == 1
    assert "不是绝对路径" in result.stderr and "本次未做任何改动" in result.stderr


def test_migrate_helper_rewrites_jsonc_and_legacy_config_and_keeps_comments(tmp_path: Path) -> None:
    helper = _load_migrate_helper()
    source = _previous_data(tmp_path / "old" / ".flocks")
    jsonc = (
        "{\n"
        f'  // copied from "{source}/plugins" once\n'
        f'  "{source}/as-a-key": 1,\n'
        f'  "mcp": {{"cmd": "{source}/plugins/mcp/run.sh", "url": "http://host//path"}},\n'
        f'  /* {source} */ "sibling": "{source}-old/x"\n'
        "}\n"
    )
    (source / "config" / "flocks.jsonc").write_text(jsonc, encoding="utf-8")
    (source / "config" / "config.json").write_text(json.dumps({"workspace": f"{source}/workspace"}), encoding="utf-8")
    target = tmp_path / "new" / ".flocks"
    lines = helper.copy(source, target, "4")

    migrated = (target / "config" / "flocks.jsonc").read_text(encoding="utf-8")
    expected = jsonc.replace(f'"cmd": "{source}/', f'"cmd": "{target}/')
    assert migrated == expected, "only the value changes: comments, keys, siblings and formatting stay"
    legacy = json.loads((target / "config" / "config.json").read_text(encoding="utf-8"))
    assert legacy["workspace"] == f"{target}/workspace"
    assert any("flocks.jsonc（1 处）" in line and "config.json（1 处）" in line for line in lines)


def test_migrate_helper_prints_utf8_whatever_the_locale(tmp_path: Path) -> None:
    """A latin-1 stdout used to raise UnicodeEncodeError on the first Chinese line, after the swap."""
    source = _previous_data(tmp_path / "old" / ".flocks")
    env = {**os.environ, "PYTHONIOENCODING": "latin-1", "LC_ALL": "C", "LANG": "C"}
    copied = subprocess.run(
        [sys.executable, str(MIGRATE_HELPER), "copy", "--source", str(source), "--target", str(tmp_path / "new" / ".flocks"),
         "--stamp", "8"],
        capture_output=True, env=env,
    )
    assert copied.returncode == 0, copied.stdout + copied.stderr
    assert "已拷贝" in copied.stdout.decode("utf-8")
    refused = subprocess.run(
        [sys.executable, str(MIGRATE_HELPER), "plan", "--target", str(tmp_path / "x" / ".flocks"), "--explicit", "relative"],
        capture_output=True, env=env,
    )
    assert refused.returncode == 1 and "绝对路径" in refused.stdout.decode("utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="posix symlinks")
def test_migrate_helper_treats_root_as_containing_everything(tmp_path: Path) -> None:
    helper = _load_migrate_helper()
    assert helper._inside(Path("/var/lib"), Path("/")) and helper._inside(Path("/"), Path("/"))
    source = _previous_data(tmp_path / "old" / ".flocks")
    os.symlink("/", source / "workspace")
    with pytest.raises(helper.MigrationError, match="拷贝本身再拷进去"):
        helper.check(source, tmp_path / "new" / ".flocks", tmp_path / "no-proc")


@pytest.mark.skipif(sys.platform == "win32", reason="posix ownership")
def test_migrate_helper_hands_the_copy_to_the_service_user_before_the_swap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pwd

    helper = _load_migrate_helper()
    source = _previous_data(tmp_path / "old" / ".flocks")
    target = tmp_path / "new" / ".flocks"
    me = pwd.getpwuid(os.getuid()).pw_name
    calls: list[tuple[str, bool]] = []
    real_chown = os.chown

    def _recording_chown(path, uid, gid, *, dir_fd=None, follow_symlinks=True):
        calls.append((str(path), follow_symlinks))
        return real_chown(path, uid, gid, follow_symlinks=follow_symlinks)

    with monkeypatch.context() as patched:
        patched.setattr(helper.os, "chown", _recording_chown)
        helper.copy(source, target, "6", me)

    staging = str(target.resolve().with_name(".flocks.migrating-6"))
    staged = {os.path.relpath(path, staging) for path, _ in calls if path == staging or path.startswith(staging + os.sep)}
    expected = {"."} | {str(path.relative_to(source)) for path in source.rglob("*")}
    assert expected <= staged, "every entry is handed over while it is still the staged copy"
    assert all(not follow for _, follow in calls), "symlinks are relabelled themselves, never followed"
    assert any(path.endswith("offline-migration-6.json") for path, _ in calls)
    with pytest.raises(helper.MigrationError, match="不存在"):
        helper.copy(source, tmp_path / "other" / ".flocks", "6", "no-such-user-for-flocks")


def test_migrate_helper_matches_escaped_paths_in_json(tmp_path: Path) -> None:
    """recovery.json is written with ensure_ascii (\\uXXXX) and JSON allows \\/; both must still be rewritten."""
    helper = _load_migrate_helper()
    source = _previous_data(tmp_path / "旧实例" / ".flocks")
    recovery = source / "data" / "hub" / "backups" / "rollback-1" / "failed" / "tree-1" / "recovery.json"
    recovery.parent.mkdir(parents=True)
    recovery.write_text(json.dumps({"installPath": f"{source}/plugins/skills/my-skill"}), encoding="utf-8")
    assert "\\u" in recovery.read_text(encoding="utf-8")
    slashed = json.dumps({"backupPath": f"{source}/hub/x"}).replace("/", "\\/")
    (source / "data" / "hub" / "slashed.json").write_text(slashed, encoding="utf-8")
    target = tmp_path / "new" / ".flocks"
    helper.copy(source, target, "2")
    moved = target / "data" / "hub" / "backups" / "rollback-1" / "failed" / "tree-1" / "recovery.json"
    assert json.loads(moved.read_text(encoding="utf-8"))["installPath"] == f"{target}/plugins/skills/my-skill"
    assert json.loads((target / "data" / "hub" / "slashed.json").read_text(encoding="utf-8"))["backupPath"] == f"{target}/hub/x"


# --------------------------------------------------------------------------- #
# base directory (FLOCKS_OFFLINE_BASE): rules, layout, detection, relocation, header
# --------------------------------------------------------------------------- #


@pytest.fixture
def short_tmp():
    # base directories are limited to 90 characters and pytest's tmp_path alone is longer on macOS
    path = Path(tempfile.mkdtemp(prefix="flb-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _base_problem(base: str, mode: str = "", build_root: str = "/opt/flocks") -> str:
    source = _bash_function_source(INSTALL_SCRIPT, "base_problem")
    script = f"set -euo pipefail\nBUILD_ROOT='{build_root}'\n{source}\nbase_problem \"$1\" \"$2\"\n"
    completed = subprocess.run([BASH, "-c", script, "bash", base, mode], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_base_directory_rules() -> None:
    assert _base_problem("/data") == ""
    assert _base_problem("/data1/apps.flocks-x_1") == ""
    assert "绝对路径" in _base_problem("data")
    assert "不能是 /" in _base_problem("")
    for bad in ("/da ta", "/data//x", "/data;x", "/data/$x", "/data/中文"):
        assert "字母" in _base_problem(bad), bad
    assert ". 或 .." in _base_problem("/data/../etc")
    assert ". 或 .." in _base_problem("/data/./x")
    assert _base_problem("/" + "d" * 89) == ""  # 90 characters: the shebang still fits in 127 bytes
    assert "太长" in _base_problem("/" + "d" * 90)
    for volatile in ("/tmp", "/tmp/x", "/var/tmp/flocks", "/run/x"):
        assert "定期清理" in _base_problem(volatile), volatile
    for system in ("/proc", "/sys/x", "/dev/shm", "/boot"):
        assert "不能装在" in _base_problem(system), system
    # /opt/flocks/app would sit inside the default install root, /opt/flocks/flocks/app too
    assert "套在一起" in _base_problem("/opt")
    assert "套在一起" in _base_problem("/opt/flocks")
    assert _base_problem("/opt/data") == ""
    # the base an existing install already uses: only what the installer cannot handle is refused
    assert _base_problem("/tmp/x", "existing") == ""
    assert "字母" in _base_problem("/tmp/x y", "existing")


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_base_directory_puts_everything_below_it(tmp_path: Path) -> None:
    payload = _fake_payload(tmp_path)
    base = "/srv/flocks-base-test-not-there"
    completed = _dry_run(payload, base=base)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    out = completed.stdout
    assert f"将安装到 {base}/flocks/app，基目录 {base}，" in out
    assert f"数据目录 {base}/flocks/data/.flocks，配置 {base}/flocks/etc/flocks.env，日志 {base}/flocks/logs/flocks-offline-install.log" in out
    assert "relocate=1" in out
    assert f"基目录 {base} 还不存在" in out  # a real run stops on it (and so does the .run header)
    # a trailing slash names the same base
    again = _dry_run(payload, base=base + "/")
    assert again.returncode == 0 and f"将安装到 {base}/flocks/app，" in again.stdout
    assert not Path(base).exists() and not (tmp_path / "opt-flocks").exists()


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_default_layout_is_unchanged_without_a_base(tmp_path: Path) -> None:
    payload = _fake_payload(tmp_path)
    completed = _dry_run(payload)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert f"将安装到 {tmp_path / 'opt-flocks'}，基目录 （默认布局），" in completed.stdout
    # _fake_payload points the default /var/log path into the temp dir; the source check below
    # keeps the real default.
    assert f"日志 {tmp_path / 'installer.log'}" in completed.stdout and "relocate=0" in completed.stdout
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    default_layout = text[text.index("set_layout() {"):text.index("base_problem() {")]
    for path in ('INSTALL_ROOT="$BUILD_ROOT"', 'DATA_HOME="/var/lib/flocks"', 'ENV_FILE="/etc/flocks/flocks.env"', 'LOG_FILE="/var/log/flocks-offline-install.log"'):
        assert path in default_layout


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_invalid_base_is_refused_before_anything_changes(tmp_path: Path) -> None:
    payload = _fake_payload(tmp_path)
    for base, says in (("data", "绝对路径"), ("/var/tmp/flocks", "定期清理"), ("/da ta", "字母")):
        completed = _dry_run(payload, base=base)
        assert completed.returncode == 1, base
        assert f"FLOCKS_OFFLINE_BASE={base} 不能用" in completed.stderr and says in completed.stderr
        assert "本次未做任何改动" in completed.stderr
    # a package built without the relocation list can only go where it was built
    (payload / "installer" / "relocate.list").unlink()
    old_package = _dry_run(payload, base="/srv/flocks-base-test-not-there")
    assert old_package.returncode == 1 and "不支持换目录" in old_package.stderr


def _fake_install_at(install_root: Path, env_file: Path, *, version: str = "v2026.9.14") -> None:
    (install_root / "flocks").mkdir(parents=True)
    (install_root / "flocks" / "pyproject.toml").write_text(f'[project]\nname = "flocks"\nversion = "{version}"\n', encoding="utf-8")
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text("FLOCKS_HOST=0.0.0.0\nFLOCKS_PORT=5288\n", encoding="utf-8")


def _written_wrapper(path: Path, install_root: Path, env_file: Path) -> Path:
    """The /usr/local/bin/flocks an install of that layout leaves behind (see install.sh)."""
    text = (
        WRAPPER_SCRIPT.read_text(encoding="utf-8")
        .replace("@ENV_FILE@", str(env_file))
        .replace("@FLOCKS_CLI@", f"{install_root}/flocks/.venv/bin/flocks")
        .replace("@SERVICE_USER@", "flocks")
    )
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_rerun_without_base_upgrades_an_install_below_a_base(tmp_path: Path, short_tmp: Path) -> None:
    payload = _fake_payload(tmp_path)
    base = short_tmp / "data"
    install_root, env_file = base / "flocks" / "app", base / "flocks" / "etc" / "flocks.env"
    _fake_install_at(install_root, env_file)
    wrapper = _written_wrapper(tmp_path / "wrapper", install_root, env_file)

    followed = _dry_run(payload, wrapper=wrapper, derive_paths=True)
    assert followed.returncode == 0, followed.stdout + followed.stderr
    assert f"Flocks 装在基目录 {base} 下，按原目录升级" in followed.stdout
    assert f"将安装到 {install_root}，基目录 {base}，" in followed.stdout
    assert f"配置 {env_file}，" in followed.stdout and "relocate=1" in followed.stdout
    # the same base given explicitly: fine, even where a new install would be refused (/tmp here)
    explicit = _dry_run(payload, wrapper=wrapper, base=str(base))
    assert explicit.returncode == 0, explicit.stdout + explicit.stderr
    assert f"将安装到 {install_root}，" in explicit.stdout
    # without a wrapper the systemd unit tells the same
    unit = tmp_path / "unit"
    unit.write_text(
        UNIT_TEMPLATE.read_text(encoding="utf-8")
        .replace("@INSTALL_ROOT@", str(install_root)).replace("@ENV_FILE@", str(env_file)).replace("@SERVICE_USER@", "flocks"),
        encoding="utf-8",
    )
    via_unit = _dry_run(payload, unit=unit, derive_paths=True)
    assert via_unit.returncode == 0 and f"将安装到 {install_root}，" in via_unit.stdout


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_rerun_without_base_refuses_a_base_remounted_noexec(tmp_path: Path, short_tmp: Path) -> None:
    """The package unpacked to the default place, so its own noexec check says nothing about the base:
    found only after the old program is parked, the upgrade would be left half done."""
    payload = _fake_payload(tmp_path)
    base = short_tmp / "data"
    install_root, env_file = base / "flocks" / "app", base / "flocks" / "etc" / "flocks.env"
    _fake_install_at(install_root, env_file)
    wrapper = _written_wrapper(tmp_path / "wrapper", install_root, env_file)
    # findmnt -no OPTIONS --target <path>: only the base's partition is noexec
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    (stub_dir / "findmnt").write_text(
        '#!/bin/sh\nfor last; do :; done\ncase "$last" in\n'
        '  "$NOEXEC_PREFIX"|"$NOEXEC_PREFIX"/*) echo rw,nosuid,nodev,noexec,relatime ;;\n'
        '  *) echo rw,relatime ;;\nesac\n',
        encoding="utf-8",
    )
    (stub_dir / "findmnt").chmod(0o755)
    stubbed = {"PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}", "NOEXEC_PREFIX": str(base)}

    refused = _dry_run(payload, wrapper=wrapper, derive_paths=True, extra_env=stubbed)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert f"已安装实例的基目录 {base} 没法处理" in refused.stderr and "noexec" in refused.stderr
    assert "本次未做任何改动" in refused.stderr

    # the same base on a partition that allows running programs goes through as before
    elsewhere = _dry_run(payload, wrapper=wrapper, derive_paths=True, extra_env={**stubbed, "NOEXEC_PREFIX": str(tmp_path / "other")})
    assert elsewhere.returncode == 0, elsewhere.stdout + elsewhere.stderr
    assert f"Flocks 装在基目录 {base} 下，按原目录升级" in elsewhere.stdout


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_an_install_is_never_moved_to_another_directory(tmp_path: Path, short_tmp: Path) -> None:
    payload = _fake_payload(tmp_path)
    base = short_tmp / "data"
    install_root, env_file = base / "flocks" / "app", base / "flocks" / "etc" / "flocks.env"
    _fake_install_at(install_root, env_file)
    wrapper = _written_wrapper(tmp_path / "wrapper", install_root, env_file)

    moved = _dry_run(payload, wrapper=wrapper, base="/srv/flocks-other-base")
    assert moved.returncode == 1
    assert f"已经装了 Flocks（{install_root}）" in moved.stderr and "去掉 FLOCKS_OFFLINE_BASE" in moved.stderr
    assert "本次未做任何改动" in moved.stderr

    # a default install (at the build root, found even without wrapper or unit) is not moved below a base either
    _fake_install_at(tmp_path / "opt-flocks", tmp_path / "etc-flocks" / "flocks.env")
    from_default = _dry_run(payload, base="/srv/flocks-other-base")
    assert from_default.returncode == 1 and "去掉 FLOCKS_OFFLINE_BASE" in from_default.stderr

    # an install in a layout this package does not know is not upgraded blindly
    odd_root = short_tmp / "custom"
    _fake_install_at(odd_root, short_tmp / "custom.env")
    odd = _dry_run(payload, wrapper=_written_wrapper(tmp_path / "odd", odd_root, short_tmp / "custom.env"))
    assert odd.returncode == 1 and "目录布局和这个安装包对不上" in odd.stderr


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_a_wrapper_left_by_a_removed_install_is_ignored(tmp_path: Path, short_tmp: Path) -> None:
    payload = _fake_payload(tmp_path)
    gone = short_tmp / "gone" / "flocks"
    wrapper = _written_wrapper(tmp_path / "wrapper", gone / "app", gone / "etc" / "flocks.env")
    fresh = _dry_run(payload, wrapper=wrapper, base="/srv/flocks-new-base")
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    assert "将安装到 /srv/flocks-new-base/flocks/app，" in fresh.stdout


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_retained_copy_of_an_install_below_a_base_is_refused(tmp_path: Path, short_tmp: Path) -> None:
    """<base>/flocks/app/installer/install.sh: its venv was rewritten for <base>, so pyvenv.cfg gives it
    away even when neither wrapper nor build root point at it. Refused without parking the live program."""
    install_root = short_tmp / "data" / "flocks" / "app"
    (install_root / "installer").mkdir(parents=True)
    for name in ("install.sh", "flocks.service", "flocks.env.template", "flocks-cli-wrapper.sh", "relocate.py"):
        shutil.copy(INSTALLER_DIR / name, install_root / "installer" / name)
    (install_root / "tools" / "python" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, install_root / "tools" / "python" / "bin" / "python3")
    (install_root / "flocks" / ".venv").mkdir(parents=True)
    (install_root / "flocks" / ".venv" / "pyvenv.cfg").write_text(f"home = {install_root}/tools/python/bin\n", encoding="utf-8")
    (install_root / "flocks" / "pyproject.toml").write_text('[project]\nname = "flocks"\n', encoding="utf-8")
    (install_root / "versions.json").write_text(
        json.dumps({"core_version": "v2026.9.14", "arch": os.uname().machine, "install_root": "/opt/flocks-build-root-test"}),
        encoding="utf-8",
    )
    before = sorted(x.name for x in install_root.iterdir())
    completed = subprocess.run(
        [BASH, str(install_root / "installer" / "install.sh")], capture_output=True, text=True,
        env={**os.environ, "FLOCKS_OFFLINE_DRY_RUN": "0", "FLOCKS_OFFLINE_WRAPPER": str(tmp_path / "none"),
             "FLOCKS_OFFLINE_UNIT_FILE": str(tmp_path / "none.service")},
    )
    assert completed.returncode == 1 and "不能直接执行" in completed.stderr
    assert sorted(x.name for x in install_root.iterdir()) == before


def _relocate(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(RELOCATE_SCRIPT), *args], capture_output=True, text=True)


def _venv_like_tree(root: Path) -> Path:
    """What uv leaves in a venv built at `root` that names it, plus things that must stay as they are."""
    venv = root / "flocks" / ".venv"
    site = venv / "lib" / "python3.12" / "site-packages"
    (site / "flocks-1.dist-info").mkdir(parents=True)
    (venv / "bin").mkdir(parents=True)
    (root / "tools" / "python" / "bin").mkdir(parents=True)
    (root / "tools" / "python" / "bin" / "python3").write_text("#!/bin/sh\n", encoding="utf-8")
    script = venv / "bin" / "flocks"
    script.write_text(f"#!{root}/flocks/.venv/bin/python\nimport sys\n", encoding="utf-8")
    script.chmod(0o755)
    (venv / "bin" / "activate").write_text(f"VIRTUAL_ENV='{root}/flocks/.venv'\nexport VIRTUAL_ENV\n", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text(f"home = {root}/tools/python/bin\nversion_info = 3.12.12\n", encoding="utf-8")
    (site / "_editable_impl_flocks.pth").write_text(f"{root}/flocks", encoding="utf-8")
    (site / "flocks-1.dist-info" / "direct_url.json").write_text(
        json.dumps({"url": f"file://{root}/flocks", "dir_info": {"editable": True}}), encoding="utf-8"
    )
    os.symlink(f"{root}/tools/python/bin/python3", venv / "bin" / "python")
    os.symlink("python", venv / "bin" / "python3")
    (site / "mod.cpython-312.pyc").write_bytes(b"\x00\x01" + str(root).encode() + b"/flocks/mod.py\x00")
    (site / "lookalike.txt").write_text(f"{root}-old/x {root}2 /srv{root}/x\n", encoding="utf-8")
    (site / "plain.txt").write_text("nothing here\n", encoding="utf-8")
    return site


def test_relocate_scan_lists_what_names_the_build_root(tmp_path: Path) -> None:
    build = tmp_path / "build"
    _venv_like_tree(build)
    listing = tmp_path / "relocate.list"
    completed = _relocate("scan", str(build), str(build), str(listing))
    assert completed.returncode == 0, completed.stderr
    lines = listing.read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"# flocks relocate list, root {build}"
    assert sorted(lines[1:]) == sorted([
        "text flocks/.venv/bin/activate",
        "text flocks/.venv/bin/flocks",
        "text flocks/.venv/pyvenv.cfg",
        "text flocks/.venv/lib/python3.12/site-packages/_editable_impl_flocks.pth",
        "text flocks/.venv/lib/python3.12/site-packages/flocks-1.dist-info/direct_url.json",
        "link flocks/.venv/bin/python",
    ])
    assert "(1 .pyc left to CPython)" in completed.stdout
    # a binary that names the build root cannot be rewritten: the build has to stop
    (build / "tools" / "python" / "bin" / "libfoo.so").write_bytes(b"\x7fELF\x00" + str(build).encode() + b"/lib")
    failed = _relocate("scan", str(build), str(build), str(listing))
    assert failed.returncode == 1 and "tools/python/bin/libfoo.so" in failed.stderr


def test_relocate_apply_moves_the_venv_to_the_new_root(tmp_path: Path) -> None:
    build = tmp_path / "build"
    site = _venv_like_tree(build)
    venv = build / "flocks" / ".venv"
    listing = tmp_path / "relocate.list"
    assert _relocate("scan", str(build), str(build), str(listing)).returncode == 0
    untouched = {path: path.read_bytes() for path in (site / "lookalike.txt", site / "plain.txt", site / "mod.cpython-312.pyc")}
    mtime = (venv / "bin" / "flocks").stat().st_mtime_ns

    completed = _relocate("apply", str(build), str(listing), str(build), "/data/flocks/app")
    assert completed.returncode == 0, completed.stderr
    assert "relocated 5 files and 1 symlinks" in completed.stdout
    assert (venv / "bin" / "flocks").read_text(encoding="utf-8") == "#!/data/flocks/app/flocks/.venv/bin/python\nimport sys\n"
    assert (venv / "bin" / "flocks").stat().st_mode & 0o777 == 0o755
    assert (venv / "bin" / "flocks").stat().st_mtime_ns == mtime
    assert "VIRTUAL_ENV='/data/flocks/app/flocks/.venv'" in (venv / "bin" / "activate").read_text(encoding="utf-8")
    assert (venv / "pyvenv.cfg").read_text(encoding="utf-8").startswith("home = /data/flocks/app/tools/python/bin\n")
    assert (site / "_editable_impl_flocks.pth").read_text(encoding="utf-8") == "/data/flocks/app/flocks"
    direct_url = json.loads((site / "flocks-1.dist-info" / "direct_url.json").read_text(encoding="utf-8"))
    assert direct_url["url"] == "file:///data/flocks/app/flocks"
    assert os.readlink(venv / "bin" / "python") == "/data/flocks/app/tools/python/bin/python3"
    assert os.readlink(venv / "bin" / "python3") == "python"
    for path, data in untouched.items():
        assert path.read_bytes() == data, path
    assert not list(build.rglob(".relocate-*"))


def test_relocate_apply_refuses_what_it_cannot_do_safely(tmp_path: Path) -> None:
    build = tmp_path / "build"
    _venv_like_tree(build)
    listing = tmp_path / "relocate.list"
    assert _relocate("scan", str(build), str(build), str(listing)).returncode == 0
    # nested roots would leave the old root inside every rewritten path
    nested = _relocate("apply", str(build), str(listing), str(build), f"{build}/app")
    assert nested.returncode == 1 and "overlap" in nested.stderr
    # a list made for another build root
    foreign = _relocate("apply", str(build), str(listing), "/opt/other-root", "/data/x")
    assert foreign.returncode == 1 and "was not made for root" in foreign.stderr
    # files that no longer match the list (already moved once) are not rewritten again
    assert _relocate("apply", str(build), str(listing), str(build), "/data/flocks/app").returncode == 0
    again = _relocate("apply", str(build), str(listing), str(build), "/data/other/app")
    assert again.returncode == 1 and "does not" in again.stderr


def test_build_script_ships_the_relocation_and_the_base_aware_header() -> None:
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert '"$SCRIPT_DIR/installer/relocate.py"' in text
    scan = 'relocate.py" scan "$INSTALL_ROOT" "$INSTALL_ROOT" "$INSTALL_ROOT/installer/relocate.list"'
    assert scan in text
    # after the venv is final, before it is packed
    assert text.index('"$UV_BIN" "${UV_SYNC_ARGS[@]}"') < text.index(scan) < text.index('"$MAKESELF" "${MAKESELF_COMPRESS[@]}"')
    assert '--header "$MAKESELF_HEADER"' in text
    check = 'grep -aq \'targetdir="\\$flocks_base/flocks/app/.staging"\' "$OUT_RUN"'
    assert check in text
    assert "the FLOCKS_OFFLINE_BASE extraction did not make it into the .run header" in text
    packing = [line for line in text[text.index("MAKESELF_HEADER="):].splitlines() if not line.lstrip().startswith("#")]
    assert not any("head -c" in line for line in packing), "a head|grep -q pipe races SIGPIPE under pipefail"


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_makeself_header_extracts_below_the_base(tmp_path: Path) -> None:
    """The header addition, expanded the way makeself expands its template, then run like the .run header."""
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    patch = re.search(
        r"python3 - \"\$CACHE_DIR/makeself/makeself-header.sh\" \"\$MAKESELF_HEADER\" <<'PY'\n(.*?)\nPY\n", text, re.DOTALL
    )
    assert patch, "header patch not found in build-offline-run.sh"
    template = tmp_path / "makeself-header.sh"
    template.write_text('cat << EOF  > "$archname"\n#!/bin/sh\ntargetdir="$archdirname"\necho "target=\\$targetdir"\nEOF\n', encoding="utf-8")
    patched = tmp_path / "makeself-header-flocks.sh"
    subprocess.run([sys.executable, "-c", patch.group(1), str(template), str(patched)], check=True)
    header = tmp_path / "header.sh"
    subprocess.run([BASH, "-c", f'archname="{header}"; archdirname=/opt/flocks/.staging; . "{patched}"'], check=True)

    def run(base: str | None, wrapper: Path | None = None, unit: Path | None = None) -> subprocess.CompletedProcess:
        env = {key: value for key, value in os.environ.items() if key != "FLOCKS_OFFLINE_BASE"}
        env["FLOCKS_OFFLINE_WRAPPER"] = str(wrapper if wrapper is not None else tmp_path / "no-wrapper")
        env["FLOCKS_OFFLINE_UNIT_FILE"] = str(unit if unit is not None else tmp_path / "no-unit")
        if base is not None:
            env["FLOCKS_OFFLINE_BASE"] = base
        return subprocess.run(["sh", str(header)], capture_output=True, text=True, env=env)

    assert run(None).stdout.strip() == "target=/opt/flocks/.staging"
    assert run("").stdout.strip() == "target=/opt/flocks/.staging"
    data = tmp_path / "data"
    data.mkdir()
    assert run(str(data)).stdout.strip() == f"target={data}/flocks/app/.staging"
    assert run(f"{data}/").stdout.strip() == f"target={data}/flocks/app/.staging"
    missing = run(str(tmp_path / "nope"))
    assert missing.returncode == 1 and "不存在" in missing.stderr and missing.stdout == ""
    relative = run("data")
    assert relative.returncode == 1 and "绝对路径" in relative.stderr
    root = run("/")
    assert root.returncode == 1 and "不能是 /" in root.stderr
    # /opt/flocks/app would be inside the default install root /opt/flocks (archdirname above)
    nested = subprocess.run(
        ["sh", "-c", f'mkdir -p "$1/opt" && FLOCKS_OFFLINE_BASE="$1/opt" sh "$2"', "sh", str(tmp_path), str(header)],
        capture_output=True, text=True,
    )
    assert nested.returncode == 0, "a base whose app dir is not below the default root is fine"
    header_text = header.read_text(encoding="utf-8")
    assert 'flocks_build_root="${targetdir%/.staging}"' in header_text
    nested_header = tmp_path / "header-opt.sh"
    nested_header.write_text(header_text.replace('targetdir="/opt/flocks/.staging"', f'targetdir="{tmp_path}/opt/flocks/.staging"', 1), encoding="utf-8")
    inside = subprocess.run(["sh", str(nested_header)], capture_output=True, text=True,
                            env={**{k: v for k, v in os.environ.items() if k != "FLOCKS_OFFLINE_BASE"},
                                 "FLOCKS_OFFLINE_BASE": f"{tmp_path}/opt", "FLOCKS_OFFLINE_WRAPPER": str(tmp_path / "no-wrapper"),
                                 "FLOCKS_OFFLINE_UNIT_FILE": str(tmp_path / "no-unit")})
    assert inside.returncode == 1 and "套在一起" in inside.stderr and inside.stdout == ""

    # an install elsewhere (the wrapper names it): the same rule as install.sh, before extracting
    elsewhere = tmp_path / "elsewhere" / "flocks" / "app"
    elsewhere_env = tmp_path / "elsewhere" / "flocks" / "etc" / "flocks.env"
    wrapper = _written_wrapper(tmp_path / "wrapper", elsewhere, elsewhere_env)
    assert run(str(data), wrapper).stdout.strip() == f"target={data}/flocks/app/.staging", "wrapper left by a removed install"
    _fake_install_at(elsewhere, elsewhere_env)
    installed = run(str(data), wrapper)
    assert installed.returncode == 1 and f"已经装了 Flocks（{elsewhere}）" in installed.stderr and "去掉 FLOCKS_OFFLINE_BASE" in installed.stderr
    (elsewhere / "flocks").rename(elsewhere / "flocks.bak-20260924120000")
    unfinished = run(str(data), wrapper)
    assert unfinished.returncode == 1 and "上次安装或升级没有做完" in unfinished.stderr
    assert f"先把 {wrapper} {elsewhere_env} 改名挪走再装" in unfinished.stderr and unfinished.stdout == ""
    same_base = _written_wrapper(tmp_path / "wrapper-same", data / "flocks" / "app", data / "flocks" / "etc" / "flocks.env")
    assert run(str(data), same_base).stdout.strip() == f"target={data}/flocks/app/.staging", "the base the install already uses"
    # no wrapper (moved away as the refusal suggested) but the systemd unit still names the install:
    # refused all the same, before extracting, and the unit is on the list this time
    unit = tmp_path / "flocks.service"
    unit.write_text(
        UNIT_TEMPLATE.read_text(encoding="utf-8")
        .replace("@INSTALL_ROOT@", str(elsewhere)).replace("@ENV_FILE@", str(elsewhere_env)).replace("@SERVICE_USER@", "flocks"),
        encoding="utf-8",
    )
    via_unit = run(str(data), unit=unit)
    assert via_unit.returncode == 1 and "上次安装或升级没有做完" in via_unit.stderr
    assert f"先把 {unit} {elsewhere_env} 改名挪走再装" in via_unit.stderr
    # install.sh puts the program where the header extracts it
    assert 'INSTALL_ROOT="$BASE/flocks/app"' in INSTALL_SCRIPT.read_text(encoding="utf-8")


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_an_upgrade_that_stopped_half_way_is_carried_on_in_place(tmp_path: Path, short_tmp: Path) -> None:
    """The old program is parked as *.bak-*, the new one never moved in: no pyproject.toml anywhere.
    A re-run without the variable must carry on below the same base, not start over in /opt/flocks."""
    payload = _fake_payload(tmp_path)
    base = short_tmp / "data"
    install_root, env_file = base / "flocks" / "app", base / "flocks" / "etc" / "flocks.env"
    _fake_install_at(install_root, env_file)
    wrapper = _written_wrapper(tmp_path / "wrapper", install_root, env_file)
    (install_root / "flocks").rename(install_root / "flocks.bak-20260924120000")

    carried_on = _dry_run(payload, wrapper=wrapper, derive_paths=True)
    assert carried_on.returncode == 0, carried_on.stdout + carried_on.stderr
    assert f"上次在基目录 {base} 下的安装或升级没有做完，按原目录接着装" in carried_on.stdout
    assert f"将安装到 {install_root}，" in carried_on.stdout
    # the env file alone (program dirs moved away by hand) still ties the machine to that place
    (install_root / "flocks.bak-20260924120000").rename(short_tmp / "moved-away")
    env_only = _dry_run(payload, wrapper=wrapper, derive_paths=True)
    assert env_only.returncode == 0 and f"将安装到 {install_root}，" in env_only.stdout
    # and moving it elsewhere is refused, naming the unfinished state
    moved = _dry_run(payload, wrapper=wrapper, base="/srv/flocks-other-base")
    assert moved.returncode == 1 and "上次安装或升级没有做完" in moved.stderr and "去掉 FLOCKS_OFFLINE_BASE 重跑会在原目录接着装" in moved.stderr
    assert f"先把 {wrapper} {env_file} 改名挪走再装" in moved.stderr
    # found through the unit while /usr/local/bin/flocks is some other tool: that file is not ours to move
    foreign = tmp_path / "foreign-flocks"
    foreign.write_text("#!/bin/sh\necho another flocks\n", encoding="utf-8")
    unit = tmp_path / "unit"
    unit.write_text(
        UNIT_TEMPLATE.read_text(encoding="utf-8")
        .replace("@INSTALL_ROOT@", str(install_root)).replace("@ENV_FILE@", str(env_file)).replace("@SERVICE_USER@", "flocks"),
        encoding="utf-8",
    )
    via_unit = _dry_run(payload, wrapper=foreign, unit=unit, base="/srv/flocks-other-base")
    assert via_unit.returncode == 1 and f"先把 {unit} {env_file} 改名挪走再装" in via_unit.stderr
    assert str(foreign) not in via_unit.stderr
    # a parked program without the env file counts too
    env_file.unlink()
    (short_tmp / "moved-away").rename(install_root / "flocks.bak-20260924120000")
    bak_only = _dry_run(payload, wrapper=wrapper, derive_paths=True)
    assert bak_only.returncode == 0, bak_only.stdout + bak_only.stderr
    assert "没有做完，按原目录接着装" in bak_only.stdout and f"将安装到 {install_root}，" in bak_only.stdout


def _retained_copy_at(install_root: Path, *, with_pyvenv_cfg: bool) -> None:
    """<install root>/installer as an install below a base leaves it (venv optionally broken)."""
    (install_root / "installer").mkdir(parents=True)
    for name in ("install.sh", "flocks.service", "flocks.env.template", "flocks-cli-wrapper.sh", "relocate.py"):
        shutil.copy(INSTALLER_DIR / name, install_root / "installer" / name)
    (install_root / "installer" / "relocate.list").write_text("# flocks relocate list, root /opt/flocks-build-root-test\n", encoding="utf-8")
    (install_root / "tools" / "python" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, install_root / "tools" / "python" / "bin" / "python3")
    (install_root / "flocks" / ".venv").mkdir(parents=True)
    if with_pyvenv_cfg:
        (install_root / "flocks" / ".venv" / "pyvenv.cfg").write_text(f"home = {install_root}/tools/python/bin\n", encoding="utf-8")
    (install_root / "flocks" / "pyproject.toml").write_text('[project]\nname = "flocks"\nversion = "v2026.9.14"\n', encoding="utf-8")
    (install_root / "versions.json").write_text(
        json.dumps({"core_version": "v2026.9.14", "arch": os.uname().machine, "install_root": "/opt/flocks-build-root-test"}),
        encoding="utf-8",
    )


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="requires bash")
def test_retained_copy_with_a_broken_venv_is_refused_by_location(tmp_path: Path, short_tmp: Path) -> None:
    """No pyvenv.cfg to give it away: the wrapper (or the base it is given) still says this is the live
    install. Not a dry run, so a mistake would park the live program; nothing may move."""
    base = short_tmp / "data"
    install_root = base / "flocks" / "app"
    env_file = base / "flocks" / "etc" / "flocks.env"
    _retained_copy_at(install_root, with_pyvenv_cfg=False)
    env_file.parent.mkdir(parents=True)
    env_file.write_text("FLOCKS_PORT=5288\n", encoding="utf-8")
    wrapper = _written_wrapper(tmp_path / "wrapper", install_root, env_file)
    before = sorted(x.name for x in install_root.iterdir())
    for extra in ({"FLOCKS_OFFLINE_WRAPPER": str(wrapper)},  # found through the wrapper
                  {"FLOCKS_OFFLINE_WRAPPER": str(tmp_path / "none"), "FLOCKS_OFFLINE_BASE": str(base)}):  # through the base
        env = {**os.environ, "FLOCKS_OFFLINE_DRY_RUN": "0", "FLOCKS_OFFLINE_UNIT_FILE": str(tmp_path / "none.service"), **extra}
        env.pop("FLOCKS_OFFLINE_DATA_HOME", None)
        env.pop("FLOCKS_OFFLINE_ENV_FILE", None)
        if "FLOCKS_OFFLINE_BASE" not in extra:
            env.pop("FLOCKS_OFFLINE_BASE", None)
        completed = subprocess.run([BASH, str(install_root / "installer" / "install.sh")], capture_output=True, text=True, env=env)
        assert completed.returncode == 1, completed.stdout + completed.stderr
        assert "不能直接执行" in completed.stderr, completed.stderr
        assert sorted(x.name for x in install_root.iterdir()) == before
        assert not any(x.name.startswith("flocks-offline-failed-") for x in install_root.iterdir())

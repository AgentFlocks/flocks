"""The bootstrap installers must never wipe an existing install directory silently.

Regression for the 2026-09 full test pass: ``install.sh`` / ``install_zh.sh`` ran
``rm -rf "$INSTALL_DIR"`` before copying the fresh source, so re-running the one-line
installer in a directory that already held a ``flocks/`` checkout destroyed its
``.venv`` and any uncommitted changes without confirmation. They now refuse unless
``--force`` (or ``FLOCKS_INSTALL_FORCE=1``) is given, and even then move the old
directory aside instead of deleting it.

``install.sh`` also printed its progress through stdout while ``download_archive``
ran inside ``$(...)``, which swallowed the "Trying source archive URL" lines and
would have polluted ``download_url``; progress now goes to stderr like the zh script.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def _write_exec(path: Path, body: str) -> Path:
    path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    path.chmod(0o755)
    return path


def _make_archive(tmp_path: Path, *, zh: bool) -> Path:
    name = "flocks-main"
    stage = tmp_path / "stage" / name
    (stage / "scripts").mkdir(parents=True)
    stub = "#!/usr/bin/env bash\necho \"STUB_INSTALLER args=[$*]\"\n"
    _write_exec(stage / "scripts" / "install.sh", stub)
    _write_exec(stage / "scripts" / "install_zh.sh", stub)
    if zh:
        archive = tmp_path / "stage" / f"{name}.zip"
        subprocess.run(["zip", "-qr", str(archive), name], cwd=stage.parent, check=True)
    else:
        archive = tmp_path / "stage" / f"{name}.tar.gz"
        subprocess.run(["tar", "-czf", str(archive), name], cwd=stage.parent, check=True)
    return archive


def _fake_curl(tmp_path: Path, archive: Path) -> Path:
    """A curl that serves *archive* for any URL (``-o`` form) and records the URL."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _write_exec(bin_dir / "curl", f'''
        #!/usr/bin/env bash
        out=""
        while [[ $# -gt 0 ]]; do
          case "$1" in -o) out="$2"; shift ;; -*) ;; *) url="$1" ;; esac; shift
        done
        echo "$url" >> "{tmp_path}/urls"
        cp "{archive}" "$out"
    ''')
    return bin_dir


@pytest.fixture(params=["install.sh", "install_zh.sh"])
def bootstrap(request, tmp_path: Path):
    zh = request.param == "install_zh.sh"
    archive = _make_archive(tmp_path, zh=zh)
    bin_dir = _fake_curl(tmp_path, archive)
    install_dir = tmp_path / "work" / "flocks"
    env = {
        "PATH": f"{bin_dir}:{SYSTEM_PATH}",
        "HOME": str(tmp_path / "home"),
        "FLOCKS_INSTALL_DIR": str(install_dir),
        "TMPDIR": str(tmp_path / "tmp"),
    }
    (tmp_path / "home").mkdir()
    (tmp_path / "tmp").mkdir()
    (tmp_path / "work").mkdir()

    def run(*args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        run_env = dict(env)
        if extra_env:
            run_env.update(extra_env)
        return subprocess.run(
            ["bash", str(REPO_ROOT / request.param), *args],
            env=run_env, cwd=tmp_path / "work", capture_output=True, text=True, timeout=60,
        )

    return run, install_dir, zh


def test_existing_install_dir_is_refused_without_force(bootstrap) -> None:
    run, install_dir, zh = bootstrap
    (install_dir / ".venv").mkdir(parents=True)
    marker = install_dir / "local-change.txt"
    marker.write_text("keep me", encoding="utf-8")

    result = run()

    assert result.returncode == 1, result.stdout + result.stderr
    assert "--force" in result.stderr and "FLOCKS_INSTALL_DIR" in result.stderr
    assert ("安装目录已存在" if zh else "Install directory already exists") in result.stderr
    assert marker.read_text(encoding="utf-8") == "keep me"
    assert "STUB_INSTALLER" not in result.stdout


@pytest.mark.parametrize("how", ["flag", "env"])
def test_force_moves_existing_install_dir_aside_instead_of_deleting(bootstrap, how: str) -> None:
    run, install_dir, zh = bootstrap
    (install_dir / ".venv").mkdir(parents=True)
    marker = install_dir / "local-change.txt"
    marker.write_text("keep me", encoding="utf-8")

    result = run("--force") if how == "flag" else run(extra_env={"FLOCKS_INSTALL_FORCE": "1"})

    assert result.returncode == 0, result.stdout + result.stderr
    assert "STUB_INSTALLER" in result.stdout
    backups = sorted(install_dir.parent.glob("flocks.bak-*"))
    assert len(backups) == 1
    assert (backups[0] / "local-change.txt").read_text(encoding="utf-8") == "keep me"
    assert (backups[0] / ".venv").is_dir()
    assert (install_dir / "scripts" / "install.sh").exists()
    assert not (install_dir / "local-change.txt").exists()


def test_empty_existing_install_dir_is_reused(bootstrap) -> None:
    run, install_dir, _zh = bootstrap
    install_dir.mkdir(parents=True)

    result = run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert (install_dir / "scripts" / "install.sh").exists()
    assert not list(install_dir.parent.glob("flocks.bak-*"))


def test_bootstrap_progress_goes_to_stderr_and_download_url_stays_clean(bootstrap) -> None:
    run, install_dir, zh = bootstrap

    result = run()

    assert result.returncode == 0, result.stdout + result.stderr
    progress = "尝试下载源码压缩包" if zh else "Trying source archive URL"
    downloaded = "下载来源" if zh else "Downloaded from"
    assert progress in result.stderr
    assert progress not in result.stdout
    url_line = next(line for line in result.stderr.splitlines() if downloaded in line)
    assert "\n" not in url_line and progress not in url_line
    assert url_line.rstrip().endswith((".tar.gz", ".zip"))


def test_help_documents_force_option() -> None:
    for script in ("install.sh", "install_zh.sh"):
        result = subprocess.run(["bash", str(REPO_ROOT / script), "--help"], capture_output=True, text=True, env={"PATH": SYSTEM_PATH, "HOME": os.environ.get("HOME", "/tmp")})
        assert result.returncode == 0
        assert "--force" in result.stdout and "FLOCKS_INSTALL_FORCE" in result.stdout


@pytest.mark.parametrize("script", ["install.sh", "install_zh.sh"])
def test_bootstrap_runs_under_posix_sh(script: str, tmp_path: Path) -> None:
    """macOS ``/bin/sh`` is bash 3.2 in POSIX mode: ``BASH_VERSION`` is set, so the old
    ``[ -n "$BASH_VERSION" ] || exec bash`` guard did not re-exec, and the
    ``done < <(build_candidate_urls)`` process substitution then failed with
    ``syntax error near unexpected token `<'``. The download loop is now a plain
    ``for`` loop and the guard re-execs when bash runs as ``sh``."""
    zh = script == "install_zh.sh"
    archive = _make_archive(tmp_path, zh=zh)
    bin_dir = _fake_curl(tmp_path, archive)
    env = {"PATH": f"{bin_dir}:{SYSTEM_PATH}", "HOME": str(tmp_path / "home"), "FLOCKS_INSTALL_DIR": str(tmp_path / "w" / "flocks"), "TMPDIR": str(tmp_path / "tmp")}
    for d in ("home", "tmp", "w"):
        (tmp_path / d).mkdir(exist_ok=True)

    result = subprocess.run(["sh", str(REPO_ROOT / script)], env=env, cwd=tmp_path / "w", capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "syntax error" not in result.stderr
    assert "STUB_INSTALLER" in result.stdout

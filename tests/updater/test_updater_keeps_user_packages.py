"""The self-update reuses ``.venv``; packages a user installed into it must survive the dependency sync.

These run the real ``uv`` against a throwaway project with no dependencies and a locally
built wheel, so nothing is downloaded.
"""

import base64
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from flocks.updater import updater

UV = shutil.which("uv")
DEMO = "flocks_user_lib_demo"

pytestmark = pytest.mark.skipif(UV is None, reason="uv is not installed")


def _uv_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    env.update({"UV_OFFLINE": "1", "UV_NO_CONFIG": "1", "UV_PYTHON": sys.executable})
    return env


def _demo_wheel(directory: Path) -> Path:
    """A minimal pure-Python wheel standing in for any library the user installs."""
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / f"{DEMO}-0.1.0-py3-none-any.whl"
    dist_info = f"{DEMO}-0.1.0.dist-info"
    files = {
        f"{DEMO}/__init__.py": b"VALUE = 'kept'\n",
        f"{dist_info}/METADATA": b"Metadata-Version: 2.1\nName: flocks-user-lib-demo\nVersion: 0.1.0\n",
        f"{dist_info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: flocks-tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    records = []
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            records.append(f"{name},sha256={digest},{len(data)}")
        records.append(f"{dist_info}/RECORD,,")
        archive.writestr(f"{dist_info}/RECORD", "\n".join(records) + "\n")
    return wheel


def _install_root_with_user_package(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "install"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "flocks-install-demo"\nversion = "0.1.0"\n'
        'requires-python = ">=3.8"\ndependencies = []\n\n[tool.uv]\npackage = false\n',
        encoding="utf-8",
    )
    env = _uv_env()
    subprocess.run([UV, "lock"], cwd=root, env=env, check=True, capture_output=True)
    subprocess.run([UV, "sync", "--no-python-downloads"], cwd=root, env=env, check=True, capture_output=True)
    python = updater._venv_python_path(root)
    subprocess.run(
        [UV, "pip", "install", "--python", str(python), str(_demo_wheel(tmp_path / "wheels"))],
        cwd=root, env=env, check=True, capture_output=True,
    )
    assert _imports_demo(python)
    return root, python


def _imports_demo(python: Path) -> bool:
    return subprocess.run([str(python), "-c", f"import {DEMO}"], capture_output=True, check=False).returncode == 0


@pytest.mark.asyncio
async def test_dependency_sync_keeps_packages_the_user_installed(tmp_path: Path) -> None:
    root, python = _install_root_with_user_package(tmp_path)

    error = await updater._sync_project_dependencies(uv_path=UV, install_root=root, env=_uv_env())

    assert error is None
    assert _imports_demo(python)


@pytest.mark.asyncio
async def test_prebuilt_dependency_sync_keeps_packages_the_user_installed(tmp_path: Path) -> None:
    root, python = _install_root_with_user_package(tmp_path)
    wheels_dir = tmp_path / "bundle-wheels"
    wheels_dir.mkdir()

    error = await updater._sync_prebuilt_dependencies(
        uv_path=UV,
        install_root=root,
        wheels_dir=wheels_dir,
        sync_timeout=120,
        env=_uv_env(),
    )

    assert error is None
    assert _imports_demo(python)

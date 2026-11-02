"""Installing and updating from a hub root whose files carry no write permission.

A hub root can be read-only (an extension's installed package, an archive
unpacked with its modes). ``copytree``/``copy2`` gave the staged copy the same
permission bits, so building the pages into it failed with ``[Errno 13]
Permission denied: .../contracts/webui/.soc_ui.<random>/soc_overview/dist``,
and the failed attempt's scratch copy could not be removed afterwards. Plugins
that installed fine that way (no build step) kept the read-only modes on disk,
which later updates and uninstalls have to cope with.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from flocks.contracts.webui import builder as builder_module
from flocks.extensions import register_hub_root, unregister_hub_root
from flocks.hub import installer
from flocks.hub.paths import get_bundled_hub_root
from flocks.hub.update_protection import UpdateConfirmationRequired

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="needs POSIX permission bits, which root does not obey",
)

ROOT_NAME = "test-read-only-hub"
RELEASE = "99.0.0"


def _set_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _restore_write(root: Path) -> None:
    if root.exists():
        for path in [root, *root.rglob("*")]:
            path.chmod(stat.S_IMODE(path.stat().st_mode) | stat.S_IWUSR)


def _owner_can_write_all(root: Path) -> bool:
    return all(os.access(path, os.W_OK) for path in [root, *root.rglob("*")])


def _pages(package: Path) -> list[Path]:
    return sorted(path.parent.relative_to(package) for path in package.rglob("manifest.json") if path.parent != package)


@pytest.fixture()
def quiet_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr(installer, "_refresh_runtime", no_refresh)


@pytest.fixture()
def fake_compiler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for esbuild, so the real page builder writes into the staged copy."""
    script = tmp_path / "bin" / "esbuild"
    script.parent.mkdir()
    script.write_text(
        "#!/bin/sh\n"
        'for arg in "$@"; do case "$arg" in --outfile=*) echo "export default 1;" > "${arg#--outfile=}";; esac; done\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setattr(builder_module, "resolve_esbuild_bin", lambda: script)


@pytest.fixture()
def read_only_release(tmp_path: Path):
    """Publish soc_ui from an extension hub root, as a read-only tree.

    The package lives in the extension root only once the fixture is entered,
    so a test can install the bundled release first.
    """
    root = tmp_path / "extension-hub"
    package = root / "plugins" / "webuis" / "soc_ui"

    def publish() -> Path:
        shutil.copytree(get_bundled_hub_root() / "plugins" / "webuis" / "soc_ui", package)
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        manifest["version"] = RELEASE
        (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (root / "index.json").write_text(
            json.dumps({
                "schemaVersion": "hub.index.v1",
                "plugins": [{
                    "id": "soc_ui",
                    "type": "webui",
                    "name": manifest["name"],
                    "version": RELEASE,
                    "trust": "official",
                    "manifestPath": "plugins/webuis/soc_ui/manifest.json",
                }],
            }),
            encoding="utf-8",
        )
        _set_read_only(package)
        register_hub_root(ROOT_NAME, root)
        return package

    yield publish
    unregister_hub_root(ROOT_NAME)
    _restore_write(package)


async def test_install_from_a_read_only_hub_root(isolated_hub_env, quiet_runtime, fake_compiler, read_only_release):
    package = read_only_release()

    record = await installer.install_plugin("webui", "soc_ui")

    installed = Path(record.installPath)
    access = installer._contracts_access_dir("soc_ui", "global")
    assert record.version == RELEASE
    assert _pages(package)
    for page in _pages(package):
        assert (installed / page / "dist" / "page.js").is_file()
    # The copies are the installation's own, whatever the source allows.
    assert _owner_can_write_all(installed)
    assert _owner_can_write_all(access)
    assert not list(installed.parent.glob(".soc_ui.*"))

    assert await installer.uninstall_plugin("webui", "soc_ui") is True
    assert not installed.exists()
    assert not access.exists()


async def test_update_from_a_read_only_hub_root(isolated_hub_env, quiet_runtime, fake_compiler, read_only_release):
    await installer.install_plugin("webui", "soc_ui")
    read_only_release()

    with pytest.raises(UpdateConfirmationRequired) as asked:
        await installer.update_plugin("webui", "soc_ui")
    record = await installer.update_plugin(
        "webui", "soc_ui", confirmation_token=asked.value.plan["token"], confirm_changes=True,
    )

    installed = Path(record.installPath)
    assert record.version == RELEASE
    assert record.backupPath
    assert (installed / "soc_overview" / "dist" / "page.js").is_file()
    assert _owner_can_write_all(installed)
    assert not list(installed.parent.glob(".soc_ui.*"))


async def test_install_left_read_only_by_an_older_version_updates_and_uninstalls(isolated_hub_env, quiet_runtime):
    """Installs copied from a read-only source before this fix kept its modes on disk."""
    record = await installer.install_plugin("skill", "triaging-security-incident")
    installed = Path(record.installPath)
    retired = installed.parent / f".{installed.name}.bak"
    _set_read_only(installed)
    try:
        await installer.install_plugin("skill", "triaging-security-incident")
        # Left behind, the replaced tree would block the next update as a pending recovery.
        assert not retired.exists()
        assert _owner_can_write_all(installed)

        _set_read_only(installed)
        assert await installer.uninstall_plugin("skill", "triaging-security-incident") is True
        assert not installed.exists()
    finally:
        _restore_write(installed)
        _restore_write(retired)


async def test_protected_update_retains_a_read_only_install(isolated_hub_env, quiet_runtime, fake_compiler):
    record = await installer.install_plugin("webui", "soc_ui")
    installed = Path(record.installPath)
    _set_read_only(installed)
    try:
        with pytest.raises(UpdateConfirmationRequired) as asked:
            await installer.update_plugin("webui", "soc_ui")
        # The original moves into the backup, which is under another parent.
        record = await installer.update_plugin(
            "webui", "soc_ui", confirmation_token=asked.value.plan["token"], confirm_changes=True,
        )
        assert (Path(record.backupPath) / "replaced" / "0" / "package" / "workspace.json").is_file()
        assert _owner_can_write_all(installed)
    finally:
        _restore_write(installed)
        _restore_write(isolated_hub_env["data_dir"] / "hub" / "backups")


def test_retry_removes_read_only_scratch_left_by_a_failed_attempt(tmp_path: Path):
    parent = tmp_path / "webui"
    leftover = parent / ".soc_ui.k2j3h4g5"
    (leftover / "soc_overview" / "src").mkdir(parents=True)
    (leftover / "soc_overview" / "src" / "index.tsx").write_text("export default 1;\n", encoding="utf-8")
    _set_read_only(leftover)
    try:
        installer._purge_stale_scratch(parent, "soc_ui")
    finally:
        _restore_write(leftover)

    assert not leftover.exists()

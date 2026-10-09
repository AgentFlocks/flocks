"""Retries must never discard an unfinished installation's only old copy."""

import errno
from pathlib import Path

import pytest

from flocks.hub import installer, local


@pytest.mark.parametrize("blocked_move", [errno.EXDEV, errno.EACCES])
@pytest.mark.parametrize("runtime_recovers", [False, True])
async def test_failed_non_soc_rollback_blocks_retries_without_losing_original(
    isolated_hub_env, monkeypatch, blocked_move, runtime_recovers,
):
    async def noop(*args):
        pass

    monkeypatch.setattr(installer, "_refresh_runtime", noop)
    record = await installer.install_plugin("skill", "triaging-security-incident")
    root = Path(record.installPath)
    marker = "original-user-edit.txt"
    (root / marker).write_text("only copy of user data")
    replace = installer._replace_with_retry

    def fail_recovery_move(src, dst):
        if src == root and "failed" in dst.parts:
            raise OSError(blocked_move, "injected recovery move failure")
        return replace(src, dst)

    async def fail_refresh(*args):
        raise RuntimeError("injected runtime failure")

    monkeypatch.setattr(installer, "_replace_with_retry", fail_recovery_move)
    monkeypatch.setattr(installer, "_refresh_runtime", fail_refresh)
    with pytest.raises(RuntimeError, match="Rollback stopped"):
        await installer.install_plugin("skill", "triaging-security-incident")
    original = root.parent / f".{root.name}.bak"
    assert (original / marker).read_text() == "only copy of user data"
    assert not (root / marker).exists()
    current_record = local.get_record("skill", "triaging-security-incident")
    if runtime_recovers:
        monkeypatch.setattr(installer, "_refresh_runtime", noop)
        monkeypatch.setattr(installer, "_replace_with_retry", replace)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="recovery is required"):
            await installer.install_plugin("skill", "triaging-security-incident")
        assert (original / marker).read_text() == "only copy of user data"
        assert local.get_record("skill", "triaging-security-incident") == current_record


@pytest.mark.parametrize("live_exists", [False, True])
def test_direct_swap_cannot_replace_unresolved_old_backup(tmp_path, live_exists):
    dst, prepared = tmp_path / "plugin", tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "payload").write_text("incoming")
    old = tmp_path / ".plugin.bak"
    old.mkdir()
    (old / "user.txt").write_text("preserve")
    if live_exists:
        dst.mkdir()
        (dst / "current.txt").write_text("current")
    with pytest.raises(RuntimeError, match="recovery is required"):
        installer._replace_prepared_path(prepared, dst)
    assert (old / "user.txt").read_text() == "preserve"
    assert (prepared / "payload").exists()
    assert dst.exists() is live_exists


def test_cleanup_keeps_unresolved_backup_and_other_staging(tmp_path):
    old = tmp_path / ".plugin.bak"
    old.mkdir()
    (old / "user.txt").write_text("preserve")
    staging = tmp_path / ".plugin.staging"
    staging.mkdir()
    with pytest.raises(RuntimeError, match="recovery is required"):
        installer._purge_stale_scratch(tmp_path, "plugin")
    assert (old / "user.txt").read_text() == "preserve"
    assert staging.exists()


async def test_suite_pending_backup_blocks_before_any_child_is_replaced(isolated_hub_env, monkeypatch):
    from flocks.hub.update_protection import build_plan, tree_hashes

    async def noop(*args):
        pass

    monkeypatch.setattr(installer, "_refresh_runtime", noop)
    monkeypatch.setattr(installer, "_build_webui_pages", lambda *args: None)
    await installer.install_plugin("component", "soc-workspace")
    for kind, identifier in [("webui", "soc_ui"), ("workflow", "stream_alert_triage")]:
        record = local.get_record(kind, identifier)
        local.save_installed_record(record.model_copy(update={"version": "0.0.1"}))
    workflow = Path(local.get_record("workflow", "stream_alert_triage").installPath)
    old = workflow.parent / f".{workflow.name}.bak"
    old.mkdir()
    (old / "user.txt").write_text("unfinished rollback")
    pages = Path(local.get_record("webui", "soc_ui").installPath)
    (pages / "user.txt").write_text("do not overwrite earlier suite child")
    before_files, before_records = tree_hashes(pages), local.load_installed_records()
    plan = build_plan("component", "soc-workspace")
    with pytest.raises(RuntimeError, match="recovery is required"):
        await installer.install_plugin(
            "component", "soc-workspace", confirm_changes=True, confirmation_token=plan["token"],
        )
    assert tree_hashes(pages) == before_files
    assert local.load_installed_records() == before_records
    assert (old / "user.txt").read_text() == "unfinished rollback"


def test_completed_recovery_allows_future_install(tmp_path):
    dst, old, prepared = tmp_path / "plugin", tmp_path / ".plugin.bak", tmp_path / "prepared"
    for path, text in [(dst, "failed replacement"), (old, "original"), (prepared, "new release")]:
        path.mkdir()
        (path / "payload.txt").write_text(text)
    with pytest.raises(RuntimeError, match="recovery is required"):
        installer._replace_prepared_path(prepared, dst)
    installer._rollback_replacement(dst, old, recovery_root=tmp_path / "recovered")
    assert (dst / "payload.txt").read_text() == "original"
    assert not old.exists()
    backup = installer._replace_prepared_path(prepared, dst)
    assert (backup / "payload.txt").read_text() == "original"
    assert (dst / "payload.txt").read_text() == "new release"


def test_cleanup_does_not_touch_dotted_plugin_backup_or_staging(tmp_path):
    other_backup = tmp_path / ".plugin.extra.bak"
    other_staging = tmp_path / ".plugin.extra.12345678"
    own_staging = tmp_path / ".plugin.12345678"
    for path in (other_backup, other_staging, own_staging):
        path.mkdir()
        (path / "data.txt").write_text(path.name)
    installer._purge_stale_scratch(tmp_path, "plugin")
    assert (other_backup / "data.txt").read_text() == ".plugin.extra.bak"
    assert (other_staging / "data.txt").read_text() == ".plugin.extra.12345678"
    assert not own_staging.exists()


def test_dangling_backup_symlink_blocks_retry(tmp_path):
    backup = tmp_path / ".plugin.bak"
    backup.symlink_to(tmp_path / "missing", target_is_directory=True)
    with pytest.raises(RuntimeError, match="recovery is required"):
        installer._purge_stale_scratch(tmp_path, "plugin")
    assert backup.is_symlink()

"""The secret file is shared by every caller: concurrent writes must not lose changes."""

import json
import os
import stat
import threading
import time

from filelock import FileLock

from flocks.security import secrets as secrets_module
from flocks.security.secrets import SecretManager, recording_secret_changes


def _signal_lock_attempts(monkeypatch) -> threading.Event:
    """Set the returned event as soon as any secret writer starts waiting for the lock.

    Asserting "still blocked" only proves something once the writer has reached the lock;
    a writer scheduled late would otherwise pass even without any lock.
    """
    attempted = threading.Event()

    class SignallingLock(FileLock):
        def acquire(self, *args, **kwargs):
            attempted.set()
            return super().acquire(*args, **kwargs)

    monkeypatch.setattr(
        secrets_module,
        "secret_file_lock",
        lambda secret_file: SignallingLock(str(secret_file.with_suffix(".lock")), timeout=5, mode=0o600),
    )
    return attempted


def test_concurrent_writers_keep_every_secret(tmp_path, monkeypatch):
    path = tmp_path / ".secret.json"
    manager = SecretManager(path)
    real_load = SecretManager._load

    def slow_load(self):
        data = real_load(self)
        # Unless writes are serialized, every writer reads the same empty snapshot.
        time.sleep(0.2)
        return data

    monkeypatch.setattr(SecretManager, "_load", slow_load)
    writers = [threading.Thread(target=manager.set, args=(f"key_{i}", f"value_{i}")) for i in range(4)]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join()

    assert json.loads(path.read_text()) == {f"key_{i}": f"value_{i}" for i in range(4)}


def test_a_reader_sees_the_previous_secrets_while_a_write_is_in_progress(tmp_path, monkeypatch):
    path = tmp_path / ".secret.json"
    manager = SecretManager(path)
    manager.set("a", "1")
    seen = []
    real_fsync = os.fsync

    def read_then_fsync(descriptor):
        seen.append(json.loads(path.read_text()))
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", read_then_fsync)
    manager.set("b", "2")

    assert seen == [{"a": "1"}]
    assert json.loads(path.read_text()) == {"a": "1", "b": "2"}


def test_saved_file_is_owner_only_and_leaves_no_temporary_files(tmp_path):
    path = tmp_path / ".secret.json"
    manager = SecretManager(path)
    manager.set("a", "1")
    assert manager.delete("a") is True
    assert manager.delete("a") is False

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {}
    assert [item.name for item in tmp_path.iterdir() if item.name.startswith(".secret-")] == []


def test_reverting_an_operation_waits_for_a_writer_holding_the_lock(tmp_path, monkeypatch):
    path = tmp_path / ".secret.json"
    manager = SecretManager(path)
    with recording_secret_changes() as changes:
        manager.set("a", "1")
        manager.set("b", "2")
    manager.delete("b")
    attempted = _signal_lock_attempts(monkeypatch)
    restored = threading.Event()

    def restore():
        changes.revert()
        restored.set()

    with FileLock(str(path.with_suffix(".lock"))):
        restorer = threading.Thread(target=restore)
        restorer.start()
        assert attempted.wait(5), "the restore never asked for the lock"
        assert not restored.wait(0.3)
    restorer.join(5)

    assert restored.is_set()
    # "a" is taken back; "b" was deleted by someone else since, which stands.
    assert json.loads(path.read_text()) == {}


def test_first_creation_keeps_secrets_written_after_the_existence_check(tmp_path, monkeypatch):
    from flocks.config import config_writer

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    example_dir = tmp_path / "examples"
    example_dir.mkdir()
    (example_dir / ".secret.json.example").write_text("{}")
    monkeypatch.setattr(config_writer.Config, "get_config_path", classmethod(lambda cls: config_dir))
    monkeypatch.setattr(config_writer, "_get_example_config_dir", lambda: example_dir)
    monkeypatch.setattr(config_writer.ConfigWriter, "ensure_memory_config", classmethod(lambda cls: False))
    path = config_dir / ".secret.json"
    attempted = _signal_lock_attempts(monkeypatch)
    initialized = threading.Event()

    def initialize():
        config_writer.ensure_config_files()
        initialized.set()

    with FileLock(str(path.with_suffix(".lock"))):
        initializer = threading.Thread(target=initialize)
        initializer.start()
        assert attempted.wait(5), "the initializer never asked for the lock"
        assert not initialized.wait(0.3)
        # Another writer creates the file while the initializer waits for the lock.
        path.write_text(json.dumps({"written": "meanwhile"}))
    initializer.join(5)

    assert initialized.is_set()
    assert json.loads(path.read_text()) == {"written": "meanwhile"}


def test_writes_through_a_symlink_update_its_target(tmp_path):
    target = tmp_path / "vault" / "secrets.json"
    target.parent.mkdir()
    target.write_text("{}")
    link = tmp_path / ".secret.json"
    link.symlink_to(target)

    SecretManager(link).set("a", "1")

    assert link.is_symlink()
    assert json.loads(target.read_text()) == {"a": "1"}

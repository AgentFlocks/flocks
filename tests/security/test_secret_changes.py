"""A failed operation takes back the secrets it wrote, and only those."""

import asyncio
import contextvars
import json

import pytest

from flocks.security.secrets import SecretManager, recording_secret_changes


@pytest.fixture
def path(tmp_path):
    path = tmp_path / ".secret.json"
    path.write_text(json.dumps({"kept": "1", "changed": "old", "removed": "gone"}))
    return path


def _elsewhere(function, *args):
    """Run as another request or the CLI would: outside the operation's context."""
    return contextvars.Context().run(function, *args)


def test_reverting_restores_each_key_the_operation_wrote(path):
    manager = SecretManager(path)
    with recording_secret_changes() as changes:
        manager.set("changed", "new")
        manager.set("added", "new")
        manager.set("added", "newer")
        manager.delete("removed")

    assert changes.revert() == []
    assert json.loads(path.read_text()) == {"kept": "1", "changed": "old", "removed": "gone"}


def test_reverting_keeps_what_others_wrote_meanwhile(path):
    manager = SecretManager(path)
    other = SecretManager(path)
    with recording_secret_changes() as changes:
        manager.set("changed", "new")
        manager.set("added", "new")
        _elsewhere(other.set, "cli_key", "from the CLI")
        _elsewhere(other.set, "added", "rewritten elsewhere")

    assert changes.revert() == ["added"]
    assert json.loads(path.read_text()) == {
        "kept": "1", "changed": "old", "removed": "gone",
        "cli_key": "from the CLI", "added": "rewritten elsewhere",
    }


def test_writes_from_tasks_and_threads_of_the_operation_are_taken_back(path):
    manager = SecretManager(path)

    async def operation():
        with recording_secret_changes() as changes:
            await asyncio.to_thread(manager.set, "from_thread", "1")
            await asyncio.create_task(asyncio.to_thread(manager.delete, "removed"))
        return changes

    changes = asyncio.run(operation())

    assert changes.revert() == []
    assert json.loads(path.read_text()) == {"kept": "1", "changed": "old", "removed": "gone"}


def test_an_enclosing_operation_takes_back_what_a_finished_inner_one_wrote(path):
    manager = SecretManager(path)
    with recording_secret_changes() as outer:
        manager.set("changed", "outer")
        with recording_secret_changes():
            manager.set("changed", "inner")
            manager.set("added", "inner")
        with recording_secret_changes() as failed:
            manager.set("changed", "failed")
        # The failed inner operation goes back to what the outer one had written.
        assert failed.revert() == []
        assert json.loads(path.read_text())["changed"] == "inner"

    assert outer.revert() == []
    assert json.loads(path.read_text()) == {"kept": "1", "changed": "old", "removed": "gone"}


def test_a_value_written_between_two_of_ours_is_what_reverting_returns_to(path):
    manager = SecretManager(path)
    with recording_secret_changes() as changes:
        manager.set("changed", "first")
        _elsewhere(SecretManager(path).set, "changed", "theirs")
        manager.set("changed", "second")

    assert changes.revert() == []
    assert json.loads(path.read_text())["changed"] == "theirs"


def _signal_lock_attempts(monkeypatch, thread_name):
    """An event set once the thread named ``thread_name`` asks for the secret file lock."""
    import threading

    from filelock import FileLock

    from flocks.security import secrets as secrets_module

    attempted = threading.Event()

    class SignallingLock(FileLock):
        def acquire(self, *args, **kwargs):
            if threading.current_thread().name == thread_name:
                attempted.set()
            return super().acquire(*args, **kwargs)

    monkeypatch.setattr(
        secrets_module, "secret_file_lock",  # secret-guard: allow (a function name, not a secret)
        lambda secret_file: SignallingLock(str(secret_file.with_suffix(".lock")), timeout=5, mode=0o600),
    )
    return attempted


def test_writes_from_two_threads_are_recorded_in_the_order_they_happened(path, monkeypatch):
    import threading

    from flocks.security.secrets import SecretChanges

    manager = SecretManager(path)
    second_attempted = _signal_lock_attempts(monkeypatch, "second-writer")
    second_written = threading.Event()
    record = SecretChanges.record
    waits: list[bool] = []

    def slow_first_record(self, secret_file, secret_id, before, after):
        if after == "first":
            second = threading.Thread(target=context.copy().run, args=(second_write,), name="second-writer")
            threads.append(second)
            second.start()
            assert second_attempted.wait(5), "the second write never asked for the lock"
            # Recorded under the lock, the second write cannot land before this record.
            waits.append(second_written.wait(0.3))
        record(self, secret_file, secret_id, before, after)

    def second_write():
        manager.set("changed", "second")
        second_written.set()

    monkeypatch.setattr(SecretChanges, "record", slow_first_record)  # secret-guard: allow (a function name, not a secret)
    threads: list[threading.Thread] = []
    with recording_secret_changes() as changes:
        context = contextvars.copy_context()
        first = threading.Thread(target=context.copy().run, args=(manager.set, "changed", "first"))
        first.start()
        first.join(10)
        for thread in threads:
            thread.join(10)
    assert not first.is_alive() and not any(thread.is_alive() for thread in threads)
    assert waits == [False] and second_written.is_set()

    assert changes.revert() == []
    assert json.loads(path.read_text())["changed"] == "old"


def test_a_write_made_while_an_inner_revert_reports_back_waits_for_it(path, monkeypatch):
    import threading

    from flocks.security.secrets import SecretChanges

    manager = SecretManager(path)
    third_attempted = _signal_lock_attempts(monkeypatch, "third-writer")
    third_written = threading.Event()
    record = SecretChanges.record
    waits: list[bool] = []
    threads: list[threading.Thread] = []

    def third_write():
        manager.set("changed", "third")
        third_written.set()

    with recording_secret_changes() as outer:
        manager.set("changed", "outer")
        with recording_secret_changes() as inner:
            manager.set("changed", "inner")
        context = contextvars.copy_context()

        def reporting_record(self, secret_file, secret_id, before, after):
            if self is outer and (before, after) == ("inner", "outer"):
                # The inner revert reports back to the outer operation; a write
                # from the outer operation starts now and must wait for it.
                third = threading.Thread(target=context.copy().run, args=(third_write,), name="third-writer")
                threads.append(third)
                third.start()
                assert third_attempted.wait(5), "the third write never asked for the lock"
                waits.append(third_written.wait(0.3))
            record(self, secret_file, secret_id, before, after)

        monkeypatch.setattr(SecretChanges, "record", reporting_record)  # secret-guard: allow (a function name, not a secret)
        assert inner.revert() == []
        for thread in threads:
            thread.join(10)
    assert waits == [False] and third_written.is_set()

    assert outer.revert() == []
    assert json.loads(path.read_text())["changed"] == "old"


def test_a_revert_that_fails_can_be_tried_again(path, monkeypatch):
    from flocks.security import secrets as secrets_module

    manager = SecretManager(path)
    with recording_secret_changes() as changes:
        manager.set("changed", "new")
    replace = secrets_module.replace_secret_file

    def failing_replace(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(secrets_module, "replace_secret_file", failing_replace)  # secret-guard: allow (a function name, not a secret)
    with pytest.raises(OSError):
        changes.revert()
    monkeypatch.setattr(secrets_module, "replace_secret_file", replace)  # secret-guard: allow (a function name, not a secret)

    assert changes.revert() == []
    assert json.loads(path.read_text())["changed"] == "old"

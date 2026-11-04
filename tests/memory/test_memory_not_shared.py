"""A memory note the stored notes did not take is reported, not passed off as a write that worked.

Here every note is settled; a backend that keeps the notes elsewhere may answer
otherwise (``flocks.memory.sync.mirror.reconcile_note``), and words the message.
"""

from __future__ import annotations

import pytest

from flocks.memory.sync import mirror


def _store_answers(monkeypatch: pytest.MonkeyPatch, *answers: bool) -> None:
    replies = iter(answers)

    async def _reconcile_note(root, note, *, attempts=1):
        return next(replies)

    monkeypatch.setattr(mirror, "reconcile_note", _reconcile_note)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    from flocks.config.config import Config

    monkeypatch.setenv("FLOCKS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(Config, "_global_config", None)
    yield tmp_path / "data"
    Config._global_config = None


@pytest.mark.asyncio
async def test_a_daily_note_says_whether_it_was_written(data_dir, monkeypatch):
    from flocks.memory.daily import DailyMemory

    _store_answers(monkeypatch, False)
    with pytest.raises(mirror.MemoryNotShared) as before:
        await DailyMemory().write_daily("first", date="2026-10-28")
    assert (before.value.path, before.value.written) == ("daily/2026-10-28.md", False)
    assert not (data_dir / "memory" / "daily" / "2026-10-28.md").exists()

    _store_answers(monkeypatch, True, False)
    with pytest.raises(mirror.MemoryNotShared) as after:
        await DailyMemory().write_daily("second", date="2026-10-28")
    assert (after.value.path, after.value.written) == ("daily/2026-10-28.md", True)
    assert (data_dir / "memory" / "daily" / "2026-10-28.md").read_text(encoding="utf-8") == "second"
    assert str(after.value).startswith("daily/2026-10-28.md")


@pytest.mark.asyncio
async def test_a_note_on_the_local_disk_is_always_settled(tmp_path):
    assert await mirror.reconcile_note(tmp_path, tmp_path / "topic.md") is True

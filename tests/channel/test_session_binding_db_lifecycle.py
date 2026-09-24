"""The PostgreSQL binding handle can reopen without losing persisted bindings."""

from __future__ import annotations

import threading

import pytest

from flocks.channel.base import ChatType
from flocks.channel.inbound import session_binding
from flocks.storage.storage import Storage


def _binding_worker_threads(baseline: set[str]) -> list[threading.Thread]:
    return [
        t for t in threading.enumerate()
        if t.name not in baseline and t.is_alive() and "_connection_worker_thread" in t.name
    ]


@pytest.mark.asyncio
async def test_binding_pg_handle_reopens_without_sqlite_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    await Storage.init(tmp_path / "flocks.db")
    await session_binding.close_binding_db()
    baseline = {t.name for t in threading.enumerate()}

    service = session_binding.SessionBindingService()
    binding = session_binding.SessionBinding(
        channel_id="slack", account_id="test", chat_id="chat-1",
        chat_type=ChatType.DIRECT, thread_id=None, session_id="ses_persisted",
        agent_id="rex", created_at=1.0, last_message_at=2.0,
    )
    await service._insert(binding)
    first_handle = session_binding._db_handle
    assert first_handle is not None and first_handle.dialect.name == "postgres"
    assert await session_binding._get_db() is first_handle
    assert [row.session_id for row in await service.list_bindings()] == [binding.session_id]

    assert not _binding_worker_threads(baseline)

    await session_binding.close_binding_db()
    assert not _binding_worker_threads(baseline)
    assert session_binding._db_handle is None and session_binding._db_ready is False

    # the handle is lazily rebuilt on the next use ...
    assert [row.session_id for row in await service.list_bindings()] == [binding.session_id]
    assert session_binding._db_handle is not first_handle
    assert not _binding_worker_threads(baseline)
    # ... and Storage.shutdown() (app lifespan + the tests' autouse fixture) releases it too
    await Storage.shutdown()
    assert not _binding_worker_threads(baseline)
    assert session_binding._db_handle is None
    assert [row.session_id for row in await service.list_bindings()] == [binding.session_id]

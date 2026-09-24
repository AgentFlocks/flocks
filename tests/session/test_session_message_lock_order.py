"""Deleting a session takes message-state locks before document-row locks."""

import asyncio
import json
import os
import sys

import pytest

from flocks.session.message import Message, MessageRole
from flocks.session.session import Session
from flocks.storage.storage import Storage

pytestmark = pytest.mark.skipif(
    not os.getenv("FLOCKS_TEST_PG_DSN"), reason="isolated PostgreSQL required",
)

WORKER = r'''
import asyncio, json, os, sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from flocks.config import Config
from flocks.storage.storage import Storage
from flocks.session.message import Message, MessageRole
from flocks.session.session import Session
from flocks.session import message_pages

async def main():
    home = Path(os.environ['HOME'])
    assert os.environ['USERPROFILE'] == str(home)
    assert Config.get_data_path().is_relative_to(home)
    assert os.environ['PYTHON_DOTENV_DISABLED'] == '1'
    schema = parse_qs(urlsplit(os.environ['FLOCKS_DB_DSN']).query)['search_path'][0].split(',')[0]
    assert schema.startswith('test_')
    await Storage.init()
    async with Storage.transaction() as db:
        assert (await db.fetch_one('SELECT current_schema()'))[0] == schema
    print('EVENT boot', flush=True)
    cmd = json.loads(await asyncio.to_thread(sys.stdin.readline))
    sid, mid = cmd['sid'], cmd['mid']
    if cmd['role'] == 'writer':
        await Message.list_with_parts(sid)
        original = message_pages.lock_session
        async def gate(db, session_id):
            if cmd['order'] == 'writer_first':
                revision = await original(db, session_id)
            print('EVENT ready', flush=True)
            assert (await asyncio.to_thread(sys.stdin.readline)).strip() == 'go'
            if cmd['order'] == 'writer_first':
                return revision
            return await original(db, session_id)
        message_pages.lock_session = gate
        operation = (Message.update(sid, mid, finish='stop') if cmd['order'] == 'writer_first'
                     else Message.create(sid, MessageRole.USER, 'late write', id='late_message'))
    else:
        original = message_pages.remove_sessions
        async def gate(db, session_ids):
            print('EVENT ready', flush=True)
            assert (await asyncio.to_thread(sys.stdin.readline)).strip() == 'go'
            return await original(db, session_ids)
        if cmd['order'] == 'writer_first':
            message_pages.remove_sessions = gate
        operation = Session.delete(cmd['project'], sid)
    try:
        result = await operation
        assert result is not None and result is not False
        print('RESULT '+json.dumps({'ok': True}), flush=True)
    except Exception as exc:
        print('RESULT '+json.dumps({'ok': False, 'type': type(exc).__name__, 'error': str(exc)}), flush=True)
    await Storage.shutdown()
asyncio.run(main())
'''


async def _line(process, prefix):
    while True:
        line = await asyncio.wait_for(process.stdout.readline(), 20)
        assert line, f"Worker exited before {prefix}"
        decoded = line.decode().strip()
        if decoded.startswith("RESULT ") and prefix != "RESULT ":
            pytest.fail(f"Worker returned early: {decoded}")
        if decoded.startswith(prefix):
            return decoded[len(prefix):]


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["writer_first", "deleter_first"])
async def test_message_write_and_session_delete_follow_one_lock_order(tmp_path, order):
    session = await Session.create(project_id="message_lock_order", directory=str(tmp_path))
    message = await Message.create(session.id, MessageRole.ASSISTANT, "synthetic")
    processes, handles = [], []
    try:
        for role in ("writer", "deleter"):
            home = tmp_path / role
            home.mkdir(mode=0o700)
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(("FLOCKS_", "FLOCKSPRO_")) and not key.endswith("_API_KEY")}
            env.update(HOME=str(home), USERPROFILE=str(home), FLOCKS_ROOT=str(home / "runtime"),
                       FLOCKS_DB_DSN=os.environ["FLOCKS_DB_DSN"], PYTHON_DOTENV_DISABLED="1",
                       PYTHONDONTWRITEBYTECODE="1")
            handle = await asyncio.to_thread((home / "worker.log").open, "w")
            handles.append(handle)
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-u", "-c", WORKER, cwd=home, env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=handle,
            )
            processes.append(process)
            assert await _line(process, "EVENT ") == "boot"
        # Finish bootstrap in both processes before deliberately holding row locks.
        for role, process in zip(("writer", "deleter"), processes):
            command = dict(sid=session.id, mid=message.id, project=session.project_id, role=role, order=order)
            process.stdin.write((json.dumps(command) + "\n").encode())
            await process.stdin.drain()
            if role == "writer":
                assert await _line(process, "EVENT ") == "ready"
        if order == "writer_first":
            # Deletion must wait before deleting runtime_state, not after those
            # rows are locked. The old order reaches the post-hook immediately.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(_line(processes[1], "EVENT "), 0.2)
            processes[0].stdin.write(b"go\n")
            await processes[0].stdin.drain()
            writer_result = json.loads(await _line(processes[0], "RESULT "))
            assert writer_result["ok"], writer_result
            assert await _line(processes[1], "EVENT ") == "ready"
            processes[1].stdin.write(b"go\n")
            await processes[1].stdin.drain()
            deleter_result = json.loads(await _line(processes[1], "RESULT "))
            assert deleter_result["ok"], deleter_result
        else:
            # The writer already passed cache checks and captured a Session
            # object, but must be fenced when deletion commits before its lock.
            deleter_result = json.loads(await _line(processes[1], "RESULT "))
            assert deleter_result["ok"], deleter_result
            processes[0].stdin.write(b"go\n")
            await processes[0].stdin.drain()
            writer_result = json.loads(await _line(processes[0], "RESULT "))
            assert writer_result.get("type") == "SessionMessagesDeletedError", writer_result
            assert writer_result["ok"] is False
        assert await Session.get_by_id_unfiltered(session.id) is None
        assert await Storage.get(f"message:{session.id}") is None
        assert await Storage.get(f"message_parts:{session.id}:late_message") is None
        async with Storage.transaction() as db:
            assert (await db.fetch_one(
                "SELECT count(*) FROM session_message_pages WHERE session_id = ?", (session.id,),
            ))[0] == 0
            assert (await db.fetch_one(
                "SELECT deleted FROM session_message_page_state WHERE session_id = ?", (session.id,),
            ))[0] is True
        Message.invalidate_cache(session.id)
        assert await Message.list(session.id) == []
    finally:
        for process in processes:
            if process.returncode is None:
                process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except asyncio.TimeoutError:
                    process.terminate()
                    await process.wait()
        for handle in handles:
            await asyncio.to_thread(handle.close)

"""Legacy normalization must not replay stale parts from a different worker."""

import asyncio
import json
import os
from pathlib import Path
import sys

import pytest

from flocks.session.message import Message, MessageRole, TextPart
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
from flocks.session.message import Message
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
    cmd = json.loads(await asyncio.to_thread(sys.stdin.readline))
    sid, mid = cmd['sid'], cmd['mid']
    await Message.list_with_parts(sid, include_archived=True)
    original = message_pages.lock_session
    async def gate(db, session_id):
        # Pause after each public operation's revision check, before its real
        # PostgreSQL lock. Persistence and merging run without substitutions.
        print('EVENT ready', flush=True)
        assert (await asyncio.to_thread(sys.stdin.readline)).strip() == 'go'
        message_pages.lock_session = original
        return await original(db, session_id)
    message_pages.lock_session = gate
    await Message.update_part(sid, mid, cmd['pid'], text=cmd['text'])
    print('EVENT done', flush=True)
    await Storage.shutdown()
asyncio.run(main())
'''


async def _wait_event(process, expected):
    while True:
        line = await asyncio.wait_for(process.stdout.readline(), timeout=20)
        assert line, f"Worker terminated before {expected}"
        if line.startswith(b"EVENT "):
            assert line.decode().strip() == "EVENT " + expected
            return


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True], ids=["canonical", "legacy"])
async def test_independent_part_updates_preserve_peer_fields(tmp_path, legacy):
    processes, handles = [], []
    session = await Session.create(project_id="parts_merge", directory=str(tmp_path))
    message = await Message.create(session.id, MessageRole.USER, "")
    sid, mid = session.id, message.id
    parts = [TextPart(id="part_a", sessionID=sid, messageID=mid, text="old-a"),
             TextPart(id="part_b", sessionID=sid, messageID=mid, text="old-b")]
    if legacy:
        raw = [{"id": p.id, "sessionID": sid, "messageID": mid,
                "type": "text", "text": p.text} for p in parts]
        await Storage.delete(f"message_parts:{sid}:{mid}")
        await Storage.set(f"message_parts:{sid}", {mid: raw}, "message_parts")
    else:
        await Storage.set(f"message_parts:{sid}:{mid}", [p.model_dump() for p in parts], "message_part")

    try:
        for number in range(2):
            home = tmp_path / f"worker-{number}"
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
            label = "ab"[number]
            command = dict(sid=sid, mid=mid, pid=f"part_{label}", text=f"new-{label}")
            process.stdin.write((json.dumps(command) + "\n").encode())
            await process.stdin.drain()
        await asyncio.gather(*(_wait_event(p, "ready") for p in processes))
        # Both writers have their old snapshots before either transaction commits.
        for process in processes:
            process.stdin.write(b"go\n")
            await process.stdin.drain()
            await _wait_event(process, "done")
        Message.invalidate_cache(sid)
        result = {p.id: p.text for p in await Message.parts(mid, sid)}
        assert result == {"part_a": "new-a", "part_b": "new-b"}
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

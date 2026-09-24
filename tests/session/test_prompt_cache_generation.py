"""Bounded local generations must not make prompt-conversion keys reusable."""

import asyncio
import json
import os
import sys

import pytest

from flocks.session.message import Message, MessageRole
from flocks.session.runner import SessionRunner
from flocks.session.session import Session

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
    cmd = json.loads(sys.argv[1])
    updated = await Message.update_part(cmd['sid'], cmd['mid'], 'part_target', text='updated-by-peer')
    assert updated is not None
    print('RESULT updated', flush=True)
    await Storage.shutdown()
asyncio.run(main())
'''


@pytest.mark.asyncio
async def test_prompt_refresh_after_generation_lru_eviction(tmp_path):
    session = await Session.create(project_id="cache_generation", directory=str(tmp_path))
    message = await Message.create(session.id, MessageRole.USER, "original-token", part_id="part_target")
    Message.invalidate_cache()
    runner = SessionRunner(session=session)
    before = await runner._to_chat_messages(await Message.list(session.id), [])
    assert "original-token" in str([m.model_dump() for m in before])
    # Keep the real production limits. Evict the session from both bounded maps
    # while the independent runner's converted context remains alive.
    count = Message._MAX_CACHED_SESSIONS + Message._MAX_CACHE_GENERATIONS + 2
    for number in range(count):
        await Message.list(f"synthetic_cache_churn_{number}")
    assert session.id not in Message._messages_cache
    assert session.id not in Message._session_cache_generations

    home = tmp_path / "peer"
    home.mkdir(mode=0o700)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("FLOCKS_", "FLOCKSPRO_")) and not key.endswith("_API_KEY")}
    env.update(HOME=str(home), USERPROFILE=str(home), FLOCKS_ROOT=str(home / "runtime"),
               FLOCKS_DB_DSN=os.environ["FLOCKS_DB_DSN"], PYTHON_DOTENV_DISABLED="1",
               PYTHONDONTWRITEBYTECODE="1")
    handle = await asyncio.to_thread((home / "worker.log").open, "w")
    process = None
    try:
        command = json.dumps(dict(sid=session.id, mid=message.id))
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-u", "-c", WORKER, command, cwd=home, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=handle,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), 20)
        assert process.returncode == 0, f"Peer exited; see {home / 'worker.log'}"
        assert b"RESULT updated" in stdout
        after = await runner._to_chat_messages(await Message.list(session.id), [])
        rendered = str([m.model_dump() for m in after])
        assert [p.text for p in await Message.parts(message.id, session.id)] == ["updated-by-peer"]
        assert "updated-by-peer" in rendered and "original-token" not in rendered
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            await process.wait()
        await asyncio.to_thread(handle.close)

import asyncio
from types import SimpleNamespace

import pytest

from flocks.monitoring.conversation import decision_response, progress_callback, public_progress
from flocks.provider.provider import StreamChunk


@pytest.mark.parametrize('document,expected', [
    ('{"reason":"正在核对', '正在核对'),
    ('{"action":"query","reason":"主机\\n与进程\\u', '主机\n与进程'),
    ('{"evidence":[{"reason":"private"}],"reason":"公开进展"}', '公开进展'),
    ('```json\n{"reason":"核对\\"文件', '核对"文件'),
    ('{"reasoning":"private","reason":', ''),
    ('{"reason":"核对\\u4e3b', '核对主'),
])
def test_progress_reads_only_public_top_level_field(document, expected):
    assert public_progress(document) == expected


async def test_real_chunks_reach_chat_before_model_finishes_without_private_reasoning():
    entered, release = asyncio.Event(), asyncio.Event()
    updates = []

    async def stream(*args, **kwargs):
        yield StreamChunk(event_type='reasoning', delta='PRIVATE', reasoning='PRIVATE')
        yield StreamChunk(delta='{"reason":"先核对主机')
        entered.set()
        await release.wait()
        yield StreamChunk(delta='证据","action":"query"}', finish_reason='stop', usage={'total_tokens': 7})

    async def progress(text):
        updates.append(text)

    token = progress_callback.set(progress)
    try:
        task = asyncio.create_task(decision_response(SimpleNamespace(chat_stream=stream), 'fixture', []))
        await asyncio.wait_for(entered.wait(), 2)
        assert updates == ['先核对主机']
        assert not task.done()
        release.set()
        response = await task
    finally:
        progress_callback.reset(token)
    assert updates[-1] == '先核对主机证据'
    assert 'PRIVATE' not in response.content
    assert response.finish_reason == 'stop' and response.usage['total_tokens'] == 7


async def test_stream_cancellation_closes_provider_request():
    entered, closed = asyncio.Event(), asyncio.Event()

    async def stream(*args, **kwargs):
        try:
            yield StreamChunk(delta='{"reason":"正在核对')
            entered.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    async def progress(text):
        pass

    token = progress_callback.set(progress)
    try:
        task = asyncio.create_task(decision_response(SimpleNamespace(chat_stream=stream), 'fixture', []))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
    finally:
        progress_callback.reset(token)

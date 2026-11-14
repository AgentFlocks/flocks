"""Scheduling boundaries preserve facts and keep feedback ahead of ignore intent."""
import json
from unittest.mock import AsyncMock

from flocks.monitoring import runtime, investigation, mailflow, mail_manual, followup
from flocks.monitoring.assessment import assess_event
from flocks.monitoring.store import rows, write, encode
from tests.monitoring.test_round_endings import context
from tests.monitoring.test_mail_manual import case, change, consume_queued, notice


async def test_manual_write_readback_rejects_new_activity_even_if_status_is_completed(case):
    case.statuses = [0, {'uuId': 'event', 'name': 'fixture', 'incidentSeverity': 4,
                         'dealStatus': 40, 'endTime': 200}]
    await change(case)
    assert (await consume_queued(case))['state'] == 'pending'
    case.statuses = [{'uuId': 'event', 'name': 'fixture', 'incidentSeverity': 4,
                      'dealStatus': 40, 'endTime': 200}]
    await mail_manual.recheck_pending(case.policy, adapter_factory=case.adapter)
    assert (await mail_manual.snapshot(await notice(case)))['state'] == 'pending'
    assert sum(p['action'] == 'update_status' for _, p in case.calls) == 1


async def test_feedback_consumes_before_automatic_ignore_intents(context, monkeypatch):
    order = []
    context.events.clear()
    async def manual(*args, **kwargs):
        order.append('manual')
        return {'checked': 0, 'verified': 0, 'pending': 0, 'errors': []}
    async def reply(*args, **kwargs):
        order.append('reply')
        return {'processed': 0, 'verified': 0, 'pending': 0, 'errors': []}
    async def automatic(*args, **kwargs):
        order.append('ignore')
        return {'processed': 0, 'verified': 0, 'pending': 0, 'errors': []}
    monkeypatch.setattr(mail_manual, 'recheck_pending', manual)
    monkeypatch.setattr(mailflow, 'cutoff', AsyncMock(return_value=1))
    monkeypatch.setattr(mailflow, 'process_replies', reply)
    monkeypatch.setattr(followup, 'process_due', automatic)
    await runtime.run(context.execution, context.policy, context.adapter)
    assert order == ['manual', 'reply', 'ignore']


async def test_successful_page_is_saved_when_later_pagination_cannot_finish(context, monkeypatch):
    context.policy = context.policy.model_copy(update={'max_pages': 1})
    await write('UPDATE monitor_installations SET policy=? WHERE owner=? AND scope=?', (encode(context.policy.model_dump()), context.policy.owner, context.policy.scope))
    # 100 unique valid records have really been read, but total=101 proves the
    # batch is incomplete. A page-budget failure cannot erase those 100 facts.
    class Partial:
        def __init__(self, policy, session_id): self.policy = policy
        async def call(self, device, params, message_id):
            return {'data': {'item': [{'uuId': f'event-{n}', 'name': 'fixture',
                'hostIp': '192.0.2.1', 'endTime': 100, 'incidentSeverity': 2, 'dealStatus': 0}
                for n in range(100)], 'total': 101}}
    async def defer(policy, event, recorder, budget):
        event['investigation'] = {'state': 'deferred', 'attempted_this_round': False, 'reason': '等待后续调查'}
        event['assessment'] = assess_event(event)
        return event
    monkeypatch.setattr(investigation, 'investigate', defer)
    result = await runtime.run(context.execution, context.policy, Partial)
    assert result.action == 'error'
    observed = await rows('SELECT * FROM monitor_observations')
    assert len(observed) == 100
    assert all(json.loads(item['data'])['query_batch_complete'] is False for item in observed)
    assert not await rows('SELECT * FROM monitor_cursors')
    tracking = await followup.snapshot(context.policy)
    assert len(tracking) == 100 and all(item['watch_count'] == 0 for item in tracking.values())
    assert not await rows('SELECT * FROM monitor_dispositions')

"""Source records remain visible without becoming confirmed investigation proof."""
import pytest

from flocks.monitoring import event_list
from flocks.monitoring.source_event import source_event_evidence
from tests.monitoring.test_event_list import install, incident, investigate, observe


def test_source_record_keeps_event_identity_and_never_uses_host_as_source_ip():
    event = incident('source', name='主机存在r57 Webshell访问，同时发现横向移动行为', incidentSeverity=4)
    card, timeline = source_event_evidence(event, [])
    fields = {field['key']: field['value'] for field in card['fields']}
    assert card['kind'] == 'source_event' and '192.0.2.10' in card['summary']
    assert fields['event_id'] == 'source' and 'src_ip' not in fields and 'dst_ip' not in fields
    assert '尚无已保存' in fields['proof_status']
    assert 'alert_id' not in card and 'evidence_id' not in card and 'query' not in card
    assert timeline[0]['kind'] == 'source_event' and timeline[0]['first_time'] == event['startTime']
    assert source_event_evidence({**event, 'key': 'other:incident:source'}, []) == (None, [])


@pytest.mark.parametrize('success,has_details,text', [(False, False, '查询失败'),
    (True, False, '未生成可归属'), (True, True, '见下方证据')])
def test_source_proof_outcome_does_not_turn_failure_or_empty_content_into_missing_source_alert(success, has_details, text):
    event = incident()
    card, _ = source_event_evidence(event, [{'kind': 'proof', 'device': event['device'],
        'success': success, 'error': '设备超时'}], has_details=has_details)
    assert text in next(f['value'] for f in card['fields'] if f['key'] == 'proof_status')


async def test_detail_keeps_live_source_event_when_no_investigation_packets_exist():
    await install()
    event = incident('newest', name='主机存在r57 Webshell访问，同时发现横向移动行为')
    await observe(event)
    await investigate(event, evidence=[], state='system_wait', result={'verdict': 'unknown', 'reason': '模型暂不可用'})
    detail = await event_list.detail('owner', 'host-security-monitor', event['key'])
    assert detail['event']['handling'] != 'ended'
    assert detail['source_event_evidence']['kind'] == 'source_event'
    assert '主机存在r57' in detail['source_event_evidence']['summary']
    assert detail['source_event_timeline'][0]['end_time'] == event['endTime']
    assert detail['story']['evidence_cards'] == []  # Source snapshot cannot satisfy risk admission.
    assert detail['investigation']['evidence'] == []
    assert not detail['event']['assessment']['notify_eligible']

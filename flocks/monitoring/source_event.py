"""The saved XDR incident is source evidence, never an investigated verdict."""
from .event_details import FIELD_LABELS, normalize_basics
from .evidence_presenter import _bounded, _summary, _text


def source_event_evidence(event, proofs, *, has_details=False):
    if not isinstance(event, dict) or not event.get('id') or not event.get('device'):
        return None, []
    if event.get('key') != f"{event['device']}:incident:{event['id']}":
        return None, []
    basics = normalize_basics(event.get('basics', {}))
    basics.update({key: value for key, value in normalize_basics(event).items() if value not in (None, '', [])})
    basics['event_id'] = event['id']
    name = _text(basics.get('name') or event.get('name') or '安全事件', 120)
    host = _text(basics.get('host') or event.get('host'), 100)
    summary = (_summary(basics, '', []) if basics.get('src_ip') or basics.get('dst_ip') else
               f'XDR 报告主机 {host} 的「{name}」告警。' if host else f'XDR 报告了「{name}」告警，影响主机未提供。')
    attempts = [p for p in proofs if p.get('kind') == 'proof' and p.get('device') == event['device']]
    if any(p.get('success') for p in attempts):
        status = ('已取得源事件举证返回，关联告警及报文见下方证据。' if has_details else
                  '源事件举证查询已返回，但当前未生成可归属的关联告警或报文，仍需核对返回内容。')
    elif attempts:
        status = '源事件举证查询失败：' + _text(attempts[-1].get('error') or '未取得有效返回', 180)
    else:
        status = '当前事件版本尚无已保存的举证查询结果，时间线、IP 和报文仍待获取。'
    fields = [{'key': key, 'label': FIELD_LABELS[key], 'value': _bounded(value, 4000)}
              for key, value in basics.items() if key in FIELD_LABELS and value not in (None, '', [])]
    fields.append({'key': 'proof_status', 'label': '时间线与举证查询', 'value': status})
    card = {'id': 'source-event-' + _text(event['id'], 200), 'kind': 'source_event',
            'summary': '源事件：' + summary, 'source': _text(event.get('device_name') or event['device'], 160),
            'time': next((basics[key] for key in ('alert_time', 'end_time', 'start_time') if basics.get(key) is not None), None),
            'fields': fields, 'packets': [], 'records': [], 'raw': _bounded(basics),
            'boundary': '这是 XDR 报告的源事件；具体攻击行为和影响仍需结合告警举证核对。'}
    first, end = basics.get('start_time'), basics.get('end_time')
    timeline = [{'id': card['id'], 'kind': 'source_event', 'first_time': first, 'end_time': end,
                 'text': summary, 'source': card['source']}] if first is not None or end is not None else []
    return card, timeline

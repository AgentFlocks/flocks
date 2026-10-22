"""Event-bound operator evidence, without inferring a new security verdict."""
import json
import re
from urllib.parse import unquote

from .event_details import (FIELD_LABELS, PACKET_FIELDS, PACKET_LABELS,
                            _SECRET_NAME, _SQL, documented_fields, normalize_basics, redact_text, safe_facts)

MAX_CARDS = 36
MAX_RAW_BYTES = 24_000
MAX_RECORDS = 36
_MEANINGFUL = {'src_ip', 'dst_ip', 'domain', 'url', 'virus_name', 'file_name',
               'file_path', 'file_md5', 'file_hash', 'process_name', 'command_line', 'x_forwarded_for',
               'threat_message', 'request_line', 'response_line'}
_ENTITY_CONTAINERS = {'entities', 'entity', 'host', 'file', 'process', 'ip', 'innerip',
                      'dns', 'srcProcess', 'net', 'http', 'originalAlert', 'threat'}
_ALERT_CONTAINERS = {'alertTimeLine', 'alertTimeline', 'alert', 'alerts'}


def _text(value, limit=220):
    if isinstance(value, list):
        parts = [str(v) for v in value if type(v) in (str, int, float)]
        value = '、'.join(parts[:3]) + (f'等 {len(parts)} 项' if len(parts) > 3 else '')
    text = redact_text(value if type(value) in (str, int, float) else '')
    return text[:limit] + ('…' if len(text) > limit else '')


def _bounded(value, limit=MAX_RAW_BYTES):
    """Final masking protects legacy query parameters as well as packet text."""
    nodes = [0]
    def visit(item, depth=0):
        nodes[0] += 1
        if depth > 12 or nodes[0] > 1600:
            return '…（已截断）'
        if isinstance(item, dict):
            return {str(key): '[已脱敏]' if re.fullmatch(_SECRET_NAME, str(key), re.I)
                    else visit(nested, depth+1) for key, nested in list(item.items())[:200]}
        if isinstance(item, list):
            return [visit(v, depth+1) for v in item[:100]] + (['…（已截断）'] if len(item) > 100 else [])
        if isinstance(item, str):
            return _text(item, 8000)
        return item if item is None or type(item) in (int, float, bool) else None
    value = visit(value)
    raw = json.dumps(value, ensure_ascii=False, default=str)
    if len(raw.encode()) > limit:
        return {'excerpt': raw.encode()[:limit-300].decode(errors='ignore'), 'truncated': True}
    return value


def bound_evidence(event, evidence, *, canonical=None, include_failed=False):
    """Successful queries must bind the original incident and saved revision.

    Cross-device searches may use another source device. Their correlation
    remains a candidate, rather than an assumed matching asset.
    """
    from .assessment import same_evidence_version
    from .investigation import fingerprint
    event = event if isinstance(event, dict) else {}
    canonical = canonical if isinstance(canonical, dict) else event
    if not same_evidence_version(canonical, event):
        return []
    revision = fingerprint(canonical)
    result = []
    for proof in evidence[:24] if isinstance(evidence, list) else []:
        if (not isinstance(proof, dict) or not include_failed and proof.get('success') not in (True, 1)
                or proof.get('event') != event.get('id') or proof.get('event_revision') != revision):
            continue
        if not isinstance(proof.get('kind'), str):
            continue
        query = proof.get('query') if isinstance(proof.get('query'), dict) else {}
        if proof.get('kind') in {'proof', 'host', 'file', 'process', 'ip', 'innerip', 'dns'}:
            if query.get('action') in {'get_proof', 'get_entities'} and query.get('uuid') != event.get('id'):
                continue
        if (proof.get('device') != event.get('device') and proof.get('kind') != 'related'
                and str(proof.get('tool', '')).startswith('sangfor_xdr')):
            continue
        if proof.get('device'):
            result.append(proof)
    return result


def _identifiers(value, *, alert_row=False):
    """Only alert fields/records identify an alert; entity UUIDs never do."""
    identifiers = []
    value = documented_fields(value)
    for key in ('alertId', 'alert_ids', 'alertIds', 'threat.id'):
        supplied = value.get(key)
        values = supplied if isinstance(supplied, list) else [supplied]
        identifiers.extend(_text(item, 1000) for item in values if isinstance(item, str) and item)
    if identifiers:
        return tuple(dict.fromkeys(identifiers))
    if alert_row:
        supplied = value.get('uuId') or value.get('uuid') or value.get('id')
        if isinstance(supplied, str) and supplied and not supplied.startswith('incident-'):
            return (_text(supplied, 1000),)
    return ()


def _descendant_alerts(value):
    """Single explicit child identities can separate an aggregate alert row."""
    found = set()
    nested = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
    for item in nested:
        if isinstance(item, dict):
            identifiers = _identifiers(item)
            if len(identifiers) == 1:
                found.update(identifiers)
            found.update(_descendant_alerts(item))
        elif isinstance(item, list):
            found.update(_descendant_alerts(item))
    return found


def _records(value, *, kind, fallback=(), context=None, identifiers=(), anchor=None,
             path=(), entity=False, alert_row=False, aggregate=None, allow_generic_alert_ids=True):
    """Walk one source tree while retaining its alert and parent-record scope."""
    context = context or {}
    if isinstance(value, dict):
        entity = entity or kind in {'host', 'file', 'process', 'ip', 'innerip', 'dns'}
        own = normalize_basics(value)
        if not own.get('domain') and value.get('domainName'):
            own['domain'] = value['domainName']
        # A generic UUID in an alert list identifies its row, not every nested
        # file/process/entity. An alert-proof query already identifies its target.
        row_identity = allow_generic_alert_ids and not entity and (alert_row or kind == 'alerts' and (
            any(key in own for key in ('name', 'alert_time', 'src_ip', 'dst_ip', 'url', 'x_forwarded_for'))
            or 'proofType' in value))
        explicit = _identifiers(value, alert_row=row_identity)
        # normalize_basics supports old alert-* UUID payloads. That convention
        # alone is insufficient to turn an entity UUID into an alert identity.
        own.pop('alert_ids', None)
        if explicit:
            if set(explicit) != set(identifiers):
                # A newly identified sibling/child alert does not inherit the
                # preceding alert's network, process, name or file observations.
                context = {key: context[key] for key in ('data_source', 'source_device') if key in context}
            if aggregate and not set(explicit).issubset(aggregate[2]):
                aggregate = None
            identifiers = explicit
            own.pop('event_id', None)
            own['alert_ids'] = list(explicit)
            anchor = path
        elif not identifiers and fallback:
            identifiers = fallback
        if identifiers and not any(key in value for key in ('event_id', 'incidentId', 'incidentUuid')):
            own.pop('event_id', None)
        if entity:
            own.pop('event_id', None)
            if value.get('alertName'):
                own['name'] = normalize_basics({'alertName': value['alertName']}).get('name')
            else:
                own.pop('name', None)
        merged = {**context, **own}
        if identifiers:
            merged['alert_ids'] = list(identifiers)
        if len(explicit) > 1 and set(explicit).issubset(_descendant_alerts(value)):
            aggregate = (value, path, set(explicit))
        packet = any(alias in documented_fields(value) for aliases in PACKET_FIELDS.values() for alias in aliases)
        meaningful = any(own.get(key) not in (None, '', []) for key in _MEANINGFUL) or packet
        named_proof = own.get('name') and any(isinstance(value.get(key), dict) for key in ('proof', 'detail', 'details'))
        if anchor is None and (meaningful or named_proof):
            anchor = path
        split_summary = aggregate and len(identifiers) > 1 and set(identifiers).issubset(aggregate[2])
        if (meaningful or own.get('name') and identifiers) and not split_summary:
            yield value, merged, identifiers, anchor if anchor is not None else path, path, aggregate
        for key, nested in value.items():
            if isinstance(nested, (dict, list)):
                yield from _records(nested, kind=kind, fallback=fallback, context=merged,
                                    identifiers=identifiers, anchor=anchor, path=(*path, key),
                                    entity=entity or key in _ENTITY_CONTAINERS,
                                    alert_row=key in _ALERT_CONTAINERS, aggregate=aggregate,
                                    allow_generic_alert_ids=allow_generic_alert_ids)
    elif isinstance(value, list):
        for index, item in enumerate(value[:100]):
            yield from _records(item, kind=kind, fallback=fallback, context=context,
                                identifiers=identifiers, anchor=anchor, path=(*path, index),
                                entity=entity, alert_row=alert_row, aggregate=aggregate,
                                allow_generic_alert_ids=allow_generic_alert_ids)


def _entity(value):
    """One address and a count; never invent pairings within aggregate lists."""
    values = value if isinstance(value, list) else [value]
    values = list(dict.fromkeys(_text(item, 45) for item in values if type(item) in (str, int) and str(item).strip()))
    return (values[0] + (f' 等 {len(values)} 个 IP' if len(values) > 1 else '')) if values else ''


def _summary(record, source, packets):
    """Say who accessed what, keeping rule claims distinct from verified impact."""
    src, dst = _entity(record.get('src_ip')), _entity(record.get('dst_ip'))
    access = (f'{src} 访问 {dst}' if src and dst else f'源 IP 未提供的请求访问 {dst}' if dst
              else f'{src} 发起访问（目的 IP 未提供）' if src else '')
    if any(_SQL.search(unquote(packet['text'])) for packet in packets if packet['field'].startswith('request')):
        return (f'{access}，请求中包含 SQL 注入可疑语句。' if access
                else '已取得的请求中包含 SQL 注入可疑语句。')
    name = re.sub(r'^(?:【[^】]*】|\[[^\]]*\])\s*', '', _text(record.get('name'), 100)).strip()
    lower = name.lower()
    if name:
        action = ''
        if '随机域名' in name:
            return f'告警显示：{src or "源 IP 未提供的主机"} 向 {dst or "地址未提供的 DNS 服务器"} 发起了大量随机域名的 DNS 查询。'
        if re.search(r'ba[ck].*(?:文件|访问)|\.bak', lower):
            action = '请求访问 .bak 备份文件' if 'bak' in lower else '请求访问备份或临时文件'
        elif 'druid' in lower and '未授权' in name:
            action = '疑似未授权访问 Druid 服务'
        elif '敏感文件访问' in name:
            action = '请求访问敏感文件'
        elif 'jetbrains' in lower and ('泄露' in name or 'idea' in lower):
            action = '疑似利用 JetBrains IDEA 漏洞读取信息'
        elif 'ds_store' in lower:
            action = '请求读取 .DS_Store 目录信息文件'
        elif 'webshell' in lower and '扫描' in name:
            action = '疑似扫描 Webshell'
        elif '配置文件访问' in name:
            action = '请求读取服务器配置文件'
        elif 't3' in lower and ('weblogic' in lower or '协议' in name):
            action = '发起 WebLogic T3 协议连接'
        if access:
            return f'告警显示：{access}，{action}。' if action else f'{access}，触发「{name.rstrip("。 ")}」告警。'
        return f'告警报告了「{name.rstrip("。 ")}」，未提供源 IP 和目的 IP。'
    return _unnamed_summary(record, packets)


def _unnamed_summary(record, packets):
    """Fallback for observations that have no supplied alert name."""
    if record.get('process_name') or record.get('command_line'):
        return f'发现「{_text(record.get("process_name") or "程序", 60)}」的进程或命令行记录。'
    if record.get('file_path') or record.get('file_name'):
        filename = _text(record.get('file_name') or str(record['file_path']).replace(chr(92), '/').rsplit('/', 1)[-1], 70)
        tag = _text(record.get('virus_name'), 60)
        return f'发现文件「{filename}」' + (f'，来源标记为「{tag}」' if tag else '') + '。'
    ports = record.get('dst_port')
    ports = ports if isinstance(ports, list) else [ports]
    dns = bool(ports) and all(str(port) == '53' for port in ports)
    if record.get('domain') and dns:
        domains = record['domain'] if isinstance(record['domain'], list) else [record['domain']]
        src = _entity(record.get('src_ip')) or '源 IP 未提供的主机'
        return (f'{src} 发起 DNS 查询，涉及 {len(domains)} 个域名。' if len(domains) > 1
                else f'{src} 查询了域名「{_text(domains[0], 80)}」。')
    if record.get('url'):
        src, dst = _entity(record.get('src_ip')), _entity(record.get('dst_ip'))
        return (f'{src} 请求访问「{_text(record["url"], 90)}」' + (f'（目的 IP：{dst}）' if dst else '') + '。' if src
                else f'来源记录了对「{_text(record["url"], 90)}」的访问请求（源 IP 未提供）。')
    if record.get('src_ip') and record.get('dst_ip'):
        tag = _text(record.get('intelligenceTag') or record.get('threatDefineName'), 60)
        return f'{_entity(record["src_ip"])} 访问 {_entity(record["dst_ip"])}' + (f'，目标被标记为「{tag}」' if tag else '') + '。'
    if record.get('x_forwarded_for'):
        return '记录了 X-Forwarded-For 请求头原值。'
    if record.get('domain'):
        return f'记录了关联域名「{_text(record["domain"], 80)}」。'
    if record.get('threat_message'):
        return f'告警信息：{_text(record["threat_message"], 100)}'
    if record.get('request_line') or record.get('response_line') or packets:
        return '已取得该告警的请求或响应记录。'
    if record.get('virus_name'):
        return f'来源标记了威胁「{_text(record["virus_name"], 70)}」。'
    return ''


def _kind(record, packets):
    if packets or record.get('url') or record.get('request_line') or record.get('response_line'):
        return 'request'
    if record.get('process_name') or record.get('command_line'):
        return 'process'
    if record.get('file_path') or record.get('file_name'):
        return 'file'
    if record.get('src_ip') and record.get('dst_ip'):
        return 'network'
    return 'domain' if record.get('domain') else 'observation'


def _merge_field(fields, key, value):
    if value in (None, '', []):
        return
    if key not in fields:
        fields[key] = value
        return
    if fields[key] == value:
        return
    prior = fields[key] if isinstance(fields[key], list) else [fields[key]]
    incoming = value if isinstance(value, list) else [value]
    fields[key] = prior + [item for item in incoming if item not in prior]


def evidence_cards(event, evidence, *, canonical=None):
    """One card per source device and alert, with bounded query provenance."""
    groups, limited = {}, False
    for proof_index, proof in enumerate(bound_evidence(event, evidence, canonical=canonical)):
        # The current OneSIG Strategy API supplies asset/policy configuration,
        # not threat logs. Its results belong to the correlation query view.
        if proof.get('product') == 'sig' or str(proof.get('tool', '')).startswith('onesig_'):
            continue
        facts, clipped = safe_facts(proof.get('facts', {}))
        source = _text(proof.get('source_name') or proof.get('device_name') or proof.get('tool') or proof['device'], 100)
        cross = proof.get('device') != event.get('device')
        query = proof.get('query') if isinstance(proof.get('query'), dict) else {}
        fallback = ((_text(query['uuid'], 1000),) if proof.get('kind') == 'alert_proof'
                    and query.get('action') == 'get_proof' and isinstance(query.get('uuid'), str)
                    and query['uuid'] else ())
        is_tdp = proof.get('product') == 'tdp' or str(proof.get('tool', '')).startswith('tdp_')
        for row, record, identifiers, anchor, path, aggregate in _records(facts, kind=proof['kind'], fallback=fallback,
                                                                         allow_generic_alert_ids=not is_tdp):
            single = len(identifiers) == 1
            key = (proof['device'], identifiers[0]) if single else (proof['device'], proof_index, anchor)
            if key not in groups:
                groups[key] = {'fields': {}, 'packets': [], 'records': [], 'paths': [],
                               'alert_id': identifiers[0] if single else None,
                               'source': source, 'partial': False, 'boundaries': []}
            group = groups[key]
            group['partial'] |= bool(proof.get('partial') or clipped)
            forwarded = record.get('x_forwarded_for')
            forwarded_values = forwarded if isinstance(forwarded, list) else [forwarded]
            group['partial'] |= any(isinstance(value, str) and '已截断' in value for value in forwarded_values)
            boundaries = []
            if cross:
                boundaries.append('跨设备查询为候选关联，须核对是否为同一资产及同一时间范围。')
            if len(identifiers) > 1:
                boundaries.append('来源记录同时关联多条告警，未能确定各项事实所属的单条告警，按独立记录保留。')
            elif not identifiers:
                boundaries.append('来源未提供可核对的告警编号，按独立记录保留，未与其他查询推定合并。')
            if aggregate:
                boundaries.append('原始记录包含多条告警的上层汇总；上方仅展示可明确归属本告警的内容。')
            for boundary in boundaries:
                if boundary not in group['boundaries']:
                    group['boundaries'].append(boundary)
            for name, value in record.items():
                if name in FIELD_LABELS:
                    _merge_field(group['fields'], name, value)
            for name in ('intelligenceTag', 'threatDefineName', 'gptResult', 'threatLevel', 'threat.name'):
                if row.get(name) is not None:
                    _merge_field(group['fields'], name, row[name])
            packet_row = documented_fields(row)
            for field, aliases in PACKET_FIELDS.items():
                for alias in aliases:
                    if alias in packet_row and isinstance(packet_row[alias], str):
                        packet = {'field': field, 'label': PACKET_LABELS[field], 'text': packet_row[alias],
                                  'truncated': '已截断' in packet_row[alias]}
                        if packet not in group['packets']:
                            group['packets'].append(packet)
            source_row, source_path = (aggregate[0], aggregate[1]) if aggregate else (row, path)
            if any(index == proof_index and source_path[:len(parent)] == parent for index, parent in group['paths']):
                continue
            if len(group['records']) >= MAX_RECORDS:
                group['partial'] = True
                note = '证据明细达到展示上限，部分来源记录未展示。'
                if note not in group['boundaries']:
                    group['boundaries'].append(note)
                continue
            raw = _bounded(source_row)
            bounded_query = _bounded(query, 8000)
            group['partial'] |= bool(isinstance(raw, dict) and raw.get('truncated'))
            group['partial'] |= bool(isinstance(bounded_query, dict) and bounded_query.get('truncated'))
            group['records'].append({'evidence_id': _text(proof.get('id'), 100), 'source': source,
                                     'query': bounded_query, 'raw': raw})
            group['paths'].append((proof_index, source_path))
    cards = []
    extra_labels = {'intelligenceTag': '来源情报标签', 'threatDefineName': '来源威胁定义',
                    'gptResult': '来源研判原值', 'threatLevel': '来源威胁等级原值', 'threat.name': '来源威胁名称'}
    for group in groups.values():
        record, packets = group['fields'], group['packets']
        summary = _summary(record, group['source'], packets)
        if not summary:
            continue
        # Only usable cards consume the display budget. All bounded source
        # queries have already been merged, including later proof for this ID.
        if len(cards) >= MAX_CARDS:
            limited = True
            continue
        fields = []
        for key, label in {**FIELD_LABELS, **extra_labels}.items():
            if key not in record:
                continue
            bounded = _bounded(record[key], 4000)
            group['partial'] |= bounded != record[key]
            fields.append({'key': key, 'label': label, 'value': bounded})
        stamp = next((record[key] for key in ('alert_time', 'start_time', 'end_time')
                      if record.get(key) is not None), None)
        if isinstance(stamp, list):
            stamp = stamp[0] if stamp else None
        first = group['records'][0] if group['records'] else {}
        cards.append({'id': f'{first.get("evidence_id", "proof")}-{len(cards)+1}',
                      'summary': summary, 'source': group['source'], 'kind': _kind(record, packets),
                      'time': stamp, 'partial': bool(group['partial'] or limited),
                      'fields': fields, 'packets': packets, 'raw': first.get('raw', {}),
                      'query': first.get('query', {}), 'records': group['records'],
                      'boundary': ' '.join(group['boundaries']),
                      'evidence_id': first.get('evidence_id', ''),
                      **({'alert_id': group['alert_id']} if group['alert_id'] else {})})
    if limited:
        for card in cards:
            card['partial'] = True
    return cards

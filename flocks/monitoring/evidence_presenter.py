"""Event-bound operator evidence, without inferring a new security verdict."""
import json
import re
from urllib.parse import unquote

from .event_details import (FIELD_LABELS, PACKET_FIELDS, PACKET_LABELS,
                            _SECRET_NAME, _SQL, normalize_basics, redact_text, safe_facts)

MAX_CARDS = 36
MAX_RAW_BYTES = 24_000
_MEANINGFUL = {'src_ip', 'dst_ip', 'domain', 'url', 'virus_name', 'file_name',
               'file_path', 'file_md5', 'file_hash', 'process_name', 'command_line'}
_INHERITED = {'host', 'src_ip', 'dst_ip', 'src_port', 'dst_port', 'data_source',
              'source_device', 'alert_time', 'start_time', 'end_time', 'name'}


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


def bound_evidence(event, evidence, *, canonical=None):
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
        if (not isinstance(proof, dict) or proof.get('success') not in (True, 1)
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


def _records(value, context=None):
    context = context or {}
    if isinstance(value, dict):
        own = normalize_basics(value)
        if not own.get('domain') and value.get('domainName'):
            own['domain'] = value['domainName']
        merged = {**context, **own}
        packet = any(alias in value for aliases in PACKET_FIELDS.values() for alias in aliases)
        if any(key in own and own[key] not in (None, '', []) for key in _MEANINGFUL) or packet:
            yield value, merged
        child_context = {key: merged[key] for key in _INHERITED if key in merged}
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                yield from _records(nested, child_context)
    elif isinstance(value, list):
        for item in value[:100]:
            yield from _records(item, context)


def _summary(record, row, source, packets):
    src = _text(record.get('src_ip') or record.get('host'), 65)
    dst, domain = _text(record.get('dst_ip'), 90), _text(record.get('domain'), 100)
    port = record.get('dst_port')
    ports = port if isinstance(port, list) else [port]
    dns = bool(ports) and all(str(p) == '53' for p in ports)
    name = _text(record.get('name') or record.get('virus_name'), 65)
    if src and dst:
        if dns:
            observed = f'记录主机 {src} 向 {dst} 发起 DNS 查询'
            if domain:
                observed += f'，包含 {domain}'
            meaning = '提供域名解析行为依据，不能据此认定已连接恶意服务器或感染。'
        else:
            observed = f'记录主机 {src} 访问 {dst}'
            if port not in (None, '', []):
                observed += f'（目的端口 {_text(port, 45)}）'
            meaning = '提供该网络访问行为的依据。'
        if name:
            observed += f'，来源记录为「{name}」'
        tag = _text(row.get('intelligenceTag') or row.get('threatDefineName') or row.get('threat.name'), 70)
        if tag:
            observed += f'，来源情报标记为「{tag}」'
        return f'{source}{observed}。{meaning}'
    if record.get('file_path') or record.get('file_name'):
        identity = _text(record.get('file_path') or record.get('file_name'), 110)
        virus = _text(record.get('virus_name'), 70)
        return (f'{source}记录文件「{identity}」' + (f'，检出「{virus}」' if virus else '')
                + '。提供文件侧取证线索，运行和感染情况仍须结合进程证据。')
    if record.get('process_name') or record.get('command_line'):
        process = _text(record.get('process_name') or '程序', 70)
        return f'{source}记录「{process}」的进程或命令行。提供程序执行线索，具体启动命令见详情。'
    if record.get('url') or packets:
        request = any(p['field'].startswith('request') for p in packets)
        response = any(p['field'].startswith('response') for p in packets)
        if any(_SQL.search(unquote(packet['text'])) for packet in packets if packet['field'].startswith('request')):
            return f'{source}的请求中出现 SQL 注入可疑语句。提供攻击尝试的内容依据，尚不能单独证明利用成功。'
        what = '请求及响应' if request and response else '请求' if request else '响应' if response else '请求地址'
        return f'{source}保存了{what}记录。可核对实际访问内容及服务端返回，不能仅凭状态码认定攻击成功。'
    if domain:
        tag = _text(row.get('intelligenceTag') or row.get('threatDefineName') or row.get('threat.name'), 70)
        return f'{source}记录关联域名 {domain}' + (f'，来源情报标记为「{tag}」' if tag else '') + '。提供域名线索，关联记录不表示已建立连接。'
    if record.get('virus_name'):
        return f'{source}记录威胁「{_text(record["virus_name"], 80)}」。此为来源识别线索，需结合实际行为判断。'
    return ''


def _views(record, packets):
    categories = []
    if record.get('src_ip') and record.get('dst_ip'):
        categories.append(('network', {'src_ip', 'dst_ip', 'domain', 'dst_port', 'name', 'host'}))
    elif record.get('domain'):
        categories.append(('domain', {'domain'}))
    if record.get('file_path') or record.get('file_name'):
        categories.append(('file', {'file_path', 'file_name', 'virus_name'}))
    if record.get('process_name') or record.get('command_line'):
        categories.append(('process', {'process_name', 'command_line'}))
    if packets or record.get('url'):
        categories.append(('request', {'url'}))
    if not categories:
        categories.append(('observation', set(record)))
    for kind, category in categories:
        yield {key: value for key, value in record.items() if key in category}, packets if 'url' in category else [], kind


def evidence_cards(event, evidence, *, canonical=None):
    """One readable observation per card; detailed facts are expandable."""
    cards, seen = [], set()
    for proof in bound_evidence(event, evidence, canonical=canonical):
        facts, clipped = safe_facts(proof.get('facts', {}))
        source = _text(proof.get('source_name') or proof.get('device_name') or proof.get('tool') or proof['device'], 100)
        cross = proof.get('device') != event.get('device')
        for row, record in _records(facts):
            packets = []
            for field, aliases in PACKET_FIELDS.items():
                alias = next((name for name in aliases if name in row), None)
                if alias and isinstance(row[alias], str):
                    packets.append({'field': field, 'label': PACKET_LABELS[field], 'text': row[alias],
                                    'truncated': '已截断' in row[alias]})
            for view, summary_packets, kind in _views(record, packets):
                summary = _summary(view, row, source, summary_packets)
                if not summary:
                    continue
                stamp = next((record[key] for key in ('alert_time', 'start_time', 'end_time')
                              if record.get(key) is not None), None)
                marker = (proof['device'], summary, str(stamp))
                if marker in seen:
                    continue
                seen.add(marker)
                fields = [{'key': key, 'label': FIELD_LABELS[key], 'value': _bounded(record[key], 4000)}
                          for key in FIELD_LABELS if key in record and record[key] not in (None, '', [])]
                for key, label in (('intelligenceTag', '来源情报标签'), ('threatDefineName', '来源威胁定义'),
                                   ('gptResult', '来源研判原值'), ('threatLevel', '来源威胁等级原值')):
                    if row.get(key) is not None:
                        fields.append({'key': key, 'label': label, 'value': _bounded(row[key], 2000)})
                cards.append({'id': f'{proof.get("id", "proof")}-{len(cards)+1}', 'summary': summary,
                              'source': source, 'kind': kind, 'time': stamp, 'partial': bool(proof.get('partial') or clipped),
                              'fields': fields, 'packets': packets, 'raw': _bounded(row),
                              'query': _bounded(proof.get('query', {}), 8000),
                              'boundary': '跨设备查询为候选关联，须核对是否为同一资产及同一时间范围。' if cross else '',
                              'evidence_id': _text(proof.get('id'), 100)})
                if len(cards) >= MAX_CARDS:
                    return cards
    return cards

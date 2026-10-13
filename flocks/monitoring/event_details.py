"""Bounded, plain-text incident facts shared by investigation, chat and mail.

This module extracts facts; it neither changes the investigation verdict nor
infers successful exploitation from a matching string or an HTTP status code.
"""
from datetime import datetime, timezone
import json
import re
import unicodedata
from urllib.parse import unquote

MAX_FACT_BYTES = 24_000
MAX_PACKET_BODY = 8_000
MAX_PACKET_HEAD = 4_000
MAX_TEXT = 1_000
_TRUNCATED = '…（已截断，内容不完整）'
_REDACTED = '[已脱敏]'

# Each value is the first explicitly supplied alias, never a guessed value.
BASIC_FIELDS = {
    'event_id': ('event_id', 'incidentId', 'incidentUuid', 'uuId', 'uuid', 'id'),
    'alert_ids': ('alert_ids', 'alertIds', 'alertId'),
    'name': ('name', 'alertName', 'incidentName'),
    'alert_time': ('alert_time', 'alertTime', 'eventTime', 'timestamp', 'time', 'lastTime', 'firstTime', 'firstTimestamp', 'startTime'),
    'start_time': ('start_time', 'startTime', 'firstTime', 'firstTimestamp'),
    'end_time': ('end_time', 'endTime', 'lastTime', 'lastTimestamp'),
    'severity': ('severity', 'incidentSeverity', 'riskLevel', 'threatLevel'),
    'host': ('host', 'hostIp', 'hostIP', 'assetIp'),
    'domain': ('domain', 'hostDomain', 'destDomain', 'dstDomain'),
    'data_source': ('data_source', 'dataSource', 'dataType', 'sourceType', 'deviceType', 'devUidDesc'),
    'source_device': ('source_device', 'devSourceName', 'devSourceNames', 'deviceName', 'sensorName', 'sourceDeviceName'),
    'src_ip': ('src_ip', 'srcIp', 'srcIps', 'sourceIp', 'net.src_ip'),
    'src_port': ('src_port', 'srcPort', 'sourcePort', 'net.src_port'),
    'dst_ip': ('dst_ip', 'dstIp', 'dstIps', 'destIp', 'destinationIp', 'net.dest_ip'),
    'dst_port': ('dst_port', 'dstPort', 'destPort', 'destinationPort', 'net.dest_port'),
    'virus_name': ('virus_name', 'virusName', 'malwareName', 'threatName'),
    'file_id': ('file_id', 'fileId', 'fileUuid'),
    'file_md5': ('file_md5', 'fileMd5', 'fileMD5', 'md5', 'virusFileMd5'),
    'file_hash': ('file_hash', 'fileHash', 'sha256'),
    'file_name': ('file_name', 'fileName'),
    'file_path': ('file_path', 'filePath', 'path'),
    'file_size': ('file_size', 'fileSize', 'fileSizeKb', 'fileSizeKB'),
    'file_created_at': ('file_created_at', 'fileCreateTime', 'fileCreatedTime', 'createTime', 'creationTime'),
    'process_name': ('process_name', 'processName', 'pName'),
    'command_line': ('command_line', 'commandLine', 'cmdLine', 'processCommandLine'),
    'url': ('url', 'requestUrl', 'uri'),
    'method': ('method', 'requestMethod', 'httpMethod'),
    'deal_status': ('deal_status', 'dealStatus'),
    'attack_state': ('attack_state', 'attackState'),
    'attack_result': ('attack_result', 'attackResult'),
    'threat_subtype': ('threat_subtype', 'threatSubTypeDesc'),
    'stage': ('stage',),
}
FIELD_LABELS = {
    'event_id': '事件编号', 'alert_ids': '告警编号', 'name': '名称', 'alert_time': '告警时间',
    'start_time': '首次发生时间', 'end_time': '最近发生时间', 'severity': '来源风险等级值',
    'host': '影响主机', 'domain': '影响域名', 'data_source': '数据源', 'source_device': '采集设备',
    'src_ip': '源 IP', 'src_port': '源端口', 'dst_ip': '目的 IP', 'dst_port': '目的端口',
    'virus_name': '病毒名称', 'file_id': '文件编号', 'file_md5': '文件 MD5', 'file_hash': '文件哈希',
    'file_name': '文件名称', 'file_path': '文件路径', 'file_size': '文件大小（来源原值）',
    'file_created_at': '文件创建时间', 'process_name': '进程名称', 'command_line': '命令行',
    'url': 'URL', 'method': '请求方法', 'deal_status': '来源处置状态值',
    'attack_state': 'XDR attackState', 'attack_result': 'XDR attackResult',
    'threat_subtype': '威胁子类型', 'stage': '来源阶段值',
}
PACKET_FIELDS = {
    'requestHead': ('requestHead', 'requestHeader', 'requestHeaders', 'request_head', 'request_headers'),
    'requestBody': ('requestBody', 'request_body'),
    'responseHead': ('responseHead', 'responseHeader', 'responseHeaders', 'response_head', 'response_headers'),
    'responseBody': ('responseBody', 'response_body'),
}
PACKET_LABELS = {'requestHead': '请求头', 'requestBody': '请求正文', 'responseHead': '响应头', 'responseBody': '响应正文'}
_SENSITIVE = re.compile(r'^(?:authorization|proxyauthorization|cookie|setcookie|password|passwd|pwd|token|accesstoken|refreshtoken|idtoken|authtoken|apikey|secret|clientsecret|authcode|credential|sessionkey)$', re.I)
_SECRET_NAME = r'(?:(?:x[_-])?(?:authorization|proxy-authorization|cookie|set-cookie|password|passwd|pwd|(?:(?:access|refresh|id|auth)[_-]?)?token|api[_-]?key|secret|client[_-]?secret|auth[_-]?code|credential|session[_-]?key))'
_CONTAINERS = {'data', 'item', 'items', 'list', 'records', 'result', 'results', 'proof', 'proofs', 'alertTimeLine',
               'alertTimeline', 'timeline', 'attackStory', 'rootCauseAnalysis', 'detail', 'details', 'alert', 'alerts',
               'incident', 'entities', 'entity', 'host', 'file', 'process', 'ip', 'innerip', 'dns', 'net', 'http',
               'srcProcess', 'aggregations'}
_FACT_KEYS = (_CONTAINERS | {alias for aliases in BASIC_FIELDS.values() for alias in aliases}
              | {alias for aliases in PACKET_FIELDS.values() for alias in aliases}
              | {'total', 'count', 'code', 'success', 'status', 'gptResult', 'threatLevel', 'threatDefineName',
                 'edrDealStatusInfo', 'ndrDealStatusInfo', 'isPermanent', 'expireTime', 'hostId', 'hostName',
                 'incidentThreatClass', 'incidentThreatType', 'threat.name', 'threat.level', 'threat.result',
                 'description', 'message', 'content', 'text', 'value', 'type', 'action', 'title', 'alertType',
                 'eventType', 'domainName', 'responseCode', 'respStatus', 'statusCode', 'httpStatus', 'bytes', 'protocol',
                 'proofType', 'kind', '_evidenceLimits',
                 'os', 'userName', 'parentProcessName', 'processId', 'pid', 'parentProcessId', 'ppid',
                 'processType', 'nodeId', 'guid', 'parentPath', 'fileModifyTime', 'fileModifier', 'intelligenceTag'})
_PACKET_ALIASES = {alias: key for key, aliases in PACKET_FIELDS.items() for alias in aliases}
_SQL = re.compile(r'@@version\b|\bunion\s+(?:all\s+)?select\b|\b(?:sleep|benchmark|load_file)\s*\(|\b(?:or|and)\s+[\'\"]?\d+[\'\"]?\s*=\s*[\'\"]?\d+|\bcase\s+when\b|\b(?:information_schema|xp_cmdshell)\b', re.I)


def _clean(text):
    return ''.join(c for c in str(text) if c in '\n\t' or not unicodedata.category(c).startswith('C'))


def redact_text(value):
    """Remove credentials from packet text/URLs/commands before any persistence."""
    text = _clean(value)
    # Multipart credentials live in the part body, rather than beside the
    # field name. Preserve the delimiter and headers while masking that body.
    multipart = (r'(?ims)(^content-disposition[ \t]*:[^\n]*\bname[ \t]*=[ \t]*'
                 r'(?:"' + _SECRET_NAME + r'"|\'' + _SECRET_NAME + r'\'|' + _SECRET_NAME + r')'
                 r'(?=[; \t\n]|$)[^\n]*\n(?:[^\n]+\n)*\n)(.*?)'
                 r'(?=\n--[^\n]*(?:\n|$)|\Z)')
    text = re.sub(multipart, lambda m: m[1] + _REDACTED, text)
    # Header lines may be flattened by upstream tools. Remove conservatively
    # through the end of that line rather than risk keeping a packed secret.
    text = re.sub(r'(?im)(\b' + _SECRET_NAME + r'\s*:\s*)[^\r\n]+',
                  lambda m: m[1] + _REDACTED, text)
    text = re.sub(r'(?i)(https?://)[^\s/@:]+:[^\s/@]+@', lambda m: m[1] + _REDACTED + '@', text)
    # Quoted JSON assignments, query/form values and command-line arguments.
    text = re.sub(r'(?i)([\"\']' + _SECRET_NAME + r'[\"\']\s*:\s*)([\"\'])(?:\\.|(?!\2).)*?\2',
                  lambda m: m[1] + '"' + _REDACTED + '"', text)
    text = re.sub(r'(?i)([\"\']' + _SECRET_NAME + r'[\"\']\s*:\s*)'
                  r'(?:-?\d+(?:\.\d+)?(?:e[+-]?\d+)?|true|false|null)(?=\s*[,}\]])',
                  lambda m: m[1] + '"' + _REDACTED + '"', text)
    text = re.sub(r'(?i)(\b' + _SECRET_NAME + r'\s*=\s*)([\"\'])(?:\\.|(?!\2).)*?\2',
                  lambda m: m[1] + '"' + _REDACTED + '"', text)
    text = re.sub(r'(?i)(\b' + _SECRET_NAME + r'\s*=\s*)([^\s&;,\"\'<>]+)',
                  lambda m: m[1] + _REDACTED, text)
    text = re.sub(r'(?i)((?:--?|/)'+ _SECRET_NAME + r'(?:\s+|[:=]))(?:"[^"]*"|\'[^\']*\'|[^\s]+)',
                  lambda m: m[1] + _REDACTED, text)
    return text


def _clip(value, limit):
    text = redact_text(value)
    encoded = text.encode('utf-8')
    if len(encoded) <= limit:
        return text, False
    room = max(0, limit - len(_TRUNCATED.encode('utf-8')))
    return encoded[:room].decode('utf-8', errors='ignore') + _TRUNCATED, True


def _scalar(value, limit=MAX_TEXT):
    if type(value) in (str, int, float, bool):
        return _clip(value, limit)[0] if isinstance(value, str) else value
    if isinstance(value, list):
        return [_scalar(v, 200) for v in value[:50] if type(v) in (str, int, float, bool)]
    return None


def normalize_basics(raw):
    """Normalize the supplied host/Web fields; absent values stay absent."""
    if not isinstance(raw, dict):
        return {}
    normalized = {}
    for field, aliases in BASIC_FIELDS.items():
        for alias in aliases:
            value = raw.get(alias)
            if value is not None and value != '' and not isinstance(value, dict):
                safe = _scalar(value, 2000 if field == 'command_line' else MAX_TEXT)
                if safe is not None:
                    normalized[field] = safe
                break
    if raw.get('severity_field') in {'incidentSeverity', 'riskLevel', 'severity'}:
        normalized['severity_field'] = raw['severity_field']
    elif type(raw.get('incidentSeverity')) is int:
        normalized['severity_field'] = 'incidentSeverity'
        normalized['severity'] = raw['incidentSeverity']
    elif type(raw.get('riskLevel')) is int:
        normalized['severity_field'] = 'riskLevel'
        normalized['severity'] = raw['riskLevel']
    elif 'severity' in normalized:
        normalized['severity_field'] = 'severity'
    identifier = raw.get('uuId') or raw.get('uuid') or raw.get('id')
    if isinstance(identifier, str) and identifier.startswith('alert-'):
        normalized.pop('event_id', None)
        normalized.setdefault('alert_ids', [identifier])
    return normalized


def safe_facts(value):
    """Keep bounded security facts, including sanitized proof and HTTP fields.

    Unknown metadata is ignored without declaring evidence incomplete. Explicit
    truncation, unreadable packet structures and upstream partial flags do mark
    it incomplete. The marker is data, never an instruction for the model.
    """
    remaining, notes = [MAX_FACT_BYTES - 1500], []
    nodes = [0]

    def note(reason):
        if reason not in notes and len(notes) < 8:
            notes.append(reason)

    def charge(value):
        size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))
        if size > remaining[0]:
            note('总事实预算已用完，内容不完整')
            return None
        remaining[0] -= size
        return value

    def packet_value(item, field):
        if item is None:
            note(f'{PACKET_LABELS[field]}未取得')
            return None
        if isinstance(item, (dict, list)):
            # Packet bodies may be JSON with application-specific keys. Preserve
            # that context as text, after recursively masking sensitive values.
            def mask(part, depth=0):
                if depth > 12:
                    note('报文嵌套过深，内容不完整')
                    return _TRUNCATED
                if isinstance(part, dict):
                    if len(part) > 200:
                        note('报文对象字段已截断，内容不完整')
                    return {str(k): _REDACTED if _SENSITIVE.fullmatch(re.sub(r'[-_]', '', str(k)).removeprefix('X').removeprefix('x')) else mask(v, depth+1)
                            for k, v in list(part.items())[:200]}
                if isinstance(part, list):
                    if len(part) > 100:
                        note('报文列表已截断')
                    return [mask(v, depth+1) for v in part[:100]]
                return part if part is None or type(part) in (str, int, float, bool) else None
            item = json.dumps(mask(item), ensure_ascii=False)
        if not isinstance(item, str):
            note(f'{PACKET_LABELS[field]}结构不可读')
            return None
        limited, partial = _clip(item, MAX_PACKET_HEAD if field.endswith('Head') else MAX_PACKET_BODY)
        if partial:
            note(f'{PACKET_LABELS[field]}已截断')
        return charge(limited)

    def keep(item, depth=0):
        nodes[0] += 1
        if nodes[0] > 1800 or depth > 12 or remaining[0] <= 0:
            note('事实结构或总量超出预算，内容不完整')
            return None
        if isinstance(item, dict):
            if any(item.get(key) is True for key in ('partial', 'truncated', 'incomplete')):
                note('来源明确标注返回不完整')
            output = {}
            if any(key in item and item[key] is None for key in ('item', 'items', 'list', 'records')):
                note('来源实体列表为空值，未取得完整结果')
            for prior_note in item.get('_evidenceLimits', []) if isinstance(item.get('_evidenceLimits'), list) else []:
                note(_text(prior_note, 160))
            if len(item) > 2000:
                note('事实字段数量已截断')
            for key, nested in list(item.items())[:2000]:
                if key not in _FACT_KEYS or key == '_evidenceLimits':
                    continue
                if remaining[0] < len(key.encode('utf-8')) + 8:
                    note('总事实预算已用完，内容不完整')
                    break
                remaining[0] -= len(json.dumps(key).encode('utf-8')) + 2
                if key in _PACKET_ALIASES:
                    output[key] = packet_value(nested, _PACKET_ALIASES[key])
                else:
                    output[key] = keep(nested, depth+1)
            listed = [v for k, v in item.items() if k in {'item', 'items', 'list', 'records'} and isinstance(v, list)]
            if type(item.get('total')) is int and any(item['total'] > len(v) for v in listed):
                note('返回数量少于来源总数，仅取得部分记录')
            return output
        if isinstance(item, list):
            if len(item) > 20:
                note('事实列表已截断')
            return [keep(v, depth+1) for v in item[:20]]
        if isinstance(item, str):
            limited, partial = _clip(item, 2000 if depth <= 2 else MAX_TEXT)
            if partial:
                note('事实文本已截断')
            return charge(limited)
        if item is None or type(item) in (bool, int):
            return charge(item)
        if type(item) is float:
            try:
                return charge(item)
            except ValueError:
                note('事实中存在非有限数值')
        return None

    safe = keep(value)
    if not isinstance(safe, dict):
        safe = {'items': safe} if isinstance(safe, list) else {}
    if notes:
        safe['_evidenceLimits'] = notes
    # Defensive serialization bound (list punctuation is deliberately not
    # charged per element). Preserve early facts and mark the omitted tail.
    if len(json.dumps(safe, ensure_ascii=False).encode('utf-8')) > MAX_FACT_BYTES:
        note('总事实预算已用完，内容不完整')
        while len(json.dumps(safe, ensure_ascii=False).encode('utf-8')) > MAX_FACT_BYTES - 500 and safe:
            candidates = [(len(json.dumps(v, ensure_ascii=False)), k) for k, v in safe.items() if k != '_evidenceLimits']
            if not candidates:
                break
            _, key = max(candidates)
            nested = safe[key]
            if isinstance(nested, dict) and nested:
                nested.pop(next(reversed(nested)))
            elif isinstance(nested, list) and nested:
                nested.pop()
            else:
                safe.pop(key)
        safe['_evidenceLimits'] = notes
    return safe, bool(notes)


def _walk(value, alert_id='', alerts_mode=False):
    if isinstance(value, dict):
        explicit = value.get('alertId')
        identifier = value.get('uuId') or value.get('uuid') or value.get('id')
        if isinstance(explicit, str) and explicit:
            alert_id = explicit
        elif isinstance(identifier, str) and not identifier.startswith('incident-') and (
                identifier.startswith('alert-') or alerts_mode or 'proofType' in value):
            alert_id = identifier
        yield value, alert_id
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                yield from _walk(nested, alert_id, alerts_mode)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested, alert_id, alerts_mode)


def _when(value):
    """Return sortable actual event time; never use collection time as a proxy."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, (int, float)) or isinstance(value, str) and re.fullmatch(r'\d{10,13}', value):
            seconds = float(value)
            if seconds > 10**11:
                seconds /= 1000
            return datetime.fromtimestamp(seconds, timezone.utc).timestamp()
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        # Source timestamps without an offset remain literal source time. We
        # use UTC only for ordering, and never attach an invented display zone.
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _text(value, limit=800):
    if isinstance(value, list):
        value = '、'.join(str(v) for v in value)
    return _clip(value, limit)[0]


def build_story(event, evidence, result=None):
    """Build an evidence-linked story, keeping collection and event times apart."""
    event = event if isinstance(event, dict) else {}
    evidence = evidence if isinstance(evidence, list) else []
    result = result if isinstance(result, dict) else event.get('investigation', {})
    result = result if isinstance(result, dict) else {}
    basics = normalize_basics(event.get('basics', {}))
    for key, val in normalize_basics(event).items():
        basics.setdefault(key, val)
    story = {'basics': basics, 'device': _text(event.get('device_name') or event.get('device') or '来源未提供'),
             'timeline': [], 'key_evidence': [], 'packets': [], 'other_queries': [], 'gaps': [],
             'alert_details': [],
             'conclusion': _text(result.get('reason') or '尚未形成有证据支持的调查结论', 1800),
             'verdict': result.get('verdict', 'unknown'), 'boundaries': []}
    seen, timeline_seen, alerts_seen = set(), set(), set()
    is_web_event = any(term in str(event.get('name', '')).lower() for term in ('sql', 'web', 'http', '注入', '跨站', '目录遍历'))
    packet_groups = {}

    def gap(text):
        text = _text(text, 600)
        if text and text not in story['gaps'] and len(story['gaps']) < 24:
            story['gaps'].append(text)

    def key_fact(ref, text, priority=False):
        marker = (ref, text)
        if marker not in seen and (priority or len(story['key_evidence']) < 30):
            seen.add(marker)
            item = {'evidence_id': ref, 'text': _text(text, 1600)}
            if priority:
                story['key_evidence'].insert(0, item)
                del story['key_evidence'][30:]
            else:
                story['key_evidence'].append(item)

    def timeline(time_value, text, ref):
        marker = (str(time_value), text, ref)
        if _when(time_value) is not None and marker not in timeline_seen and len(story['timeline']) < 40:
            timeline_seen.add(marker)
            story['timeline'].append({'time': time_value, 'text': _text(text), 'evidence_id': ref})

    if basics.get('start_time') is not None:
        timeline(basics['start_time'], 'XDR 记录事件首次发生', '事件基础信息')
    if basics.get('end_time') is not None and basics.get('end_time') != basics.get('start_time'):
        timeline(basics['end_time'], 'XDR 记录事件最近发生', '事件基础信息')
    for proof in evidence[:24]:
        if not isinstance(proof, dict):
            continue
        ref = _text(proof.get('id') or '未编号证据', 100)
        source = _text(proof.get('source_name') or proof.get('device_name') or proof.get('device') or proof.get('tool') or '来源未提供', 160)
        kind = _text(proof.get('kind') or '关联', 50)
        if not proof.get('success'):
            gap(f'{ref} · {source}：查询未完成；{proof.get("error") or "未取得可用结果"}，不能按无风险处理')
            continue
        facts, clipped = safe_facts(proof.get('facts', {}))
        if proof.get('partial') or clipped:
            gap(f'{ref} · {source}：仅取得部分事实，不能据此排除风险')
        prior_notes = proof.get('facts', {}).get('_evidenceLimits', []) if isinstance(proof.get('facts'), dict) else []
        for note in [*prior_notes, *facts.get('_evidenceLimits', [])]:
            gap(f'{ref}：{note}')
        before = len(story['key_evidence'])
        for row, alert_id in _walk(facts, alerts_mode=kind in {'alerts', 'alert', 'alert_proof', 'alert-proof'}):
            record = normalize_basics(row)
            if alert_id:
                record.pop('event_id', None)
                record.setdefault('alert_ids', [alert_id])
            group_key = (ref, alert_id or '告警编号未提供')
            has_packet_fields = any(alias in row for aliases in PACKET_FIELDS.values() for alias in aliases)
            if alert_id and any(key in row for key in ('alertId', 'uuId', 'uuid')) or has_packet_fields:
                packet_groups.setdefault(group_key, {'fields': set(), 'name': record.get('name', ''),
                    'web': is_web_event or has_packet_fields or 'http' in str(row.get('proofType', '')).lower()})
            # Source identity is only descriptive here. Correlation eligibility
            # remains the investigator's responsibility, never inferred by IP.
            alert_marker = (ref, alert_id, str(record.get('alert_time')), str(record.get('name')))
            if (any(key in row for key in ('alertId', 'alertTime', 'firstTime', 'lastTime')) or alert_id and 'name' in row) and alert_marker not in alerts_seen and len(story['alert_details']) < 20:
                alerts_seen.add(alert_marker)
                story['alert_details'].append({'evidence_id': ref, **record})
            details = [f'{FIELD_LABELS[key]}：{_display_field(key, record[key], record)}' for key in
                       ('alert_ids', 'host', 'data_source', 'source_device', 'virus_name', 'file_id', 'file_md5', 'file_hash', 'file_name', 'file_path', 'file_size',
                        'file_created_at', 'process_name', 'command_line', 'url', 'domain', 'src_ip', 'src_port',
                        'dst_ip', 'dst_port', 'attack_state', 'attack_result', 'threat_subtype', 'stage') if key in record]
            if details:
                key_fact(ref, source + '；' + '；'.join(details))
            if record.get('file_created_at') is not None:
                timeline(record['file_created_at'], f'{source}：来源记录文件创建，文件 {_text(record.get("file_path") or record.get("file_name") or "标识未提供", 300)}（不等于告警发生时间）', ref)
            if record.get('alert_time') is not None:
                description = record.get('name') or row.get('description') or row.get('title')
                if description:
                    timeline(record['alert_time'], f'{source}：{description}', ref)
            for canonical, aliases in PACKET_FIELDS.items():
                supplied = next((key for key in aliases if key in row), None)
                if not supplied:
                    continue
                content = row[supplied]
                if not isinstance(content, str) or not content and canonical.endswith('Head'):
                    gap(f'{ref}：{PACKET_LABELS[canonical]}未取得，不能据此判定误报或攻击成功')
                    continue
                if group_key in packet_groups:
                    packet_groups[group_key]['fields'].add(canonical)
                if len(story['packets']) < 24:
                    story['packets'].append({'evidence_id': ref, 'source': source, 'field': canonical,
                                             'label': PACKET_LABELS[canonical], 'text': content or '（正文为空，来源明确返回）', 'alert_id': alert_id,
                                             'truncated': _TRUNCATED in content})
                if canonical.startswith('request'):
                    decoded = unquote(content)
                    matches = list(_SQL.finditer(decoded))[:3]
                    for match in matches:
                        snippet = decoded[max(0, match.start()-80):min(len(decoded), match.end()+150)]
                        key_fact(ref, '请求中存在需核对的 SQL 可疑片段：' + snippet, priority=True)
                    if matches and 'SQL 可疑片段只证明请求含相关语句，不能单独证明利用成功或误报。' not in story['boundaries']:
                        story['boundaries'].append('SQL 可疑片段只证明请求含相关语句，不能单独证明利用成功或误报。')
                if canonical == 'responseHead':
                    status = re.search(r'HTTP/\d(?:\.\d)?\s+(\d{3})\b', content)
                    if status:
                        key_fact(ref, f'响应 HTTP {status[1]}；状态码本身不证明攻击成功或安全。')
            # Keep source threat/status facts without turning raw values into a
            # new decision, and omit repetitive empty entity counts from mail.
            statuses = [f'{key}={_text(row[key], 240)}' for key in
                        ('gptResult', 'threatLevel', 'edrDealStatusInfo', 'ndrDealStatusInfo')
                        if row.get(key) is not None and row[key] != '']
            if statuses:
                key_fact(ref, source + '；' + '；'.join(statuses))
        if len(story['key_evidence']) == before:
            story['other_queries'].append(f'{ref} · {source}（{kind}）：已查，未取得值得展开的新增事实；不据此排除风险。')
    for (ref, alert_id), group in packet_groups.items():
        if not group['web']:
            continue
        if not group['fields'].intersection({'requestHead', 'requestBody'}):
            gap(f'{ref} · 告警 {alert_id}：未取得请求包，不能确认请求是否实际包含攻击语句')
        if not group['fields'].intersection({'responseHead', 'responseBody'}):
            gap(f'{ref} · 告警 {alert_id}：未取得响应包，不能核对服务端响应及利用效果')
    story['timeline'].sort(key=lambda entry: _when(entry['time']))
    if not story['timeline']:
        gap('来源未提供可用的事件发生时间；查询取得时间未代替事件时间，暂无法还原完整时间线')
    is_web = bool(story['packets']) or any(term in str(event.get('name', '')).lower() for term in ('sql', 'web', 'http', '注入', '跨站', '目录遍历'))
    if is_web:
        fields = {packet['field'] for packet in story['packets']}
        if not fields.intersection({'requestHead', 'requestBody'}):
            gap('未取得请求包，不能确认请求是否实际包含攻击语句')
        if not fields.intersection({'responseHead', 'responseBody'}):
            gap('未取得响应包，不能核对服务端响应及利用效果')
        request_packets = [packet for packet in story['packets'] if packet['field'].startswith('request')]
        if request_packets and not any(_SQL.search(unquote(packet['text'])) for packet in request_packets):
            story['boundaries'].append('已检查现有请求片段，未命中本地 SQL 可疑模式；该模式检查不覆盖所有攻击，也不能单独据此认定误报。')
        story['boundaries'].append('没有发现匹配片段不等于误报；HTTP 200、XDR attackState 与 attackResult 各自含义不同，利用结果须结合原始证据判断。')
    for item in result.get('gaps', [])[:12] if isinstance(result.get('gaps'), list) else []:
        gap(item)
    refs = {str(p.get('id')) for p in evidence if isinstance(p, dict) and p.get('success')}
    requested = result.get('evidence_ids', [])
    if isinstance(requested, list) and any(str(ref) not in refs for ref in requested):
        gap('调查结论引用了未取得成功结果的证据；该结论需继续核对')
    story['evidence_ids'] = [str(ref) for ref in requested if str(ref) in refs] if isinstance(requested, list) else []
    return story


def _time_text(value):
    if type(value) in (int, float) or isinstance(value, str) and re.fullmatch(r'\d{10,13}', value):
        timestamp = _when(value)
        if timestamp is not None:
            return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace('+00:00', 'Z')
    return _text(value)


def _severity_text(basics):
    value = basics.get('severity')
    labels = ({-1: '未分级', 1: '低危', 2: '中危', 3: '高危', 4: '严重'}
              if basics.get('severity_field') == 'incidentSeverity' else
              {0: '严重', 1: '高危', 2: '中危', 3: '低危', 4: '信息'}
              if basics.get('severity_field') == 'riskLevel' else {})
    return f'{labels[value]}（来源原值 {value}）' if type(value) is int and value in labels else _text(value)


def _display_field(key, value, basics):
    if key == 'severity':
        return _severity_text(basics)
    if key in {'alert_time', 'start_time', 'end_time', 'file_created_at'}:
        return _time_text(value)
    if key in {'attack_state', 'attack_result'}:
        labels = {0: '尝试', 1: '失败', 2: '成功', 3: '失陷'} if key == 'attack_state' else {0: '尝试', 1: '成功', 2: '失败', 3: '失陷'}
        return f'{labels[value]}（来源原值 {value}；XDR 标签，不等于独立确认）' if type(value) is int and value in labels else _text(value)
    return _text(value)


def story_sections(story):
    """Existing chat sections consume these strings as plain text."""
    basics = story.get('basics', {})
    base = [f'监测设备：{story.get("device", "来源未提供")}']
    base += [f'{FIELD_LABELS.get(key, key)}：{_display_field(key, value, basics)}'
             for key, value in basics.items() if key != 'severity_field' and value not in ('', [])]
    base += [f'{FIELD_LABELS[key]}：来源未提供' for key in
             ('event_id', 'alert_ids', 'name', 'alert_time', 'severity', 'host', 'data_source', 'source_device')
             if basics.get(key) is None or basics.get(key) in ('', [])]
    for alert in story.get('alert_details', []):
        fields = [f'{FIELD_LABELS[key]}：{_display_field(key, alert[key], alert)}'
                  for key in ('alert_ids', 'name', 'alert_time', 'severity', 'host', 'data_source', 'source_device') if key in alert]
        if fields:
            base.append('关联告警 [' + alert['evidence_id'] + ']：' + '；'.join(fields))
    if basics.get('severity_field'):
        base.append(f'等级字段：{basics["severity_field"]}（按来源口径，不混用枚举）')
    timeline = '\n'.join(f'{_time_text(entry["time"])} · {entry["text"]} [{entry["evidence_id"]}]' for entry in story.get('timeline', []))
    evidence = '\n'.join(f'[{entry["evidence_id"]}] {entry["text"]}' for entry in story.get('key_evidence', []))
    sections = [{'label': '事件基本信息', 'text': '\n'.join(base)},
                {'label': '事件时间线', 'text': timeline or '未取得可还原事件经过的发生时间，未用查询时间替代。'},
                {'label': '调查结论', 'text': story.get('conclusion') or '尚未形成调查结论'},
                {'label': '关键证据', 'text': evidence or '尚未取得可展开的关键证据，不能据此判为误报。'}]
    if story.get('other_queries'):
        sections.append({'label': '其他联动查询', 'text': '\n'.join(story['other_queries'][:12])})
    if story.get('gaps') or story.get('boundaries'):
        sections.append({'label': '证据与缺口', 'text': '\n'.join(story.get('gaps', []) + story.get('boundaries', []))})
    return sections


def story_text(story, mail=False):
    """Readable evidence summary; mail omits routine empty query chatter."""
    sections = story_sections(story)
    if mail:
        sections = [section for section in sections if section['label'] != '其他联动查询']
        # Keep the conclusion and packet evidence ahead of long histories so a
        # bounded mail cannot lose the facts that justify contacting an owner.
        order = {'事件基本信息': 0, '调查结论': 1, '关键证据': 2, '证据与缺口': 3, '事件时间线': 4}
        sections.sort(key=lambda section: order.get(section['label'], 5))
        sections = [{**section, 'text': _clip(section['text'], 3000 if section['label'] in {'事件基本信息', '事件时间线'} else 4500)[0]}
                    for section in sections]
    parts = [section['label'] + '\n' + section['text'] for section in sections]
    if not mail and story.get('packets'):
        parts.append('请求与响应（已脱敏；按纯文本显示）\n' + '\n\n'.join(
            f'[{packet["evidence_id"]}] {packet["label"]} · {packet["source"]}\n{packet["text"]}' for packet in story['packets']))
    return _clip('\n\n'.join(parts), 12_000 if mail else 48_000)[0]


def _readable_text(value, limit=900):
    """Remove machine bookkeeping from a business excerpt, retaining facts."""
    text = _text(value, 4000)
    text = re.sub(r'\[?evidence-\d+\]?[：:·\s]*', '', text, flags=re.I)
    text = re.sub(r'\bincident-[a-z\d-]{12,}\b', '本事件', text, flags=re.I)
    text = re.sub(r'告警编号[：:]\s*(?:alert-[a-z\d-]+[、，,;；\s]*)+', '', text, flags=re.I)
    text = re.sub(r'\balert-[a-z\d-]{12,}\b', '关联告警', text, flags=re.I)
    text = re.sub(r'\b(?:gptResult|attackState|attack_result|attackResult|incidentSeverity)\s*[=:]\s*[-\d]+', '', text)
    text = re.sub(r'[；;]\s*[；;]+', '；', text)
    text = re.sub(r'^[\s；;：:·]+|[\s；;：:·]+$', '', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text[:limit]


def readable_story(story):
    """Business overview beside expandable, unchanged source evidence.

    This is deterministic presentation, not a second model or a new verdict.
    Original stories, proof references and raw packets remain available for
    audit; the concise view avoids repeating operational bookkeeping.
    """
    basics = story.get('basics') if isinstance(story.get('basics'), dict) else {}
    selected = {key: basics[key] for key in ('event_id', 'name', 'host', 'start_time', 'end_time',
                'data_source', 'source_device') if basics.get(key) not in (None, '', [])}
    selected['device'] = story.get('device', '')
    if basics.get('severity') is not None:
        selected['severity'] = _severity_text(basics)
    timeline, evidence, seen = [], [], set()
    for entry in story.get('timeline', []):
        if not isinstance(entry, dict):
            continue
        text = _readable_text(entry.get('text'), 600)
        marker = (str(entry.get('time')), text)
        if text and marker not in seen:
            seen.add(marker)
            timeline.append({'time': entry.get('time'), 'text': text})
    for record in story.get('alert_details', []):
        if not isinstance(record, dict):
            continue
        source = record.get('src_ip') or record.get('host')
        destination = record.get('dst_ip')
        name = _readable_text(record.get('name') or '关联行为', 160)
        endpoint = str(destination) if destination else ''
        if endpoint and record.get('dst_port') is not None:
            endpoint += ':' + str(record['dst_port'])
        detail = f'{source} 向 {endpoint} 发起访问；{name}' if source and endpoint else name
        for key, phase in (('start_time', '来源记录该行为首次出现'), ('end_time', '来源记录该行为最近出现')):
            when = record.get(key)
            text = phase + '：' + detail
            marker = (str(when), text)
            if _when(when) is not None and marker not in seen:
                seen.add(marker)
                timeline.append({'time': when, 'text': text})
    timeline.sort(key=lambda entry: _when(entry['time']) or 0)
    seen.clear()
    for entry in story.get('key_evidence', []):
        if not isinstance(entry, dict):
            continue
        text = _readable_text(entry.get('text'), 900)
        if text and text not in seen:
            seen.add(text)
            evidence.append({'text': text})
        if len(evidence) >= 6:
            break
    gaps = []
    for item in story.get('gaps', []):
        text = _readable_text(item, 300)
        # Identity checks and missing behaviour/packets are materially useful;
        # routine source diagnostics stay in the detailed audit sections.
        if text and text not in gaps:
            gaps.append(text)
        if len(gaps) >= 3:
            break
    return {'basics': selected, 'conclusion': _readable_text(story.get('conclusion'), 1200),
            'verdict': story.get('verdict', 'unknown'), 'evidence': evidence,
            'timeline': timeline, 'gaps': gaps}

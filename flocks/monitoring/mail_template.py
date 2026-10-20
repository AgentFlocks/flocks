"""Operator-facing incident notifications, rendered from already verified facts.

The renderer does not make a security decision. Callers must first establish an
eligible, current investigation. All dynamic HTML is escaped; no remote HTML,
raw packets, evidence identifiers or model-provided markup are rendered.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
import re
from zoneinfo import ZoneInfo

from .event_details import FIELD_LABELS, normalize_basics, redact_text

TEMPLATE_VERSION = 2
_IDS = re.compile(r'(?<![A-Za-z0-9_-])(?:(?:incident|alert)-[A-Za-z0-9-]+|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?![A-Za-z0-9_-])', re.I)
_REFS = re.compile(r'\[?(?<![A-Za-z0-9_-])(?:evidence|cap)[-_]\d+(?![A-Za-z0-9_-])\]?', re.I)
_RAW_ENUM = re.compile(r'(?<![A-Za-z0-9_])(?:gptResult|attack_result|attackResult|attackState|attack_state|incidentSeverity|threatLevel|firstTime|lastTime)\s*(?:[=:：]|为)\s*[\w./-]+(?:[（(][^）)]*[）)])?', re.I)
_INTERNAL = re.compile(r'查询失败|查询未完成|item\s*返回|authserv|预算|剩余调用|未绑定|模型|evidence|traceback|json|SQL 可疑片段只证明|状态码本身不证明', re.I)


@dataclass(frozen=True)
class Notification:
    subject: str
    text: str
    html: str


def _text(value, limit=500):
    if isinstance(value, list):
        value = '、'.join(str(v) for v in value[:6])
    return redact_text(str(value or ''))[:limit].strip()


def readable_text(value, limit=500):
    """Remove transport/debug identifiers while preserving operational facts."""
    text = _text(value, 4000)
    text = _REFS.sub('', text)
    text = re.sub(r'(?:告警编号|事件编号|设备编号|通知编号)\s*[：:]\s*[^；;。\n]+[；;]?', '', text)
    text = _IDS.sub('', text)
    text = re.sub(r'^事件\s*[：:]\s*', '', text)
    text = _RAW_ENUM.sub('', text)
    text = re.sub(r'[（(](?:来源原值|来源阶段值|XDR 标签)[^）)]*[）)]', '', text)
    text = re.sub(r'\b(?:WAIT_DEAL|BLOCK_FAILED|BLOCK_SUCCESS|UNBLOCK_SUCCESS|DEAL_SUCCESS)\b',
                  lambda m: {'WAIT_DEAL': '待处置', 'BLOCK_FAILED': '阻断未成功', 'BLOCK_SUCCESS': '已阻断',
                             'UNBLOCK_SUCCESS': '已解除阻断', 'DEAL_SUCCESS': '已完成处置'}[m[0]], text)
    text = re.sub(r'[（(]\s*[）)]', '', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'(?:[；;,，]\s*){2,}', '；', text)
    text = re.sub(r'\s*([，。；：])\s*', r'\1', text).strip(' ；;，,:：。[]')
    return text[:limit].rstrip('；;，,：: ')


def display_time(value, timezone_name='Asia/Shanghai'):
    """Localize offset-aware times; leave offset-free source times literal."""
    if value is None or isinstance(value, bool):
        return ''
    try:
        if type(value) in (int, float) or isinstance(value, str) and re.fullmatch(r'\d{10,13}', value):
            seconds = float(value)
            date = datetime.fromtimestamp(seconds / 1000 if seconds > 10**11 else seconds, timezone.utc)
        else:
            date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if date.tzinfo is None:
            return date.strftime('%Y-%m-%d %H:%M:%S') + '（来源时间）'
        local = date.astimezone(ZoneInfo(timezone_name))
        zone = '北京时间' if timezone_name == 'Asia/Shanghai' else local.strftime('%z')
        return local.strftime('%Y-%m-%d %H:%M:%S') + f'（{zone}）'
    except (ValueError, TypeError, OverflowError, OSError):
        return ''


def _severity(basics):
    value, source = basics.get('severity'), basics.get('severity_field')
    labels = ({-1: '未分级', 1: '低危', 2: '中危', 3: '高危', 4: '严重'} if source == 'incidentSeverity' else
              {0: '严重', 1: '高危', 2: '中危', 3: '低危', 4: '信息'} if source == 'riskLevel' else {})
    if type(value) is int:
        return labels.get(value, '未提供')
    text = _text(value, 30)
    return text if text in {'严重', '高危', '中危', '低危', '信息', '未分级'} else '未提供'


def _sentence(value, limit=180, *, max_sentences=1):
    """Accept an operator sentence only when it contains no execution metadata."""
    raw = str(value or '')
    # First/last observations do not establish uninterrupted activity. Keep
    # the actual dates in the source timeline rather than guessed durations.
    duration_claim = re.search(r'(?:持续|连续|长达|已有|近|约)\s*(?:约\s*)?[一二三四五六七八九十百零两\d.]+\s*(?:个?月|年|天|周|小时|分钟)', raw)
    if (duration_claim or _REFS.search(raw) or _IDS.search(raw) or _RAW_ENUM.search(raw)
            or re.search(r'gptResult|attack_result|attackResult|attackState|incidentSeverity|threatLevel|firstTime|lastTime', raw, re.I)
            or re.search(r'(?<![A-Za-z0-9_])\d{10,13}(?![A-Za-z0-9_])', raw)):
        return ''
    value = readable_text(value, 2000)
    if value in {'None', 'null'}:
        return ''
    if not value or _INTERNAL.search(value) or len(re.findall(r'[。！？]', value)) > max_sentences:
        return ''
    # Never publish a clipped sentence with a missing condition or negation.
    # Long audit reasons remain in the incident details instead.
    if len(value) > limit:
        return ''
    return value.rstrip('。；; ') + '。'


def _conclusion(event, story, facts):
    """Lead with a decision, then separate its concrete supporting observations.

    The caller has already enforced confirmed-risk eligibility. The operator
    fields are generated during that same investigation, not by a new model
    call. Historical results fall back to structured observations, never to a
    truncated wall of technical reasoning.
    """
    summary = _sentence(story.get('operator_summary'), 120)
    if not summary:
        host = _text(event.get('host') or story.get('basics', {}).get('host'), 100)
        summary = f'已确认主机 {host} 存在需要处置的安全风险。' if host else '本事件已确认存在安全风险，需要责任人处置。'
    explanation = []
    for value in story.get('operator_explanation', [])[:3] if isinstance(story.get('operator_explanation'), list) else []:
        sentence = _sentence(value)
        if sentence and sentence != summary and sentence not in explanation:
            explanation.append(sentence)
    historic = _sentence(story.get('conclusion'), 180)
    if not explanation and historic and historic != summary:
        explanation.append(historic)
    if len(explanation) < 2:
        for fact in facts:
            sentence = _sentence(fact.get('narrative') or fact.get('summary'), 240, max_sentences=2)
            if sentence and sentence != summary and sentence not in explanation:
                explanation.append(sentence)
            if len(explanation) == 2 or sentence.count('。') == 2:
                break
    return summary, explanation[:3]


def _timeline(story, timezone_name):
    cards = story.get('evidence_cards')
    verified_refs = set() if isinstance(cards, list) else None
    for card in cards if isinstance(cards, list) else []:
        if not isinstance(card, dict):
            continue
        sources = [card, *(card['records'] if isinstance(card.get('records'), list) else [])]
        for source in sources:
            reference = source.get('evidence_id') if isinstance(source, dict) else None
            if isinstance(reference, str) and reference:
                verified_refs.add(reference)
    entries = [item for item in story.get('timeline', [])[:40] if isinstance(item, dict)
               and (verified_refs is None or item.get('evidence_id') in verified_refs or item.get('evidence_id') == '事件基础信息')]
    # Alert first/last activity and concrete endpoints make the chronology
    # useful to an operator. They are source facts, never collection timestamps.
    for item in story.get('alert_details', [])[:20]:
        if not isinstance(item, dict) or verified_refs is not None and item.get('evidence_id') not in verified_refs:
            continue
        name = readable_text(item.get('name'), 100)
        if not name:
            continue
        activity = name
        if item.get('src_ip') and item.get('dst_ip'):
            source, destination = _text(item['src_ip'], 80), _text(item['dst_ip'], 80)
            port = _text(item.get('dst_port'), 50)
            dns = bool(re.fullmatch(r'53(?:[、,; ]53)*', port))
            if dns:
                activity = f'主机 {source} 向 DNS 服务器 {destination} 发起域名解析请求'
                if item.get('domain'):
                    domains = item['domain'] if isinstance(item['domain'], list) else re.split(r'[、，,;；\s]+', str(item['domain']))
                    activity += '，涉及 ' + '、'.join(_text(value, 100) for value in domains[:3])
                    if len(domains) > 3:
                        activity += ' 等域名'
                activity += '；来源记录行为「' + name + '」'
            else:
                activity += f'；主机 {source} → {destination}'
                if port:
                    activity += '，目的端口 ' + port
        for field, label in (('start_time', '首次记录'), ('end_time', '最近记录'), ('alert_time', '记录')):
            if item.get(field) is not None:
                entries.append({'time': item[field], 'text': label + '「' + activity + '」'})
    result, seen = [], set()
    for item in entries:
        time = display_time(item.get('time'), timezone_name)
        text = readable_text(item.get('text'), 240)
        device = str(story.get('device') or '')
        if device and text.startswith(device + '：'):
            text = text[len(device) + 1:]
        if time and text and (time, text) not in seen:
            seen.add((time, text))
            result.append((time, text))
    # Display times share the selected zone; offset-free times remain labeled.
    result.sort(key=lambda item: item[0])
    # Prefer the concrete activity over a duplicate, terse alert-name entry.
    result = [(time, text) for time, text in result if not any(
        time == other_time and text != other_text and '「' in other_text and text in other_text
        for other_time, other_text in result)]
    concrete = [(time, text) for time, text in result if text.startswith(('首次记录「', '最近记录「', '记录「'))]
    result = [(time, text) for time, text in result if not any(
        time == other_time and text != other_text and (text.startswith('XDR 记录事件')
        or '：' in text and text.rsplit('：', 1)[-1] in other_text)
        for other_time, other_text in concrete)]
    return result if len(result) <= 12 else [*result[:6], *result[-6:]]


def _legacy_evidence(story, timezone_name):
    """Project structured recorded activity, avoiding a dump of raw fields."""
    result, seen = [], set()
    for item in story.get('alert_details', [])[:20]:
        if not isinstance(item, dict):
            continue
        parts = []
        source = readable_text(item.get('data_source'), 50)
        if item.get('src_ip') and item.get('dst_ip'):
            dest = _text(item['dst_ip'], 100)
            if item.get('dst_port'):
                dest += '，目的端口 ' + _text(item['dst_port'], 60)
            parts.append(f"主机 {_text(item['src_ip'], 100)} 与 {dest} 通信")
        elif item.get('host'):
            parts.append('涉及主机 ' + _text(item['host'], 100))
        for key, title in (('domain', '涉及域名'), ('url', '请求地址'), ('file_path', '文件位置'),
                           ('file_name', '文件名称'), ('virus_name', '检出威胁'), ('process_name', '涉及进程')):
            if item.get(key):
                parts.append(title + '：' + readable_text(item[key], 180))
        name = readable_text(item.get('name'), 100)
        if name:
            parts.append('记录行为：' + name)
        if not parts:
            continue
        text = '；'.join(parts)
        stamp = display_time(item.get('alert_time') or item.get('start_time'), timezone_name)
        marker = text
        if marker not in seen:
            seen.add(marker)
            result.append({'time': stamp or '发生时间不详（来源未提供）', 'text': text[:450], 'meaning': f'{source} 记录提供上述风险判断的行为依据。' if source else '该记录提供上述风险判断的行为依据。'})
    # Some sources return entity facts without an alert-level record. Interpret
    # those selected fields as observations rather than printing a field dump.
    # Split only at known field labels: a command may itself contain semicolons.
    labels = ('病毒名称', '文件名称', '文件路径', '文件 MD5', '文件哈希', '文件创建时间',
              '进程名称', '命令行', '影响域名', '源 IP', '目的 IP', '目的端口', 'URL',
              '来源发生时间', '告警时间', '首次发生时间', '最近发生时间')
    # Ignored metadata still delimits a preceding meaningful value.
    labels = tuple(dict.fromkeys((*labels, *FIELD_LABELS.values(), 'gptResult', 'threatLevel',
                                 'edrDealStatusInfo', 'ndrDealStatusInfo')))
    label_pattern = re.compile(r'(?:^|[；;])\s*(' + '|'.join(re.escape(label) for label in labels) + r')\s*[：:=]')

    def literal(value, limit=320):
        # Commands, URLs and paths are operational evidence; preserve their
        # syntax and apply credential redaction before escaping HTML later.
        value = redact_text(str(value or '')).strip()
        return value if len(value) <= limit else value[:limit] + '…（节选）'

    def append(text, meaning, time):
        if text and text not in seen and not any(text in fact['text'] for fact in result):
            seen.add(text)
            result.append({'time': time or '发生时间不详（来源未提供）', 'text': text, 'meaning': meaning})

    for item in story.get('key_evidence', [])[:30]:
        text = str(item.get('text') or '') if isinstance(item, dict) else ''
        if not text:
            continue
        matches = list(label_pattern.finditer(text))
        fields = {match[1]: literal(text[match.end():matches[index+1].start() if index+1 < len(matches) else len(text)], 500)
                  for index, match in enumerate(matches)}
        # Only a timestamp explicitly on this evidence item/record can date it.
        # Event start/end and retrieval timestamps are deliberately not used.
        actual_time = item.get('time') if item.get('time') is not None else item.get('event_time')
        stamp = display_time(actual_time, timezone_name)
        for field in ('来源发生时间', '告警时间', '首次发生时间', '最近发生时间'):
            if not stamp and fields.get(field):
                stamp = display_time(fields[field], timezone_name)
        file_name, file_path, virus = (fields.get(key, '') for key in ('文件名称', '文件路径', '病毒名称'))
        if file_name or file_path or virus:
            observation = '来源记录文件「' + (file_name or file_path) + '」' if file_name or file_path else '来源记录文件威胁'
            if file_path and file_name and file_path != file_name:
                observation += '，位置为「' + file_path + '」'
            if virus:
                observation += '；来源将其识别为「' + virus + '」'
            file_stamp = stamp
            if fields.get('文件创建时间'):
                created = display_time(fields['文件创建时间'], timezone_name)
                if created:
                    file_stamp = '文件创建于 ' + created
            meaning = '文件名和路径可用于定位待核查样本。' if file_name or file_path else ''
            meaning += '来源的威胁识别结果提供文件侧判断依据。' if virus else '该文件记录帮助核查相关文件及其来源。'
            append(observation, meaning, file_stamp)
        process, command = fields.get('进程名称', ''), fields.get('命令行', '')
        if process or command:
            observation = '来源记录进程「' + process + '」' if process else '来源记录程序执行行为'
            if command:
                observation += '；实际命令行为「' + command + '」'
            append(observation, '进程名和命令行可用于定位运行程序、核查启动方式，并在主机日志中追溯其执行活动。', stamp)
        src, dest, port, domain = (fields.get(key, '') for key in ('源 IP', '目的 IP', '目的端口', '影响域名'))
        if src and dest:
            observation = f'来源记录主机 {src} 向 {dest} 发起通信'
            if port:
                observation += '，目的端口 ' + port
            if domain:
                observation += '；关联域名为「' + domain + '」'
            append(observation, '通信两端和端口帮助定位连接、检索网络日志，核对该事件的外联活动。', stamp)
        elif domain:
            append('来源记录关联域名「' + domain + '」', '该域名可用于检索 DNS 记录并核对相关访问；关联记录本身不表示已成功连接。', stamp)
        snippet = re.search(r'SQL 可疑片段[：:]\s*(.+)', text, re.S)
        if snippet:
            append('请求中出现片段「' + literal(snippet[1], 320) + '」',
                   '该请求内容支持核对注入尝试，并可据此定位对应访问日志；不单独据此认定利用成功。', stamp)
        elif fields.get('URL'):
            append('来源记录请求地址「' + fields['URL'] + '」', '该请求地址帮助定位受影响接口并检索对应访问记录。', stamp)
        response = re.search(r'响应 HTTP (\d{3})\b', text)
        if response:
            append('服务器响应 HTTP ' + response[1], '该事实用于核对服务端响应；状态码本身不代表攻击利用成功。', stamp)
    return result[:6]


def _mail_observations(cards):
    """Project a combined alert card into the mail's existing fact categories.

    These are temporary views over verified fields. The incident story keeps
    one card per alert, and private raw provenance never becomes mail content.
    """
    for card in cards[:30]:
        if not isinstance(card, dict):
            continue
        if card.get('kind') not in {'network', 'domain', 'file', 'process', 'request'}:
            yield card
            continue
        fields = {field.get('key'): field.get('value')
                  for field in card.get('fields', []) if isinstance(field, dict)} if isinstance(card.get('fields'), list) else {}
        kinds = []
        if fields.get('src_ip') and fields.get('dst_ip'):
            kinds.append('network')
        elif fields.get('domain'):
            kinds.append('domain')
        if fields.get('file_name') or fields.get('file_path') or fields.get('virus_name'):
            kinds.append('file')
        if fields.get('process_name') or fields.get('command_line'):
            kinds.append('process')
        if fields.get('url') or card.get('packets') or card.get('kind') == 'request':
            kinds.append('request')
        for kind in kinds or [card.get('kind')]:
            yield {**card, 'kind': kind}


def _evidence(story, timezone_name):
    """One observation per line, with a short meaning followed by source facts."""
    cards = story.get('evidence_cards')
    result, seen = [], set()
    if isinstance(cards, list):
        # These cards come from the same bounded, sanitized query results as
        # the incident details. Do not re-interpret private raw source fields.
        allowed = {'影响主机', '主机', '源 IP', '目的 IP', '目的端口', '影响域名', '域名',
                   '请求地址', 'URL', '文件名称', '文件路径', '病毒名称', '进程名称', '命令行',
                   '来源发生时间', '告警时间', '首次发生时间', '最近发生时间', '威胁子类型', '文件创建时间',
                   '来源情报标签', '来源威胁定义', '请求方法'}
        for card in _mail_observations(cards):
            summary = readable_text(card.get('summary'), 300)
            if not summary:
                continue
            details = []
            fields = card.get('fields', []) if isinstance(card.get('fields'), list) else []
            by_kind = {
                'network': {'src_ip', 'dst_ip', 'dst_port', 'domain'},
                'domain': {'domain', 'intelligenceTag', 'threatDefineName'},
                'file': {'file_name', 'file_path', 'virus_name', 'file_created_at'},
                'process': {'process_name', 'command_line'},
                'request': {'url', 'method'},
            }
            selected = by_kind.get(card.get('kind'))
            priority = {'domain': 0, 'virus_name': 1, 'process_name': 2, 'command_line': 3, 'file_path': 4, 'file_name': 5, 'dst_ip': 6, 'dst_port': 7, 'src_ip': 8}
            fields = sorted((field for field in fields if isinstance(field, dict)), key=lambda field: priority.get(field.get('key'), 20))
            for field in fields:
                if field.get('label') not in allowed or selected is not None and field.get('key') not in selected:
                    continue
                title = field['label']
                value = field.get('value')
                if value is None or value == '' or value == []:
                    continue
                if title in {'来源发生时间', '告警时间', '首次发生时间', '最近发生时间'}:
                    continue  # the card's explicit source time is displayed once
                if isinstance(value, list):
                    value = '、'.join(str(item) for item in value[:3]) + ('等' if len(value) > 3 else '')
                elif title in {'影响域名', '域名'}:
                    values = re.split(r'[、，,;；\s]+', str(value))
                    value = '、'.join(values[:3]) + ('等' if len(values) > 3 else '')
                if field.get('key') == 'file_created_at':
                    value = display_time(value, timezone_name)
                    if value:
                        details.append('文件创建于 ' + value)
                    continue
                if title == '命令行':
                    value = redact_text(str(value))[:180]
                else:
                    value = readable_text(value, 180)
                if value:
                    details.append(title + '：' + value)
            stamp = display_time(card.get('time'), timezone_name)
            source = readable_text(card.get('source'), 70)
            kind = card.get('kind')
            values = {field.get('key'): field.get('value') for field in fields}
            port = values.get('dst_port')
            ports = port if isinstance(port, list) else [port]
            dns = bool(ports) and all(str(value) == '53' for value in ports)
            headlines = {
                'network': '记录到主机的DNS解析请求，提供相关域名访问依据。' if dns else '记录到主机对目标地址的访问，提供外联行为依据。',
                'domain': '提供关联域名及其情报，供核对该域名的威胁性质。',
                'file': '定位到相关文件，提供检出样本及存放位置依据。',
                'process': '记录到相关程序的执行，提供进程及启动方式依据。',
                'request': '请求记录中出现SQL注入特征，支持攻击尝试判断。' if 'SQL' in summary else '提供请求与响应记录，供核对实际访问内容。',
            }
            headline = source + headlines[kind] if kind in headlines else summary
            tag = readable_text(values.get('intelligenceTag') or values.get('threatDefineName'), 45)
            if tag and kind == 'network':
                activity = 'DNS查询' if dns else '网络访问'
                headline = f'{source}记录到涉及「{tag}」的{activity}，支持相关风险判断。'
            elif tag and kind == 'domain':
                headline = f'{source}将关联域名标记为「{tag}」，提供域名风险线索。'
            detail = '；'.join(details[:4])
            if source and not summary.startswith(source):
                detail = '来源：' + source + ('；' + detail if detail else '')
            marker = (headline, detail, stamp)
            if marker in seen:
                continue
            result.append({'time': stamp or '发生时间不详（来源未提供）',
                           'summary': headline.rstrip('。') + '。', 'narrative': summary, 'detail': detail})
            seen.add(marker)
            if len(result) == 6:
                break
        return result
    # Compatibility with saved investigations created before evidence cards.
    # Their raw facts stay in the detail page; the mail retains only selected
    # observations and their qualified meaning.
    for fact in _legacy_evidence(story, timezone_name):
        meaning = fact.get('meaning', '').strip()
        text = fact.get('text', '').strip()
        if not text:
            continue
        # Preserve execution syntax in the detail, but put its significance
        # first. DNS observations support a query, not an established C2 link.
        dns = bool(re.search(r'目的端口[ ：]*53(?:\D|$)', text))
        if dns:
            summary = 'DNS记录证明主机发起了域名解析请求，提供相关访问行为依据。'
            text = text.replace('发起通信', '发起DNS查询').replace(' 通信', '发起DNS查询')
        elif '来源记录进程' in text or '程序执行' in text:
            summary = '进程与命令行记录提供程序执行及启动方式的依据。'
        elif '来源记录文件' in text:
            summary = '文件记录提供定位相关样本及其来源的依据。'
        else:
            summary = meaning or '来源记录提供本事件相关行为的依据。'
        marker = (summary, text)
        if marker not in seen:
            result.append({'time': fact.get('time', ''), 'summary': summary, 'detail': text})
            seen.add(marker)
    return result[:6]


def _handling_advice(name, host=''):
    """A concrete, brief recommendation, never a claim of executed actions."""
    lower = name.lower()
    # fakewebsite is a DNS/phishing threat family, not a Web application flaw.
    if any(term in lower for term in ('fakewebsite', 'phishing', 'dga', '域名', '钓鱼')):
        return '隔离受影响主机，清理恶意项，阻断恶意域名并复查。'
    if any(term in lower for term in ('蠕虫', '病毒', '木马', '挖矿', '远控', '恶意软件')):
        return '隔离受影响主机，清除恶意程序及启动项，复查异常外联。'
    if any(term in lower for term in ('sql', '注入', '目录遍历', '跨站', '漏洞')):
        return '阻断攻击来源，修复受影响接口并清除入侵遗留，复查利用迹象。'
    if any(term in lower for term in ('登录', '口令', '密码', '爆破')):
        return '停用异常账号并重置凭据，限制异常登录来源，复查访问记录。'
    if any(term in lower for term in ('shell', 'netcat', '命令', '进程')):
        return '隔离受影响主机，终止恶意进程并清除持久化项，复查外联。'
    return '隔离受影响资产，按上述证据清除恶意项并复查异常是否停止。'


def render_notification(event, story, responsible_name='', *, timezone_name='Asia/Shanghai'):
    """Render one confirmed incident; no rendering path can enable delivery."""
    story = story if isinstance(story, dict) else {}
    basics = normalize_basics(event.get('basics', {}))
    basics.update({key: value for key, value in normalize_basics(story.get('basics', {})).items()
                   if value is not None and value != ''})
    device = _text(event.get('device_name') or story.get('device') or '未提供', 160)
    host = _text(event.get('host') or basics.get('host') or '未提供', 120)
    name = readable_text(event.get('name') or basics.get('name') or '安全事件', 100)
    person = _text(responsible_name or '责任人', 80)
    start = display_time(basics.get('start_time') or event.get('startTime'), timezone_name)
    end = display_time(basics.get('end_time') or event.get('endTime'), timezone_name)
    period = start + (' — ' + end if end and end != start else '') if start else end or '来源未提供'
    details = [('监测设备', device), ('影响主机', host), ('风险等级', _severity(basics)),
               ('发生时间', period), ('处置责任人', person)]
    evidence = _evidence(story, timezone_name)
    conclusion, explanation = _conclusion(event, story, evidence)
    timeline = _timeline(story, timezone_name)
    advice = _handling_advice(name, host)
    # A notification is a request to remedy a confirmed risk, not another
    # request for the owner to repeat the investigation. Nonblocking forensic
    # gaps remain available in the incident detail page.
    actions = ['完成后请直接回复本邮件，说明处理措施和结果；如暂未完成，请说明当前进展。',
               '系统将在下一轮监测中读取反馈，更新事件状态并回查确认。']
    footer = '事件编号：' + _text(event.get('id'), 160)
    subject = re.sub(r'[\r\n]', ' ', f'安全事件处置通知 · {name} · {device} · {host}')[:240]
    lines = [f'{person}：', '', f'【需要处置】{name}', '', '调查结论', conclusion, *explanation, '', '基本信息']
    lines += [f'{title}：{value}' for title, value in details]
    lines += ['', '关键证据']
    lines += [f'- {fact["summary"]} {fact["time"] + "；" if fact["time"] else ""}{fact["detail"]}' for fact in evidence]
    if not evidence:
        lines.append('- 风险判断依据见上述调查结论。')
    lines += ['', '事件经过']
    lines += [f'- {time}：{text}' for time, text in timeline] or ['- 来源未提供可还原的详细发生时间，请结合上述行为核查。']
    lines += ['', '总结和处置建议', '总结：' + conclusion, '处置建议：' + advice]
    lines += ['', '反馈方式', *['- ' + action for action in actions], '', footer,
              '如另起邮件反馈，请带上此事件编号，便于关联本次处置。']
    text = '\n'.join(lines)

    def e(value):
        return escape(str(value), quote=True)

    def section(title, content):
        return (f'<h2 style="margin:24px 0 10px;font-size:16px;color:#173f36">{e(title)}</h2>' + content)

    def bullets(items):
        return '<ul style="margin:0;padding-left:22px">' + ''.join(f'<li style="margin:9px 0">{item}</li>' for item in items) + '</ul>'

    table = '<table role="presentation" style="width:100%;border-collapse:collapse">' + ''.join(
        f'<tr><th style="text-align:left;vertical-align:top;width:100px;padding:7px 0;color:#64746c;font-weight:normal">{e(title)}</th>'
        f'<td style="padding:7px 0;color:#1c2c25">{e(value)}</td></tr>' for title, value in details) + '</table>'
    evidence_html = bullets([f'<strong style="color:#1d4434">{e(fact["summary"])}</strong> '
                             f'<span style="color:#526b5c">{e(fact["time"])}</span>'
                             + (f'；{e(fact["detail"])}' if fact["detail"] else '')
                             for fact in evidence]) if evidence else '<p>风险判断依据见上述调查结论。</p>'
    def time_html(value):
        clock, _, zone = str(value).partition('（')
        return f'<span style="white-space:nowrap">{e(clock.strip())}</span>' + (f'<br><span style="font-size:12px">{e("（" + zone)}</span>' if zone else '')

    timeline_html = '<table role="presentation" style="width:100%;border-collapse:collapse">' + ''.join(
        f'<tr><td style="padding:9px 12px 9px 0;width:165px;vertical-align:top;color:#456a5a;border-bottom:1px solid #edf2ef">{time_html(time)}</td>'
        f'<td style="padding:9px 0;vertical-align:top;border-bottom:1px solid #edf2ef">{e(description)}</td></tr>' for time, description in timeline) + '</table>' if timeline else '<p>来源未提供可还原的详细发生时间，请结合上述行为核查。</p>'
    html = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head>'
            '<body style="margin:0;background:#f3f7f5;color:#202c26;font-family:Arial,\'Microsoft YaHei\',sans-serif;font-size:14px;line-height:1.75">'
            '<div style="max-width:720px;margin:24px auto;background:#ffffff;border:1px solid #dce9e1;border-radius:12px;padding:28px">'
            '<p style="margin:0 0 8px;color:#276747;font-size:13px;font-weight:bold">Flocks 安全运营 · 需要处置</p>'
            f'<h1 style="font-size:23px;line-height:1.45;margin:0 0 18px;color:#142b20">{e(name)}</h1><p>{e(person)}：</p>'
            + section('调查结论', f'<div style="background:#ecf7f0;border-left:4px solid #35a06c;padding:14px 18px;border-radius:4px"><strong>{e(conclusion)}</strong>'
                      + ''.join(f'<p style="margin:8px 0 0">{e(sentence)}</p>' for sentence in explanation) + '</div>')
            + section('基本信息', table) + section('关键证据', evidence_html) + section('事件经过', timeline_html)
            + section('总结和处置建议', f'<p style="margin:0 0 8px">{e(conclusion)}</p>'
                      + f'<p style="margin:0;padding:12px 16px;background:#edf5ff;border-radius:6px"><strong>处置建议：</strong>{e(advice)}</p>')
            + section('反馈方式', bullets([e(action) for action in actions]))
            + f'<div style="margin-top:26px;padding-top:16px;border-top:1px solid #e3ece6;font-size:12px;color:#697a70;word-break:break-all">{e(footer)}<br>如另起邮件反馈，请带上此事件编号，便于关联本次处置。</div>'
            '</div></body></html>')
    return Notification(subject=subject, text=text, html=html)

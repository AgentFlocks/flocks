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

TEMPLATE_VERSION = 1
_IDS = re.compile(r'\b(?:incident|alert)-[A-Za-z0-9-]+\b|\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b', re.I)
_REFS = re.compile(r'\[?\bevidence[-_]\d+\b\]?', re.I)
_RAW_ENUM = re.compile(r'\b(?:gptResult|attack_result|attackResult|attackState|attack_state|incidentSeverity|threatLevel|firstTime|lastTime)\s*[=:：]\s*[\w.-]+(?:[（(][^）)]*[）)])?', re.I)
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


def _conclusion(story):
    sentences = re.split(r'(?<=[。！？])|\n+', _text(story.get('conclusion'), 4000))
    cleaned = [readable_text(s, 240) for s in sentences if s.strip()]
    useful = [s for s in cleaned if not _INTERNAL.search(s)]
    useful = [s for s in useful if s and not re.fullmatch(r'[\W\d]+', s)]
    # Keep the owner's immediate decision separate from investigation prose.
    return '；'.join(useful[:3])[:420] or '已取得与本事件一致的异常行为证据，确认存在安全风险，需要责任人核查并处置。'


def _timeline(story, timezone_name):
    entries = [item for item in story.get('timeline', [])[:40] if isinstance(item, dict)]
    # Alert first/last activity and concrete endpoints make the chronology
    # useful to an operator. They are source facts, never collection timestamps.
    for item in story.get('alert_details', [])[:20]:
        if not isinstance(item, dict):
            continue
        name = readable_text(item.get('name'), 100)
        if not name:
            continue
        activity = name
        if item.get('src_ip') and item.get('dst_ip'):
            activity += f"；主机 {_text(item['src_ip'], 80)} → {_text(item['dst_ip'], 80)}"
            if item.get('dst_port'):
                activity += '，目的端口 ' + _text(item['dst_port'], 50)
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
    return result[:12]


def _evidence(story, timezone_name):
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


def _handling_advice(name, host):
    """Owner tasks suggested by event type, never claims of actions executed."""
    lower = name.lower()
    if any(term in lower for term in ('蠕虫', '病毒', '木马', 'dga', '挖矿', '远控')):
        return f'请排查主机 {host} 的可疑进程、启动项及异常域名访问；按本单位流程处理恶意项，并复查异常通信是否停止。'
    if any(term in lower for term in ('sql', '注入', '目录遍历', 'web', '跨站')):
        return f'请核查主机 {host} 的相关访问日志、异常请求及服务暴露情况，处理攻击来源和可利用的问题，并复查是否存在异常操作。'
    if any(term in lower for term in ('登录', '口令', '密码', '爆破')):
        return f'请核查主机 {host} 的账号登录日志、来源地址及成功登录情况，处理异常账号或访问，并复查是否仍有异常活动。'
    if any(term in lower for term in ('shell', 'netcat', '命令', '进程')):
        return f'请核查主机 {host} 的相关进程、命令行及外联连接，确认业务归属，处理未经授权的活动并复查。'
    return f'请核查主机 {host} 的上述异常活动，并按本单位安全处置要求处理和复查。'


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
    conclusion = _conclusion(story)
    evidence = _evidence(story, timezone_name)
    timeline = _timeline(story, timezone_name)
    # Nonblocking investigation limitations can help the owner locate a threat.
    # Technical diagnostics and unresolved judgement gaps never appear in mail.
    gaps = [readable_text(g, 160) for g in story.get('gaps', [])
            if isinstance(g, str) and not _INTERNAL.search(g) and
            re.search(r'感染途径|变种|样本|文件位置|所属(?:业务|人员)|根因', g)]
    gaps = list(dict.fromkeys(g for g in gaps if g))[:2]
    actions = [_handling_advice(name, host),
               '完成后请直接回复本邮件，说明处理措施、处理结果及复查情况；如暂未完成，请说明当前进展。',
               '系统将在后续监测中读取回复、核对处理结果，并更新事件状态。']
    footer = '事件编号：' + _text(event.get('id'), 160)
    subject = re.sub(r'[\r\n]', ' ', f'安全事件处置通知 · {name} · {device} · {host}')[:240]
    lines = [f'{person}：', '', f'【需要处置】{name}', '', '调查结论', conclusion, '', '基本信息']
    lines += [f'{title}：{value}' for title, value in details]
    lines += ['', '关键证据']
    lines += [f'- {fact["time"] + "：" if fact["time"] else ""}{fact["text"]}。{fact["meaning"]}' for fact in evidence]
    if not evidence:
        lines.append('- 风险判断依据见上述调查结论。')
    lines += ['', '事件经过']
    lines += [f'- {time}：{text}' for time, text in timeline] or ['- 来源未提供可还原的详细发生时间，请结合上述行为核查。']
    if gaps:
        lines += ['', '处置时请补充核查', *['- ' + gap for gap in gaps]]
    lines += ['', '请您处理', *['- ' + action for action in actions], '', footer,
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
    evidence_html = bullets([f'{"<strong>" + e(fact["time"]) + "</strong><br>" if fact["time"] else ""}{e(fact["text"])}。<br><span style="color:#566b62">{e(fact["meaning"])}</span>' for fact in evidence]) if evidence else '<p>风险判断依据见上述调查结论。</p>'
    timeline_html = '<table role="presentation" style="width:100%;border-collapse:collapse">' + ''.join(
        f'<tr><td style="padding:9px 12px 9px 0;width:165px;vertical-align:top;color:#456a5a;border-bottom:1px solid #edf2ef">{e(time)}</td>'
        f'<td style="padding:9px 0;vertical-align:top;border-bottom:1px solid #edf2ef">{e(description)}</td></tr>' for time, description in timeline) + '</table>' if timeline else '<p>来源未提供可还原的详细发生时间，请结合上述行为核查。</p>'
    html = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head>'
            '<body style="margin:0;background:#f3f7f5;color:#202c26;font-family:Arial,\'Microsoft YaHei\',sans-serif;font-size:14px;line-height:1.75">'
            '<div style="max-width:720px;margin:24px auto;background:#ffffff;border:1px solid #dce9e1;border-radius:12px;padding:28px">'
            '<p style="margin:0 0 8px;color:#276747;font-size:13px;font-weight:bold">Flocks 安全运营 · 需要处置</p>'
            f'<h1 style="font-size:23px;line-height:1.45;margin:0 0 18px;color:#142b20">{e(name)}</h1><p>{e(person)}：</p>'
            + section('调查结论', f'<div style="background:#ecf7f0;border-left:4px solid #35a06c;padding:14px 18px;border-radius:4px">{e(conclusion)}</div>')
            + section('基本信息', table) + section('关键证据', evidence_html) + section('事件经过', timeline_html)
            + (section('处置时请补充核查', bullets([e(gap) for gap in gaps])) if gaps else '')
            + section('请您处理', bullets([e(action) for action in actions]))
            + f'<div style="margin-top:26px;padding-top:16px;border-top:1px solid #e3ece6;font-size:12px;color:#697a70;word-break:break-all">{e(footer)}<br>如另起邮件反馈，请带上此事件编号，便于关联本次处置。</div>'
            '</div></body></html>')
    return Notification(subject=subject, text=text, html=html)

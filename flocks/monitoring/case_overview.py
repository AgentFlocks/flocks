"""Concise operator copy from current, event-bound evidence, without new verdicts."""
import json
import re

from .assessment import fact_fields
from .event_details import _readable_text, brief_values
from .evidence_presenter import _summary

_GENERIC_GAP = re.compile(r'调查尚未|尚未形成|置信度|高置信|版本不一致|结论缺少与|未明确证实|系统故障|查询未完成|仅取得部分事实|存在查询失败的来源|模型.*(?:超时|退避|冷却|不可用)|关联告警举证覆盖|事实列表已截断|evidence-\d+.*(?:查询|部分事实)')


def _packet_content(packet):
    text = packet.get('text')
    return isinstance(text, str) and bool(text.strip()) and not re.fullmatch(
        r'[（(](?:来源明确返回空正文|正文为空，来源明确返回)[）)]', text.strip())


def _evidence_status(cards):
    """Only describe current direct observations, never candidate correlations."""
    fields = {field.get('key') for card in cards for field in card.get('fields', [])}
    packets = {packet.get('field') for card in cards for packet in card.get('packets', []) if _packet_content(packet)}
    available = ['告警记录']
    if fields.intersection({'src_ip', 'dst_ip'}):
        available.append('访问 IP')
    packet_labels = [label for key, label in (('requestHead', '请求头'), ('requestBody', '请求正文'),
                      ('responseHead', '响应头'), ('responseBody', '响应正文')) if key in packets]
    if packet_labels:
        available.append('部分' + '、'.join(packet_labels))
    if fields.intersection({'file_name', 'file_path', 'file_md5', 'file_hash'}):
        available.append('文件信息')
    if fields.intersection({'process_name', 'command_line'}):
        available.append('进程或命令行记录')
    return '已有：' + '、'.join(available) + '。', packets, fields


def _behavior_gaps(name, cards, known):
    """Required proof follows the alleged behavior, not an empty verdict label."""
    _, packets, fields = _evidence_status(cards)
    lower = name.lower()
    web = any(term in lower for term in ('sql', 'web', 'http', '注入', '跨站', '目录遍历'))
    missing = []
    if web:
        payload_missing = []
        for card in cards:
            values = {field.get('key'): field.get('value') for field in card.get('fields', [])}
            content = [p for p in card.get('packets', []) if _packet_content(p)]
            request_fields = {p.get('field') for p in content if str(p.get('field', '')).startswith('request')}
            needs_body = any(re.match(r'\s*(?:POST|PUT|PATCH)\s', p['text'], re.I) for p in content if p.get('field') == 'requestHead')
            if ('requestBody' not in request_fields and needs_body or
                    not request_fields and not values.get('url') and not values.get('request_line')):
                title = re.sub(r'^【[^】]*】\s*', '', str(values.get('name') or '告警'))
                payload_missing.append((_readable_text(title, 36), '请求正文' if needs_body else '完整请求'))
        if not packets.intersection({'requestHead', 'requestBody'}):
            missing.append('还缺：完整的攻击请求，需核对具体访问或上传了什么。')
        elif payload_missing:
            details = ('、'.join(f'{title}告警的{field}' for title, field in payload_missing) if len(payload_missing) <= 2
                       else f'{payload_missing[0][0]}等 {len(payload_missing)} 条告警的完整请求内容')
            missing.append(f'还缺：{details}，需核对具体访问或上传了什么。')
        if not packets.intersection({'responseHead', 'responseBody'}):
            missing.append('还缺：服务端响应内容，需核对服务器实际返回了什么。')
        elif 'responseBody' not in packets:
            missing.append('还缺：响应正文或主机执行记录；响应状态码不能说明攻击是否成功。')
        if 'webshell' in lower or '横向' in name:
            targets = []
            if 'webshell' in lower:
                targets.append('Webshell 文件的落地和执行记录，才能确认是否写入并运行')
            if '横向' in name:
                targets.append('跨主机的登录或远程执行记录，才能确认是否发生横向移动')
            missing.append('还需核对：' + '；还需'.join(targets) + '。')
        elif 'sql' in lower or 'sql' in ''.join(str(f.get('value', '')) for c in cards for f in c.get('fields', []) if f.get('key') == 'name').lower():
            missing.append('还需：完整请求和数据库执行记录，才能判断是否存在 SQL 注入及是否执行成功。')
        elif not missing:
            missing.append('还需核对：返回内容是否泄露数据、文件或命令是否实际执行，才能确认攻击造成的影响。')
    elif any(term in lower for term in ('dga', '随机域名', '蠕虫', 'dns')):
        if not fields.intersection({'domain', 'url'}) and not known.intersection({'domain', 'domainName', 'hostDomain', 'destDomain', 'dstDomain'}):
            missing.append('还缺：实际查询的域名或 DNS 请求内容，才能核对异常查询。')
        missing.append('还需：发起查询的进程及对应文件记录，才能确认哪台主机、哪个程序在查询，以及是否感染。')
    elif any(term in lower for term in ('木马', '病毒', '挖矿', '恶意文件', '远控')):
        missing.append('还需：可疑文件及其进程执行记录，才能确认恶意程序是否在主机上运行。')
    return missing


def _closure_text(assessment):
    if assessment.get('source_closure_basis') == 'source_status':
        return '该安全事件已被 XDR 关闭。'
    if assessment.get('source_closure_basis') == 'not_returned':
        return '该安全事件已标记为源端关闭（XDR 本轮查询未再返回）。'
    return '该安全事件已标记为源端关闭。'


def _concrete_gaps(items):
    gaps = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, str) or _GENERIC_GAP.search(item):
            continue
        text = _readable_text(item, 180)
        if text and text not in gaps:
            gaps.append(text)
    return gaps[:3]


def _retained_overview(case, assessment):
    """Use validated retained facts, never a refresh error as the risk reason."""
    snapshot = case.get('confirmed_risk', {})
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except (ValueError, TypeError):
            snapshot = {}
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    prior = snapshot.get('result', {})
    prior = prior if isinstance(prior, dict) else {}
    summary = _readable_text(assessment.get('risk_fact_summary') or assessment.get('confirmed_reason'), 240)
    if not summary:
        summary = '此前已核实存在风险，原有调查结论保留。'
    historical = assessment.get('risk_scope') == 'previous_activity'
    if assessment.get('source_closed'):
        summary = _closure_text(assessment) + '关闭前已确认风险：' + summary
    elif historical:
        summary = '此前活动已确认风险：' + summary
    finding_source = assessment.get('risk_fact_findings') or prior.get('operator_explanation')
    findings = [_readable_text(item, 260) for item in finding_source[:2] if isinstance(item, str)] if isinstance(finding_source, list) else []
    findings = [line for line in findings if line]
    if historical:
        findings = ['此前活动依据：' + line for line in findings]
    missing = _concrete_gaps(assessment.get('risk_fact_gaps') or assessment.get('gaps') or prior.get('gaps'))
    value = {'summary': summary, 'findings': findings, 'missing_evidence': missing,
             'gaps': (['已有（此前核实）：' + findings[0]] if missing and findings else []) + missing,
             'confirmed_risk_retained': True}
    if not assessment.get('ended') and not assessment.get('source_closed'):
        value.update(recheck_state=assessment.get('recheck_state'),
                     recheck_reason=_readable_text(assessment.get('recheck_reason'), 240))
    return value

def operator_overview(event, case, assessment, story, proofs):
    """Presentation only: input proofs were filtered by bound_evidence."""
    if assessment.get('confirmed_risk_retained'):
        return _retained_overview(case, assessment)
    result = case.get('result') if isinstance(case.get('result'), dict) else {}
    cards = story.get('evidence_cards', [])
    rank = {'request': 0, 'process': 1, 'file': 2, 'network': 3, 'domain': 4}
    findings = []
    for card in sorted(cards, key=lambda card: (bool(card.get('boundary')), rank.get(card.get('kind'), 5))):
        fields = {field['key']: field.get('value') for field in card.get('fields', []) if isinstance(field, dict)}
        text = _summary(fields, '', card.get('packets', [])) or _readable_text(card.get('summary'), 190)
        if text and '跨设备' in card.get('boundary', ''):
            text = '候选关联：' + text
        if text and text not in findings:
            findings.append(text)
        if len(findings) == 2:
            break
    host = brief_values(event.get('host') or story.get('basics', {}).get('host'), '主机')
    name = _readable_text(event.get('name') or story.get('basics', {}).get('name') or '安全事件', 80)
    has_findings = bool(findings)
    if not findings:
        findings = [f'XDR记录{("主机" + host) if host else "本事件"}的「{name}」告警；尚未取得可核实的行为详情。']
    direct = [proof for proof in proofs if proof.get('device') == event.get('device') and proof.get('kind') != 'related']
    known = set().union(*(fact_fields(proof.get('facts')) for proof in direct)) if direct else set()
    direct_ids = {proof['id'] for proof in direct}
    direct_cards = [card for card in cards if any(record.get('evidence_id') in direct_ids for record in card.get('records', []))]
    missing = []
    def add(text):
        text = _readable_text(text, 150)
        if text and text not in missing:
            missing.append(text)
    verdict = assessment.get('verdict', 'unknown')
    if verdict == 'unknown':
        for item in _behavior_gaps(name, direct_cards, known):
            add(item)
        # Concrete gaps precede generic program-state explanations.
        for item in [*(result.get('blocking_gaps') if isinstance(result.get('blocking_gaps'), list) else []),
                     *(result.get('gaps') if isinstance(result.get('gaps'), list) else [])]:
            if isinstance(item, str) and not _GENERIC_GAP.search(item):
                add(item)
        if not assessment.get('is_current'):
            add('当前活动版本对应的取证，历史证据仅供参考。')
        if not missing:
            add('还需：与告警对应的完整行为记录和目标主机的处理结果，才能确认是否存在攻击及其影响。')
    if verdict == 'risk':
        missing = _concrete_gaps(result.get('gaps'))
    if (assessment.get('ended') or assessment.get('source_closed')) and verdict == 'unknown':
        closed = (_closure_text(assessment) if assessment.get('source_closed') or '源端关闭' in str(assessment.get('label', ''))
                  else '该安全事件' + (str(assessment.get('label')) if str(assessment.get('label', '')).startswith('已')
                                        else '已' + str(assessment.get('label') or '结束监测')) + '。')
        summary = closed + ('此前调查：' + findings[0] + '尚未确认实际影响。' if has_findings
                            else '此前仅取得告警信息，尚未查清具体行为和实际影响。')
    elif verdict in {'risk', 'benign'}:
        summary = _readable_text(result.get('operator_summary') or result.get('reason'), 200)
        if re.search(r'cap-\d+|\b\d{10,13}\b|attackState|attackResult|gptResult', summary):
            summary = ''
        if not summary:
            summary = ('已确认风险：' if verdict == 'risk' else '已核实无风险：') + findings[0]
    else:
        summary = f'XDR检出{("主机" + host) if host else "本事件"}的「{name}」告警，当前仍需补证核对。'
    if assessment.get('source_closed') and verdict in {'risk', 'benign'}:
        summary = _closure_text(assessment) + ('关闭前已确认风险：' if verdict == 'risk' else '关闭前已核实无风险：') + summary
    available = _evidence_status(direct_cards)[0] if direct_cards else '已有：本事件告警，尚无可核对的直接行为详情。'
    return {'summary': summary, 'findings': findings, 'missing_evidence': missing[:3],
            'gaps': ([available] if missing else []) + missing[:3]}

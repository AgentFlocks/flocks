"""Concise operator copy from current, event-bound evidence, without new verdicts."""
import json
import re

from .assessment import fact_fields
from .event_details import _readable_text, brief_values
from .evidence_presenter import _summary

_GENERIC_GAP = re.compile(r'调查尚未|尚未形成|置信度|高置信|版本不一致|结论缺少与|未明确证实|系统故障|查询未完成|仅取得部分事实|存在查询失败的来源|模型.*(?:超时|退避|冷却|不可用)|关联告警举证覆盖|事实列表已截断|evidence-\d+.*(?:查询|部分事实)')


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
    packets = {packet.get('field') for packet in story.get('packets', []) if packet.get('evidence_id') in direct_ids
               and packet.get('text') and '正文为空' not in packet['text']}
    missing = []
    def add(text):
        text = _readable_text(text, 150)
        if text and text not in missing:
            missing.append(text)
    verdict = assessment.get('verdict', 'unknown')
    if verdict == 'unknown':
        lower = name.lower()
        web = any(term in lower for term in ('sql', 'web', 'http', '注入', '跨站', '目录遍历'))
        dga = any(term in lower for term in ('dga', '随机域名', '蠕虫'))
        if web:
            if not packets.intersection({'requestHead', 'requestBody'}):
                add('实际攻击请求内容，尚不能核对攻击语句。')
            if not packets.intersection({'responseHead', 'responseBody'}):
                add('服务端响应或主机执行结果，尚不能确认利用效果。')
        elif dga:
            if not known.intersection({'domain', 'domainName', 'hostDomain', 'destDomain', 'dstDomain', 'requestBody', 'request_body'}):
                add('实际查询域名或DNS载荷，尚不能核对异常解析行为。')
            if not known.intersection({'fileName', 'filePath', 'commandLine', 'processCommandLine', 'processName'}):
                add('主机文件、进程或执行记录，尚不能确认感染情况。')
        elif any(term in lower for term in ('木马', '病毒', '挖矿', '恶意文件', '远控')):
            if not known.intersection({'fileName', 'filePath', 'fileMd5', 'processName', 'commandLine'}):
                add('主机侧文件或进程记录，尚不能确认恶意程序及其执行。')
        # Concrete gaps precede generic program-state explanations.
        for item in [*(result.get('blocking_gaps') if isinstance(result.get('blocking_gaps'), list) else []),
                     *(result.get('gaps') if isinstance(result.get('gaps'), list) else [])]:
            if isinstance(item, str) and not _GENERIC_GAP.search(item):
                add(item)
        if not assessment.get('is_current'):
            add('当前活动版本对应的取证，历史证据仅供参考。')
        if not missing:
            add('现有行为的完整核对及实际影响依据，当前尚未形成可靠判断。')
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
    return {'summary': summary, 'findings': findings, 'missing_evidence': missing[:3]}

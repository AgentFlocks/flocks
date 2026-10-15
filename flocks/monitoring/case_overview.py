"""Concise operator copy from current, event-bound evidence, without new verdicts."""
import re

from .assessment import fact_fields
from .event_details import _readable_text, behavior_description, brief_values

_GENERIC_GAP = re.compile(r'调查尚未|尚未形成|置信度|高置信|版本不一致|结论缺少与|未明确证实|系统故障|查询未完成|仅取得部分事实|存在查询失败的来源')


def operator_overview(event, case, assessment, story, proofs):
    """Presentation only: input proofs were filtered by bound_evidence."""
    result = case.get('result') if isinstance(case.get('result'), dict) else {}
    cards = story.get('evidence_cards', [])
    rank = {'request': 0, 'process': 1, 'file': 2, 'network': 3, 'domain': 4}
    findings = []
    for card in sorted(cards, key=lambda card: (bool(card.get('boundary')), rank.get(card.get('kind'), 5))):
        fields = {field['key']: field.get('value') for field in card.get('fields', []) if isinstance(field, dict)}
        text = (_readable_text(card.get('summary'), 190) if card.get('kind') == 'request'
                else behavior_description(fields, story.get('basics')))
        if text and card.get('kind') != 'request':
            source = _readable_text(card.get('source') or '来源记录', 70)
            if re.fullmatch(r'[a-f0-9-]{24,}', source):
                source = '关联来源'
            text = source + ('候选关联：' if card.get('boundary') else '记录：') + text
        if text and card.get('kind') == 'request' and card.get('boundary'):
            text = '候选关联：' + text
        if text and text not in findings:
            findings.append(text)
        if len(findings) == 2:
            break
    host = brief_values(event.get('host') or story.get('basics', {}).get('host'), '主机')
    name = _readable_text(event.get('name') or story.get('basics', {}).get('name') or '安全事件', 80)
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
    if assessment.get('ended') and verdict == 'unknown':
        summary = f'当前状态：{assessment.get("label") or "已归档"}；保留此前证据与研判记录。'
    elif verdict in {'risk', 'benign'}:
        summary = _readable_text(result.get('operator_summary') or result.get('reason'), 200)
        if re.search(r'cap-\d+|\b\d{10,13}\b|attackState|attackResult|gptResult', summary):
            summary = ''
        if not summary:
            summary = ('已确认风险：' if verdict == 'risk' else '已核实无风险：') + findings[0]
    else:
        summary = f'XDR检出{("主机" + host) if host else "本事件"}的「{name}」告警，当前仍需补证核对。'
    return {'summary': summary, 'findings': findings, 'missing_evidence': missing[:3]}

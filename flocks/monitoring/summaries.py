"""Deterministic, bounded plain-text step summaries from already queried facts."""
from dataclasses import dataclass
import unicodedata

MAX_DETAILS = 48_000
ENTITY_LABELS = {'host': '主机', 'file': '文件', 'process': '进程', 'ip': '外部 IP', 'innerip': '内部 IP', 'dns': '域名'}
STATUS_LABELS = {0: '待处置', 10: '处置中', 30: '已防护', 40: '处置完成', 50: '已挂起', 60: '忽略', 70: '已遏制'}


@dataclass
class Summary:
    text: str
    details: str = ''


def label(value, fallback='未提供', limit=160):
    if type(value) not in (str, int):
        return fallback
    text = ' '.join(''.join(c for c in str(value).replace('\n', ' ').replace('\r', ' ') if not unicodedata.category(c).startswith('C')).split())
    if not text:
        return fallback
    return text if len(text) <= limit else text[:limit] + '…（已截短）'


def detail_lines(lines, total):
    kept, length = [], 0
    for line in lines:
        if length + len(line) + 1 > MAX_DETAILS:
            break
        kept.append(line)
        length += len(line) + 1
    if len(kept) < total:
        kept.append(f'还有 {total - len(kept)} 条明细未在此摘要中展开；以上不是全部记录，统计包含全部返回数据。')
    return '\n'.join(kept)


def event_label(event):
    return f"{label(event.get('name'), '名称未提供')}（ID：{label(event.get('uuId') or event.get('id'), limit=100)}）"


def incident_type(event):
    native = [label(event.get(key), '', 80) for key in ('incidentThreatClass', 'incidentThreatType')]
    return ' / '.join(value for value in native if value) or label(event.get('type') or event.get('incidentType') or event.get('eventType'), '类型未提供', 80)


def page_summary(page, items, cumulative, complete, *, selection=None, received=None, counts=None):
    if selection is not None:
        text = (f'本地筛选条件：{selection}。第 {page} 页原始返回 {received} 条，'
                f'符合条件 {len(items)} 条，排除 {received - len(items)} 条。'
                f"本轮原始累计去重 {counts['source_unique']} 条，符合条件 {cumulative} 条，"
                f"排除 {counts['excluded_unique']} 条；重复返回 {counts['duplicates']} 条。")
        if complete:
            text += f'分页已完整结束。本轮 XDR 安全事件符合条件 {cumulative} 条。'
    else:
        text = f'第 {page} 页查询到 {len(items)} 条事件，本轮累计去重 {cumulative} 条。'
    if complete and selection is None:
        text += f'分页已完整结束。本轮 XDR 安全事件查询到 {cumulative} 条事件。'
    elif not complete:
        text += '分页尚未结束，暂不保存本批事件和上次查询进度。'
    lines = (f"{i}. {event_label(event)}；类型：{incident_type(event)}；主机：{label(event.get('hostIp'), '未提供', 100)}。"
             for i, event in enumerate(items, 1))
    return Summary(text, detail_lines(lines, len(items)))


def hosts_summary(event, hosts):
    missing = sum(not any(host.get(key) for key in ('id', 'hostId', 'hostIp', 'ip', 'name')) for host in hosts)
    text = f'针对事件 {event_label(event)}，查询到 {len(hosts)} 条关联主机记录。'
    if missing:
        text += f'其中 {missing} 条未提供主机标识，不能据此确认具体主机。'
    elif not hosts:
        text += '该事件本次未返回关联主机。'
    lines = (f"{i}. 名称：{label(host.get('name'))}；标识：{label(host.get('hostId') or host.get('id'), limit=100)}；地址：{label(host.get('hostIp') or host.get('ip'), limit=100)}。"
             for i, host in enumerate(hosts, 1))
    return Summary(text, detail_lines(lines, len(hosts)))


def analysis_summary(result, events, groups):
    related = [keys for keys in groups.values() if len(keys) > 1]
    if not events and result['errors']:
        return Summary('关联分析已跳过：事件查询未完整成功，没有可分析的完整事件批次。不能将此次失败理解为查询到 0 条事件。此前收到的回信是否完成跟进，以本轮回信及回查记录为准。')
    text = f"关联分析完成：分析 {len(events)} 条事件，形成 {len(related)} 个同设备、同主机关联组，涉及 {sum(map(len, related))} 条事件；{result['risk']} 条风险，{result['unknown']} 条待判定。"
    if result['errors']:
        text += f"有 {len(result['errors'])} 项查询或主机补查错误，结果不完整。"
    if len(events) == 1:
        text += '本轮只有一条样本，因此没有多事件关联组；这不表示没有关联主机。'
    text += '这里按事件等级做初筛，不等于已经确认恶意；是否需要处置还须结合具体实体证据核对。状态标记与闭环结果以回查事实为准。'
    by_key = {event['key']: event for event in events}
    lines = (f"{i}. 主机：{label(by_key[keys[0]].get('host'), limit=100)}；关联事件：" + '；'.join(event_label(by_key[key]) for key in keys[:20]) + (f'；另 {len(keys)-20} 条事件见上方各页查询明细' if len(keys) > 20 else '') + '。'
             for i, keys in enumerate(related, 1))
    lines = list(lines) + [f"事件：{event_label(event)}；初筛依据：{label(event.get('reason'), limit=500)}；与本轮同设备、同主机的其他事件关联 {len(event.get('related', []))} 条。" for event in events]
    return Summary(text, detail_lines(lines, len(lines)))


def evidence_summary(event, decision, *, development=False):
    evidence = decision.evidence
    failed = evidence['failedQueries']
    value = evidence['gptResult']
    verdict = ('属于规则识别的恶意结论' if value in {10, 20, 110, 115, 120} else
               '属于规则识别的误报结论，仍须核对实体证据' if value in {40, 160} else '本组件无法仅凭该值确认恶意或误报')
    lines = [f'事件：{event_label(event)}', f"XDR 研判：{verdict}（gptResult：{label(value, '未提供')}）。"]
    for kind, name in ENTITY_LABELS.items():
        data = evidence['entities'].get(kind)
        if kind in failed or data is None:
            reason = label(evidence.get('failedReasons', {}).get(kind), '未取得可用实体结果', 300)
            lines.append(f'{name}：证据未取得或结构不符合约定；原因：{reason}。不能当作零条或安全。')
        else:
            malicious = sum(x['level'] == 3 for x in data['items'])
            safe = sum(x['level'] == 1 for x in data['items'])
            unknown = len(data['items']) - malicious - safe
            lines.append(f"{name}：{data['count']} 条" + (f"，隔离确认：{'是' if data['isolated'] else '否'}。" if kind == 'host' else
                         f'；恶意 {malicious} 条、安全 {safe} 条、待判定 {unknown} 条。'))
    conclusion = ('证据不完整，无法给出完整风险结论。' if failed else
                  'XDR 误报结论与业务或实体证据一致，满足忽略建议条件。' if decision.target == 60 else
                  '已返回证据仅作为跟进依据，不能把等级或既有状态当作威胁已消除。')
    text = f'证据核对完成：{conclusion}规则建议：{decision.reason.replace("标为", "建议标为")}。此处尚未修改 XDR。'
    if development:
        text += '本次为邮件链路测试，无论是否需要处置，都向已配置的责任人发送一封测试通知；风险结论保持原样。'
    return Summary(text, '\n'.join(lines))


def operation_summary(event, params, value):
    """Summarize only whitelisted facts; never persist raw entity payloads."""
    from time import time
    from .adapter import page_items, response_items
    from .status_rules import entities
    action = params['action']
    prefix = f'事件：{event_label(event)}。'
    if action == 'list':
        items, _ = page_items(value)
        details = '\n'.join(f"{event_label(item)}；当前状态：{STATUS_LABELS.get(item.get('dealStatus'), '未知')}；主机：{label(item.get('hostIp'))}。" for item in items[:5])
        return {'matches': len(items)}, Summary(prefix + f'按事件编号精确查询返回 {len(items)} 条。' + ('用来确认仍是原事件及当前状态，查询本身不会修改状态。' if items else '未返回目标事件，本次不继续发送或写入；保留记录供核对。'), details)
    if action == 'get_entities':
        kind = params['entity_type']
        data = entities(kind, value, int(time()))
        source = response_items(value.get('data'))
        lines = []
        for index, item in enumerate(source):
            identifiers = '；'.join(f'{key}：{label(item[key])}' for key in ('id', 'name', 'hostIp', 'fileName', 'md5', 'sha256', 'processName', 'ip', 'domain') if type(item.get(key)) in (str, int))
            fact = data['items'][index] if kind != 'host' else {}
            lines.append(f"{index + 1}. {identifiers or '未提供可展示的名称或标识'}；" + (f"威胁判定：{ {1: '安全', 3: '恶意'}.get(fact.get('level'), '待判定')}；控制成功证据：{'有' if fact.get('controlled') else '未确认'}。" if kind != 'host' else '主机隔离证据在分析步骤统一核对。'))
        details = f"工具动作：get_entities；关联字段：uuid；实体类型：{kind}。\n" + detail_lines(lines, len(source))
        return data, Summary(prefix + f"查询关联{ENTITY_LABELS.get(kind, kind)}，得到 {data['count']} 条。" + ('没有返回该类实体，不代表事件没有风险。' if not data['count'] else '这些是该事件自身的关联证据，用于后续核对。'), details)
    return {'request_completed': True}, Summary(prefix + '状态标记接口已返回，接下来重新查询 XDR；只有回查状态一致才算确认。')


def round_summary(status, result, observed, feedback, notification, enabled, development):
    text = f"本轮{ {'completed': '完成', 'partial': '部分完成', 'failed': '失败'}[status]}：读取 {len(observed)} 条事件，初筛风险 {result['risk']} 条，待判定 {result['unknown']} 条。"
    if not observed:
        text += ('查询未完整成功，不能据此说没有告警；请检查失败步骤后重试。' if result['errors'] else
                 '当前时间范围和筛选条件下没有可分析事件，因此未生成新的告警通知；后续轮次继续查询。')
    for event in observed[:5]:
        investigation = event.get('investigation')
        if investigation:
            state = {'ready': '已形成调查结论', 'pending': '已保存，待续查', 'needs_review': '需人工核对'}.get(investigation['state'], '待核对')
            text += f"\n智能体调查：{event_label(event)} · {state}。{investigation.get('reason', '')}"
            if investigation['state'] == 'pending':
                text += '下一轮优先读取已有证据继续调查。'
    if enabled:
        text += f"\n邮件：发送 {notification['sent']} 封；处理回信 {feedback['processed']} 封，{feedback['verified']} 封已回查确认，{feedback['pending']} 封待跟进。"
        text += '\n' + '\n'.join(notification.get('explanations', [])[:5]) if notification.get('explanations') else ''
        if notification['sent']:
            text += '\n下一步：请回复测试处理结果；回信先保存，下一轮解读并核对原事件，明确完成后标记忽略并回查。' if development else '\n下一步：等待责任人回信，下一轮解读并核对原事件，再标记状态和回查。'
        elif feedback['pending'] or notification['pending']:
            text += '\n下一步：查看上方失败或待核对原因及邮件跟进记录；发送结果未知时不会自动重发。'
        elif observed:
            text += '\n未新增邮件不等于没有风险；请按上方通知判断查看去重或未发送原因。'
    else:
        text += '\n邮件跟进未启用，本轮只做查询和分析，没有发信或修改状态。需要联调时先配置责任人邮箱并启用邮件跟进。'
    if development:
        text += '\n本轮仅统计随机样本，不代表全部告警；测试标记忽略不代表威胁已消除。'
    return text

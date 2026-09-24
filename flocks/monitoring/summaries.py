"""Deterministic, bounded plain-text step summaries from already queried facts."""
from dataclasses import dataclass
import unicodedata

MAX_DETAILS = 48_000


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
        text += '分页尚未结束，暂不提交事件及查询水位。'
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
        return Summary('关联分析已跳过：事件查询未完整成功，没有可分析的完整事件批次。不能将此次失败理解为查询到 0 条事件。是否执行状态标记以本轮自动标记步骤为准。')
    text = f"关联分析完成：分析 {len(events)} 条事件，形成 {len(related)} 个同设备、同主机关联组，涉及 {sum(map(len, related))} 条事件；{result['risk']} 条风险，{result['unknown']} 条待判定。"
    if result['errors']:
        text += f"有 {len(result['errors'])} 项查询或主机补查错误，结果不完整。"
    text += '当前为关联分析结果，后续状态标记与闭环结果以回查事实为准。'
    by_key = {event['key']: event for event in events}
    lines = (f"{i}. 主机：{label(by_key[keys[0]].get('host'), limit=100)}；关联事件：" + '；'.join(event_label(by_key[key]) for key in keys[:20]) + (f'；另 {len(keys)-20} 条事件见上方各页查询明细' if len(keys) > 20 else '') + '。'
             for i, keys in enumerate(related, 1))
    return Summary(text, detail_lines(lines, len(related)))

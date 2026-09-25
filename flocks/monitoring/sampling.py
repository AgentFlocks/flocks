"""Bounded development sampling; never checkpoints an incomplete source scan."""
import secrets

from .adapter import ContractError, normalize, page_items
from .summaries import Summary, page_summary, event_label, STATUS_LABELS
from . import diagnostics as diag

PAGE_SIZE = 5  # XDR list contract minimum, not the number admitted to monitoring.
DESCRIPTION = '开发联调：不限处置状态，优先中危/高危/严重，每轮随机分析 1 条；仅统计样本。'


def enabled(policy):
    return getattr(policy, 'development_sample', False) is True


async def query_sample(adapter, device, start, end, recorder):
    base = {'action': 'list', 'start_time': start, 'end_time': end, 'time_field': 'endTime',
            'deal_statuses': [], 'white_status': ['未加白', '部分加白'], 'page_size': PAGE_SIZE}

    async def fetch(page, severities):
        params = {**base, 'page_num': page, 'api_params': {'severities': severities}}
        async def operation(message_id):
            items, total = page_items(await adapter.call(device, params, message_id))
            if total is None or len(items) != min(PAGE_SIZE, max(0, total - (page - 1) * PAGE_SIZE)):
                raise ContractError('联调抽样缺少准确总数或页面不完整，未提交样本')
            if len({item['uuId'] for item in items}) != len(items):
                raise ContractError('联调抽样页面出现重复事件，未提交样本')
            if severities and any(type(item.get('incidentSeverity')) is not int or item['incidentSeverity'] not in severities for item in items):
                raise ContractError('XDR 等级筛选未生效，未提交样本')
            return (items, total), {'page': page, 'received': len(items), 'total': total, 'development_sample': True}, Summary(
                f'联调抽样定位：候选 {total} 条，本页读取 {len(items)} 条；' +
                ('只随机选取 1 条进入分析，其余不会在本轮逐条处理。' if total else '本次筛选没有候选事件。') +
                '每轮重新查看最近 24 小时，上次正常查询进度保持不变。')
        return await recorder.call('查询 XDR 事件', {'device': device, **params}, operation)

    severities = [2, 3, 4]
    items, total = await fetch(1, severities)
    if not total:
        severities = []
        items, total = await fetch(1, severities)
    if not total:
        return []
    index = secrets.randbelow(total)
    page, offset = divmod(index, PAGE_SIZE)
    if page:
        items, latest_total = await fetch(page + 1, severities)
        if latest_total != total:
            raise ContractError('抽样期间事件总数变化，下一轮重新抽样')
    raw = items[offset]
    event = normalize(device, raw)
    event['development_sample'] = True
    diag.event('query.sample', development_sample=True, total=total, page=page + 1, events=1,
               preferred_severity=bool(severities), event_id=diag.opaque(event['key']))

    async def selected(_):
        details = page_summary(page + 1, [raw], 1, False).details
        return [event], {'event': event['id'], 'development_sample': True}, Summary(
            f'本轮随机选中事件：{event_label(event)}。\n当前 XDR 状态：{STATUS_LABELS.get(raw.get("dealStatus"), "未知")}；只分析这一条。'
            '\n原处置状态不影响抽样；本次测试邮件不要求事件必须恶意，分析结果会如实写入邮件。收到明确完成反馈后标记忽略并回查。', details)
    return await recorder.call('选取联调样本', {'development_sample': True}, selected)

"""Validate persisted investigation windows without inventing query boundaries."""
import math
import re

from .adapter import ContractError


MAX_UNIX_SECOND = 253402300799  # Last second representable by datetime (year 9999).
MAX_QUERY_SPAN = 86400


class QueryTimeError(ContractError):
    """A deterministic parameter problem, distinct from a model/service outage."""
    kind = 'query_time'
    error_kind = 'query_time'

    def __init__(self, detail, *, code, field, source='关联查询'):
        self.code, self.field = code, field
        super().__init__(f'{source}未执行：{detail}；已保留事件，不能按无风险处理')


def unix_second(value, *, field, source='关联查询'):
    """Accept unambiguous, integral Unix seconds; never infer milliseconds or dates."""
    label = {'start': '开始时间', 'end': '结束时间', 'current': '动态当前时间'}[field]
    if value is None or isinstance(value, str) and not value.strip():
        raise QueryTimeError(f'原事件调查时间窗口缺少{label}' if field != 'current' else f'{label}缺失',
                             code='missing_value', field=field, source=source)
    if type(value) is str and re.fullmatch(r'[0-9]+', value.strip()) and len(value.strip()) <= 12:
        value = int(value.strip())
    elif type(value) is float and math.isfinite(value) and value.is_integer():
        value = int(value)
    if type(value) is not int or not 0 <= value <= MAX_UNIX_SECOND:
        raise QueryTimeError(f'{label}不是有效的 Unix 秒时间戳（不接受毫秒或无法确定单位的值）',
                             code='invalid_timestamp', field=field, source=source)
    return value


def normalize_window(window, *, source='关联查询', max_span=MAX_QUERY_SPAN):
    """Normalize equivalent types only; missing/reversed/oversized windows remain invalid."""
    if not isinstance(window, dict):
        raise QueryTimeError('原事件调查时间窗口缺失或格式无效',
                             code='missing_window', field='window', source=source)
    start = unix_second(window.get('start'), field='start', source=source)
    end = unix_second(window.get('end'), field='end', source=source)
    if end <= start:
        raise QueryTimeError('原事件调查结束时间必须晚于开始时间',
                             code='invalid_order', field='window', source=source)
    if end - start > max_span:
        raise QueryTimeError(f'原事件调查时间窗口超过 {max_span // 3600} 小时，未扩大或替换查询范围',
                             code='window_too_wide', field='window', source=source)
    return {'start': start, 'end': end}


def tdp_window(window, current):
    """Keep the authorized historical range; future ends may only be shortened."""
    source = 'TDP 关联查询'
    normalized = normalize_window(window, source=source)
    current = unix_second(current, field='current', source=source)
    end = min(current, normalized['end'])
    if end <= normalized['start']:
        raise QueryTimeError('原事件调查时间窗口尚未开始，没有可查询的已发生时间范围',
                             code='future_window', field='window', source=source)
    return {'start': normalized['start'], 'end': end}

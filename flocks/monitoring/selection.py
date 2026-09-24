"""Business predicate, deliberately separate from the unverified XDR mapping.

These are internal semantic states, not appliance field names or wire values.
The production runtime must not use this rule until a verified state reader is
available for its XDR version. No default reader or inferred mapping is supplied.
"""
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from .adapter import ContractError
from .pagination import SelectionRule

FILTER_EXPRESSION = 'filter 加白状态 != "已加白" and 处置状态 in {"待处置","处置中"}'


class Disposition(Enum):
    PENDING = '待处置'
    IN_PROGRESS = '处置中'
    OTHER = '已确认的其他处置状态'


@dataclass(frozen=True)
class IncidentState:
    disposition: Disposition | None
    is_whitelisted: bool | None


def matches_monitoring_scope(state: IncidentState) -> bool:
    # Validate both terms first. In particular None, 0 and "false" must never
    # fall through a truthiness test and become "not whitelisted".
    if not isinstance(state, IncidentState) or type(state.disposition) is not Disposition:
        raise ContractError('事件处置状态缺失或未识别，无法确认筛选范围')
    if type(state.is_whitelisted) is not bool:
        raise ContractError('事件加白状态缺失或未识别，无法确认筛选范围')
    return state.disposition in {Disposition.PENDING, Disposition.IN_PROGRESS} and not state.is_whitelisted


def monitoring_selection(read_state: Callable[[dict], IncidentState]) -> SelectionRule:
    """Require an explicit, verified reader; never interpret a raw dict here."""
    return SelectionRule(FILTER_EXPRESSION, lambda raw: matches_monitoring_scope(read_state(raw)))

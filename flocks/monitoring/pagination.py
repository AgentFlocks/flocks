"""Keep source pagination completeness independent of local event selection.

No XDR status mapping is defined here. A selection rule may only be wired into
the runtime after its source field contract is verified; ordinary queries keep
all records. Invalid/unknown source states must raise ContractError in the rule.
"""
from collections.abc import Callable
from dataclasses import dataclass

from .adapter import ContractError, normalize


@dataclass(frozen=True)
class SelectionRule:
    description: str
    matches: Callable[[dict], bool]


class IncidentPages:
    def __init__(self, device, rule: SelectionRule | None = None):
        self.device = device
        self.rule = rule
        self.events = {}
        self.decisions = {}
        self.seen_pages = set()
        self.total = None
        self.received = 0

    @property
    def counts(self):
        return {'received': self.received, 'source_unique': len(self.decisions),
                'matched_unique': len(self.events),
                'excluded_unique': len(self.decisions) - len(self.events),
                'duplicates': self.received - len(self.decisions)}

    def add(self, items, total, page_size):
        """Consume a page already validated by page_items; return page matches.

        Completeness uses original UUIDs/total and original page size, even if
        every item on this page is excluded. The device batch remains private
        until the caller has checked completeness and the page budget.
        """
        signature = tuple(sorted(item['uuId'] for item in items))
        if items and signature in self.seen_pages:
            raise ContractError('分页重复，查询完整性无法确认')
        self.seen_pages.add(signature)
        if total is not None:
            if self.total is not None and total != self.total:
                raise ContractError('分页总数变化，保留水位等待重试')
            self.total = total

        selected = []
        for raw in items:
            included = self.rule.matches(raw) if self.rule else True
            if type(included) is not bool:
                raise ContractError('事件筛选结果无效，不能将未知状态视为符合条件')
            key = raw['uuId']
            if key in self.decisions and included != self.decisions[key]:
                raise ContractError('重复事件筛选状态变化，保留水位等待重试')
            self.decisions[key] = included
            if included:
                self.events[key] = normalize(self.device, raw)
                selected.append(raw)
        self.received += len(items)
        if self.total is not None and len(self.decisions) > self.total:
            raise ContractError('分页总数与去重记录不一致')
        complete = self.total is not None and len(self.decisions) == self.total
        if not complete and len(items) < page_size:
            if self.total is not None:
                raise ContractError('分页提前结束；不推进查询水位')
            complete = True
        return selected, complete

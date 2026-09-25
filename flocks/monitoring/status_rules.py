"""Explainable status selection from documented XDR evidence, never remote prose.

This component changes incident labels only. A successful marking operation is
separate from eradication or containment, and unknown evidence cannot close risk.
"""
from dataclasses import dataclass, asdict
from time import time
from .adapter import ContractError, response_items

RULE_VERSION = 'xdr-evidence-v1'
ENTITY_TYPES = ('host', 'file', 'process', 'ip', 'innerip', 'dns')


@dataclass(frozen=True)
class Decision:
    target: int
    reason: str
    evidence: dict
    rule: str = RULE_VERSION

    def json(self):
        return asdict(self)


def malicious_evidence(decision):
    """Severity alone and a prior disposition do not establish maliciousness."""
    evidence = decision.evidence
    if evidence['failedQueries'] or evidence['gptResult'] in {40, 160}:
        return False
    return evidence['gptResult'] in {10, 20, 110, 115, 120} or any(
        item['level'] == 3 for kind in ('file', 'process', 'ip', 'dns')
        for item in evidence['entities'][kind]['items'])


def active_control(info, successes, timestamp):
    if not isinstance(info, dict) or info.get('status') not in successes:
        return False
    if info.get('status') == 'DEAL_SUCCESS':
        return True
    expiry = info.get('expireTime')
    return info.get('isPermanent') is True or type(expiry) is int and expiry > timestamp


def entities(kind, value, timestamp):
    data = value.get('data')
    items = response_items(data)
    if len(items) > 200 or any(not isinstance(item, dict) for item in items):
        raise ContractError('实体证据结构无效或超过分析预算')
    # No names, command lines, payloads or arbitrary prose enter the decision.
    if kind == 'host':
        return {'count': len(items), 'isolated': bool(items) and active_control(
            data.get('edrDealStatusInfo'), {'ISOLATE_SUCCESS'}, timestamp), 'items': []}
    records = []
    for item in items:
        level = item.get('threatLevel')
        status = item.get('edrDealStatusInfo') if kind in ('file', 'process', 'dns') else item.get('ndrDealStatusInfo')
        success = {'DEAL_SUCCESS'} if kind == 'file' else {'BLOCK_SUCCESS'}
        records.append({'level': level if type(level) is int and level in {0, 1, 2, 3} else None,
                        'controlled': active_control(status, success, timestamp)})
    return {'count': len(items), 'items': records}


def select_status(raw, responses, *, timestamp=None, failures=()):
    timestamp = int(time()) if timestamp is None else timestamp
    evidence = {'gptResult': raw.get('gptResult') if type(raw.get('gptResult')) is int else None,
                'failedQueries': list(failures), 'entities': {}}
    for kind in ENTITY_TYPES:
        try:
            evidence['entities'][kind] = entities(kind, responses[kind], timestamp)
        except (KeyError, ContractError, AttributeError):
            if kind not in evidence['failedQueries']:
                evidence['failedQueries'].append(kind)
    if evidence['failedQueries']:
        return Decision(10, '实体证据不完整，标为处置中并继续跟进', evidence)
    groups = evidence['entities']
    assessed = [item for kind in ('file', 'process', 'ip', 'dns') for item in groups[kind]['items']]
    malicious = [item for item in assessed if item['level'] == 3]
    unknown = any(item['level'] not in {1, 3} for item in assessed)
    conclusion = evidence['gptResult']
    definitions = raw.get('threatDefineName')
    business = isinstance(definitions, list) and '业务行为' in definitions
    benign = bool(assessed) and all(item['level'] == 1 for item in assessed)
    # A low severity, an absent entity or an arbitrary reason is not benign proof.
    if conclusion in {40, 160} and not malicious and not unknown and (business or benign):
        return Decision(60, 'XDR 明确研判为误报，且业务定性或实体安全证据一致，标为忽略', evidence)
    files = [item for item in groups['file']['items'] if item['level'] == 3]
    other_threats = any(item['level'] != 1 for kind in ('process', 'ip', 'dns') for item in groups[kind]['items'])
    if (conclusion in {20, 120} and files and not unknown and not other_threats
            and not groups['innerip']['count'] and all(item['controlled'] for item in files)):
        return Decision(40, '病毒事件的恶意文件均已处置，其他已返回实体无未解决威胁，标为处置完成', evidence)
    inner_controlled = all(item['controlled'] for item in groups['innerip']['items'])
    if (conclusion in {10, 20, 110, 115, 120} and groups['host']['isolated']
            or malicious and not unknown and inner_controlled and all(item['controlled'] for item in malicious)):
        return Decision(70, 'XDR 已确认主机隔离或相关恶意实体均受控，标为已遏制；仍需跟进根因', evidence)
    return Decision(10, '尚无充分的完成、遏制或误报证据，标为处置中并继续跟进', evidence)

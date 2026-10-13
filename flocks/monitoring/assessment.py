"""One evidence-bound decision for event presentation and owner notification.

The local display outcome never authorizes an XDR status write. Source scores,
incident labels and missing data are not independent proof of a security event.
"""
import json
import re

from .event_details import redact_text

STATUSES = ('risk_open', 'risk_closed', 'benign', 'insufficient')
LABELS = {'risk_open': '有风险 · 处置中', 'risk_closed': '有风险 · 已处置',
          'benign': '无风险 · 已忽略', 'insufficient': '证据不足 · 暂忽略'}
# Tiny field-presence projections use the same keys as the original raw facts.
FACT_KEYS = {
    'srcIp', 'srcIps', 'sourceIp', 'src_ip', 'net.src_ip', 'dstIp', 'dstIps',
    'destIp', 'destinationIp', 'dst_ip', 'net.dest_ip', 'srcPort', 'sourcePort',
    'dstPort', 'destPort', 'destinationPort', 'protocol', 'domain', 'domainName',
    'hostDomain', 'destDomain', 'dstDomain', 'url', 'requestUrl', 'uri',
    'requestHead', 'requestHeader', 'requestHeaders', 'request_head', 'request_headers',
    'requestBody', 'request_body', 'responseBody', 'response_body', 'commandLine',
    'cmdLine', 'processCommandLine', 'processName', 'pName', 'parentProcessName',
    'fileName', 'filePath', 'path', 'fileMd5', 'fileMD5', 'md5', 'fileHash', 'sha256',
    'virusName', 'malwareName', 'threatName', 'fileCreateTime', 'fileCreatedTime',
    'fileModifyTime', 'userName', 'parentPath', 'intelligenceTag',
}


def _decoded(value, default):
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, type(default)) else default
        except (ValueError, TypeError, RecursionError):
            return default
    return value if isinstance(value, type(default)) else default


def _text(value, limit=900):
    return redact_text(value)[:limit] if isinstance(value, str) else ''


def fact_fields(facts):
    """Presence of concrete observations, with bounded traversal and no prose."""
    found, stack, visited = set(), [facts], 0
    while stack and visited < 6000:
        item = stack.pop()
        visited += 1
        if isinstance(item, dict):
            for key, value in item.items():
                if key in FACT_KEYS and isinstance(value, (str, int, float, list)) and value not in ('', [], None):
                    found.add(key)
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(item[:300])
    return found


def _proof_fields(proof):
    supplied = proof.get('fact_fields')
    return ({key for key in supplied if isinstance(key, str) and key in FACT_KEYS}
            if isinstance(supplied, list) else fact_fields(proof.get('facts')))


def has_behavior_facts(proof):
    """Exclude labels-only results; the investigator determines their meaning."""
    fields = _proof_fields(proof)
    source = fields & {'srcIp', 'srcIps', 'sourceIp', 'src_ip', 'net.src_ip'}
    destination = fields & {'dstIp', 'dstIps', 'destIp', 'destinationIp', 'dst_ip', 'net.dest_ip'}
    context = fields & {'srcPort', 'sourcePort', 'dstPort', 'destPort', 'destinationPort', 'protocol', 'domain', 'domainName'}
    packet = fields & {'requestBody', 'request_body', 'requestHead', 'requestHeader', 'requestHeaders', 'request_head', 'request_headers'}
    command = fields & {'commandLine', 'cmdLine', 'processCommandLine'}
    file_identity = fields & {'fileName', 'filePath', 'path', 'fileMd5', 'fileMD5', 'md5', 'fileHash', 'sha256'}
    file_observation = fields & {'virusName', 'malwareName', 'threatName', 'fileCreateTime', 'fileCreatedTime', 'fileModifyTime', 'processName', 'pName'}
    return bool(source and destination and context or packet or command or file_identity and file_observation)


def _requires_dga_facts(event):
    name = event.get('name') if isinstance(event.get('name'), str) else ''
    return bool(re.search(r'(?<![a-z0-9])dga(?![a-z0-9])|随机域名|域名生成算法', name, re.I))


def _has_dga_material(proof):
    """A DNS endpoint and source tags cannot demonstrate generated domains.

    These observations are necessary investigation material, not a malicious
    verdict: actual domains/query payloads still require a high-confidence,
    evidence-cited model judgment. A concrete infected file or process may
    independently establish the incident even if DNS names are unavailable.
    """
    fields = _proof_fields(proof)
    domains = fields & {'domain', 'domainName', 'hostDomain', 'destDomain', 'dstDomain'}
    query_payload = fields & {'requestBody', 'request_body'}
    file_identity = fields & {'fileName', 'filePath', 'path', 'fileMd5', 'fileMD5', 'md5', 'fileHash', 'sha256'}
    malicious_file = file_identity and fields & {'virusName', 'malwareName', 'threatName'}
    process_observation = (fields & {'commandLine', 'cmdLine', 'processCommandLine'}
                           and fields & {'processName', 'pName'})
    return bool(domains or query_payload or malicious_file or process_observation)


def same_evidence_version(first, second):
    """Status feedback alone does not erase the original risk evidence.

    All incident identity/activity fields still use the canonical fingerprint.
    Proofs retain the original fingerprint; no evidence is rebound to a newer
    incident merely to make a notification eligible.
    """
    from .investigation import fingerprint
    if not isinstance(first, dict) or not isinstance(second, dict):
        return False
    if not first.get('id') or not first.get('device') or first.get('id') != second.get('id') or first.get('device') != second.get('device'):
        return False
    def activity(event):
        value = {**event, 'dealStatus': None}
        if isinstance(value.get('basics'), dict):
            value['basics'] = {key: item for key, item in value['basics'].items()
                               if key not in {'deal_status', 'dealStatus'}}
        return value
    return fingerprint(activity(first)) == fingerprint(activity(second))


def assess_event(event, investigation=None, evidence=None, *, handling=None):
    """Return four business outcomes; only confirmed current risks notify.

    ``investigation`` may be the persisted row (JSON strings are accepted) or
    an attached runtime result carrying ``event_revision``. Supplementary gaps
    and partial queries do not invalidate independently supported risk facts;
    the model must explicitly identify uncertainty that blocks its conclusion.
    """
    from .investigation import fingerprint
    event = event if isinstance(event, dict) else {}
    case = investigation if isinstance(investigation, dict) else event.get('investigation', {})
    case = case if isinstance(case, dict) else {}
    result = _decoded(case.get('result'), {}) if 'result' in case else case
    canonical = _decoded(case.get('event'), {})
    version = fingerprint(event)
    current = same_evidence_version(canonical, event) if canonical else case.get('event_revision') == version
    proof_version = fingerprint(canonical) if canonical and current else version
    proofs = _decoded(evidence, []) if evidence is not None else _decoded(case.get('evidence'), [])
    if not proofs and isinstance(case.get('assessment_proofs'), str):
        proofs = _decoded(case['assessment_proofs'], [])
    reason = _text(result.get('reason')) or '尚未取得足够证据形成明确结论。'
    blocking = [text for item in result.get('blocking_gaps', [])[:12] if (text := _text(item, 300))] if isinstance(result.get('blocking_gaps'), list) else []
    refs = result.get('evidence_ids') if isinstance(result.get('evidence_ids'), list) else []
    requested = [ref for ref in refs if isinstance(ref, str) and ref]
    known = {proof.get('id'): proof for proof in proofs if isinstance(proof, dict) and isinstance(proof.get('id'), str)}
    bound = [known[ref] for ref in requested if ref in known and known[ref].get('success') in (True, 1)
             and known[ref].get('event') == event.get('id') and known[ref].get('event_revision') == proof_version]
    cited = bool(requested) and len(requested) == len(refs) and len(bound) == len(requested)
    if any(sum(isinstance(proof, dict) and proof.get('id') == ref for proof in proofs) != 1 for ref in requested):
        cited = False
    supported = cited and any(has_behavior_facts(proof) for proof in bound)
    dga_missing = bool(supported and _requires_dga_facts(event) and not any(_has_dga_material(proof) for proof in bound))
    if dga_missing:
        supported = False
    verdict = result.get('verdict') if isinstance(result.get('verdict'), str) else 'unknown'
    consistent = bool(_text(result.get('reason'))) and isinstance(result.get('blocking_gaps'), list)
    if verdict == 'benign' and result.get('risk_confirmed') is True:
        consistent = False
        blocking.append('结论同时声称无风险与已证实风险，当前判断相互矛盾。')
    eligible = (case.get('state') == 'ready' and current and cited and supported and consistent
                and result.get('confidence') == 'high' and not blocking)
    if not current:
        blocking.insert(0, '当前事件与已保存的调查版本不一致，需要重新核对。')
    elif case.get('state') != 'ready':
        blocking.insert(0, '调查尚未形成最终结论。')
    elif not cited:
        blocking.insert(0, '结论缺少与当前事件对应的成功查询依据。')
    elif dga_missing:
        blocking.insert(0, '现有证据只显示普通 DNS 连通或来源标签；缺少实际查询域名、查询载荷或独立主机侧恶意行为，尚不能确认 DGA 风险。')
    elif not supported:
        blocking.insert(0, '现有依据仅含来源标签、评分或实体标识，尚未证实具体安全行为。')
    elif result.get('confidence') != 'high':
        blocking.insert(0, '现有证据不足以形成高置信度结论。')
    risk = eligible and verdict == 'risk' and result.get('risk_confirmed') is True
    benign = eligible and verdict == 'benign'
    # A source ignore mark alone is neither a benign verdict nor proof that a
    # real risk was handled. Closure requires completed status/read-back facts.
    closed = handling == 'closed' if handling is not None else type(event.get('dealStatus')) is int and event['dealStatus'] == 40
    status = ('risk_closed' if closed else 'risk_open') if risk else 'benign' if benign else 'insufficient'
    if not risk and not benign and not blocking:
        blocking.append('尚未明确证实存在风险，也未取得可确认无风险的依据。')
    if not risk and not benign:
        reason = '证据不足，暂不通知责任人；尚未排除风险。' + (blocking[0] if blocking else reason)
    return {'policy_version': 1, 'event_revision': version, 'status': status, 'label': LABELS[status], 'verdict': 'risk' if risk else 'benign' if benign else 'unknown',
            'notify_eligible': bool(risk and not closed), 'reason': reason, 'gaps': list(dict.fromkeys(blocking))[:6],
            'is_current': bool(current), 'evidence_supported': bool(supported), 'evidence_ids': [p['id'] for p in bound],
            'ignored_locally': status in {'benign', 'insufficient'},
            'risk_excluded': bool(benign)}

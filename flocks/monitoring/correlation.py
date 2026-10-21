"""Bounded, version-bound cross-product query results, without a risk verdict."""
from .event_details import redact_text, safe_facts
from .evidence_presenter import _bounded, bound_evidence, evidence_cards

MAX_RESULTS = 24
_LISTS = {'item', 'items', 'list', 'records', 'rows'}
_ENVELOPES = {'data', 'result'}
_META = {'total', 'count', 'code', 'status', 'success', 'message', 'msg', 'response_code',
         'partial', 'truncated', 'incomplete', '_monitor_partial', '_evidenceLimits'}


def product_of(proof):
    """Use controlled product/tool identifiers, never a device display name."""
    tool = str(proof.get('tool', ''))
    if tool.startswith('tdp_'):
        return 'tdp'
    if tool.startswith('onesig_'):
        return 'sig'
    return proof.get('product') if proof.get('product') in {'tdp', 'sig'} else 'unknown'


def response_metadata(raw, product):
    """Measure explicit response envelopes before safe_facts drops metadata.

    Lists are measured without traversing records. Only known envelope paths
    are examined; a business field called total inside a record is not a query
    count. Unknown objects and contradictory counts never establish no-match.
    """
    if product not in {'tdp', 'sig'}:
        return {'schema': 1, 'explicit_empty': False, 'partial': False, 'has_content': False}
    counts, totals = [], []
    partial, content, opaque = False, False, False
    pending = [(raw, 0)]
    visits = 0
    while pending:
        value, depth = pending.pop()
        visits += 1
        if visits > 16 or depth > 3:
            partial = True
            break
        if isinstance(value, list):
            counts.append(len(value))
            content |= bool(value)
            continue
        if not isinstance(value, dict):
            opaque |= value not in (None, '', False)
            continue
        partial |= any(value.get(key) is True for key in ('partial', 'truncated', 'incomplete', '_monitor_partial'))
        partial |= bool(value.get('_evidenceLimits'))
        for key in ('total', 'count'):
            if type(value.get(key)) is int and value[key] >= 0:
                totals.append(value[key])
        for key, nested in list(value.items())[:200]:
            if key in _LISTS and isinstance(nested, list):
                counts.append(len(nested))
                content |= bool(nested)
            elif key in _LISTS:
                partial, opaque = True, True
            elif key in _ENVELOPES and isinstance(nested, (dict, list)):
                pending.append((nested, depth+1))
            elif key not in _META and key not in _LISTS:
                opaque |= nested not in (None, '', [], {})
                content |= nested not in (None, '', [], {})
        if len(value) > 200:
            partial = True
    returned = max(counts) if counts else None
    total = max(totals) if totals else None
    inconsistent = len(set(totals)) > 1 or total == 0 and (bool(returned) or opaque)
    partial |= total is not None and returned is not None and total > returned
    explicit_empty = (returned == 0 or total == 0) and not (content or opaque or partial or inconsistent)
    result = {'schema': 1, 'explicit_empty': bool(explicit_empty), 'partial': bool(partial),
              'has_content': bool(content)}
    if returned is not None:
        result['returned_count'] = returned
    if total is not None:
        result['total_count'] = total
    if inconsistent:
        result['inconsistent'] = True
    return result


def _text(value, size=400):
    return redact_text(value if isinstance(value, (str, int, float)) else '')[:size]


def _metadata(proof, facts, product):
    supplied = proof.get('result_meta')
    if not isinstance(supplied, dict) or type(supplied.get('schema')) is not int or supplied['schema'] != 1:
        fallback = response_metadata(facts, product)
        # Legacy facts have already passed through an older whitelist. Missing
        # fields or empty containers cannot prove the original response empty.
        return {'schema': 1, 'explicit_empty': False, 'partial': fallback['partial'],
                'has_content': fallback['has_content']}
    clean = {'schema': 1}
    for key in ('explicit_empty', 'partial', 'inconsistent', 'has_content'):
        clean[key] = supplied.get(key) is True
    for key in ('returned_count', 'total_count'):
        if type(supplied.get(key)) is int and 0 <= supplied[key] <= 10**12:
            clean[key] = supplied[key]
        elif key in supplied:
            clean['inconsistent'] = True
    # Even persisted program metadata cannot override contradictory safe facts.
    fallback = response_metadata(facts, product)
    clean['inconsistent'] |= bool(clean.get('explicit_empty') and fallback.get('has_content'))
    clean['inconsistent'] |= clean.get('total_count') == 0 and bool(clean.get('returned_count'))
    counts = [clean[key] for key in ('returned_count', 'total_count') if key in clean]
    clean['inconsistent'] |= bool(clean['explicit_empty'] and (
        clean['has_content'] or 0 not in counts or any(count > 0 for count in counts)))
    if 'returned_count' in clean and 'total_count' in clean:
        clean['partial'] |= clean['total_count'] > clean['returned_count']
    clean['partial'] |= fallback['partial']
    return clean


def correlation_results(event, evidence, *, canonical=None, plan=None, cards=None):
    """One row per actual query; planned gaps have no invented evidence IDs."""
    from .assessment import same_evidence_version
    from .investigation import fingerprint

    event = event if isinstance(event, dict) else {}
    canonical = canonical if isinstance(canonical, dict) else event
    if not same_evidence_version(canonical, event):
        return []
    revision = fingerprint(canonical)
    proofs = bound_evidence(event, evidence, canonical=canonical, include_failed=True)
    cards = cards if isinstance(cards, list) else evidence_cards(event, evidence, canonical=canonical)
    rows, queried = [], set()
    limited_input = (isinstance(evidence, list) and len(evidence) > 24
                     or isinstance(plan, list) and len(plan) > 24)
    for index, proof in enumerate(proofs):
        product = product_of(proof)
        if proof.get('kind') != 'related' and product == 'unknown':
            continue
        source = _text(proof.get('source_name') or proof.get('device_name') or proof.get('tool') or proof['device'], 160)
        facts, clipped = safe_facts(proof.get('facts', {}))
        meta = _metadata(proof, facts, product)
        limited_facts = _bounded(facts, 8000)
        query = proof.get('query') if isinstance(proof.get('query'), dict) else {}
        limited_query = _bounded(query, 8000)
        partial = bool(proof.get('partial') or clipped or meta.get('partial')
                       or limited_facts != facts or limited_query != query)
        ref = _text(proof.get('id'), 100)
        has_card = any(card.get('evidence_id') == ref or any(record.get('evidence_id') == ref
                       for record in card.get('records', []) if isinstance(record, dict))
                       for card in cards if isinstance(card, dict)) if ref else False
        if proof.get('success') not in (True, 1):
            status, summary = 'failed', '关联查询未成功；不能据此排除风险。'
        elif meta.get('explicit_empty') and not partial and not meta.get('inconsistent'):
            status = 'no_match'
            summary = '本次资产查询未匹配记录；不代表没有安全威胁。' if product == 'sig' else '本次查询范围内未匹配记录；不代表没有风险。'
        elif has_card and product != 'sig':
            status, summary = 'candidate', '取得候选关联记录，需核对是否为同一资产及同一时间范围。'
        elif meta.get('has_content') or meta.get('returned_count') or meta.get('total_count') or facts:
            status = 'insufficient'
            summary = ('取得资产或策略资料；当前接口不提供威胁日志，不能据此确认阻断或排除风险。'
                       if product == 'sig' else '查询成功，但现有资料不足以核对告警关联。')
        else:
            status, summary = 'unknown', '查询结果结构或返回数量未知；不能将缺少可展开事实视为无匹配。'
        boundary = ('跨产品查询为候选关联，须核对是否为同一资产及同一时间范围。'
                    if product != 'sig' else 'OneSIG 当前接口仅核对资产或策略资料，不代表取得威胁处置记录。')
        if partial:
            boundary += ' 仅取得或展示部分结果，不能据此排除风险。'
        if meta.get('inconsistent'):
            boundary += ' 来源计数与记录不一致，未按无匹配处理。'
        row = {'id': f'correlation-{ref or index+1}', 'evidence_id': ref, 'device': _text(proof['device'], 160),
               'source': source, 'product': product, 'kind': _text(proof.get('kind'), 50),
               'status': status, 'summary': summary, 'partial': partial, 'boundary': boundary,
               'query': limited_query, 'facts': limited_facts}
        for key in ('returned_count', 'total_count'):
            if key in meta:
                row[key] = meta[key]
        host = query.get('host') or event.get('host')
        if host:
            row['host'] = _text(host, 200)
        start, end = query.get('time_from'), query.get('time_to')
        if start is not None or end is not None:
            row['window'] = _bounded({'start': start, 'end': end}, 1000)
        if proof.get('error'):
            row['error'] = _text(proof['error'], 600)
        rows.append(row)
        queried.add((proof['device'], proof.get('tool')))
    for index, entry in enumerate(plan[:24] if isinstance(plan, list) else []):
        if (not isinstance(entry, dict) or entry.get('event') != event.get('id')
                or entry.get('event_device') != event.get('device') or entry.get('event_revision') != revision
                or entry.get('status') not in {'not_queried', 'unavailable', 'skipped'}):
            continue
        product = product_of(entry)
        if (entry.get('device'), entry.get('tool')) in queried:
            continue
        row = {'id': f'correlation-plan-{index+1}', 'device': _text(entry.get('device'), 160),
               'source': _text(entry.get('source') or entry.get('name') or entry.get('device') or '未选择数据源', 160),
               'product': product, 'kind': 'related', 'status': entry['status'],
               'summary': _text(entry.get('reason') or '本版本未取得该数据源的关联查询结果。', 600),
               'partial': False, 'boundary': '尚无本次查询结果，不能据此排除风险。', 'query': {}, 'facts': {}}
        if entry.get('host'):
            row['host'] = _text(entry['host'], 200)
        if isinstance(entry.get('window'), dict):
            row['window'] = _bounded({key: entry['window'].get(key) for key in ('start', 'end')}, 1000)
        rows.append(row)
    if len(rows) > MAX_RESULTS:
        rows = rows[:MAX_RESULTS]
        limited_input = True
    if rows and limited_input:
        rows[-1]['partial'] = True
        rows[-1]['boundary'] += ' 关联查询处理或展示达到条数上限，后续条目未全部核对或展示。'
    return rows

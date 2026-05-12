
import os
import re
import sys
import json
import time
import pickle
import hashlib
import datetime
import traceback
from pathlib import Path

from datasketch import MinHash, MinHashLSH

IS_WINDOWS = sys.platform == 'win32'
if IS_WINDOWS:
    import msvcrt  # noqa: F401
else:
    import fcntl   # noqa: F401

WORKFLOW_NAME = 'tdp_alert_pull_dedup'
MINHASH_SEED = 2024
NUM_PERM = 128
LSH_CLUSTER_WARN_THRESHOLD = 100000
MAX_RECORDS_PER_FILE = 10000
_JSONL_PREFIX = 'alerts'

HTTP_METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'OPTIONS', 'PATCH', 'TRACE']

TDP_FIELD_MAP = {
    'customer_uuid': 'customer_uuid',
    'device_id':     'device_id',
    'id':            'id',
    'time':          'time',
    'direction':     'direction',
    'sip':           'net_real_src_ip',
    'dip':           'net_dest_ip',
    'sport':         'net_src_port',
    'dport':         'net_dest_port',
    'net_type':      'net_type',
    'net_app_proto': 'net_app_proto',
    'req_http_url':  'net_http_url',
    'req_user_agent':'net_http_reqs_user_agent',
    'req_host':      'net_http_reqs_host',
    'req_line':      'net_http_reqs_line',
    'req_header':    'net_http_reqs_header',
    'req_body':      'net_http_reqs_body',
    'req_cookie':    'net_http_reqs_cookie',
    'req_body_len':  'net_http_reqs_content_length',
    'rsp_status_code': 'net_http_status',
    'rsp_line':      'net_http_resp_line',
    'rsp_header':    'net_http_resp_header',
    'rsp_body':      'net_http_resp_body',
    'rsp_body_len':  'net_http_resp_content_length',
    'net_bytes_toclient': 'net_bytes_toclient',
    'net_bytes_toserver': 'net_bytes_toserver',
    'threat_rule_id':    'threat_suuid',
    'threat_name':       'threat_name',
    'threat_msg':        'threat_msg',
    'threat_ioc':        'threat_ioc',
    'threat_level':      'threat_level',
    'threat_severity':   'threat_severity',
    'threat_phase':      'threat_phase',
    'threat_type':       'threat_type',
    'threat_result':     'threat_result',
    'threat_confidence': 'threat_confidence',
    'connection_established': 'established',
    'asset_group_name':  'dest_assets_group_name',
    'asset_name':        'dest_assets_latestName',
}

NEED_ANALYSIS = {
    'alert_not_scan_http_direction_in',
    'alert_not_scan_http_direction_out',
    'alert_not_scan_http_direction_lateral',
}

# ── Paths ─────────────────────────────────────────────────────────────────────

def get_workflow_root():
    from flocks.config import Config
    flocks_root = Config().get_global().data_dir.parent  # ~/.flocks
    root = Path(flocks_root) / 'workflows' / WORKFLOW_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_state_paths(threshold):
    base = str(get_workflow_root() / f'lsh_state_np{NUM_PERM}_th{int(threshold * 100)}')
    return base + '.pkl', base + '.lock'


def get_cursor_path():
    return str(get_workflow_root() / 'cursor.json')


def get_output_dir(now):
    date_str = now.strftime('%Y-%m-%d')
    out_dir = get_workflow_root() / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    return str(out_dir)


# ── File locking ──────────────────────────────────────────────────────────────

def acquire_lock(lock_path):
    fh = open(lock_path, 'w+')
    if IS_WINDOWS:
        fh.write('L'); fh.flush(); fh.seek(0)
        while True:
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1); break
            except OSError:
                continue
    else:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    return fh


def release_lock(fh):
    try:
        if IS_WINDOWS:
            try:
                fh.seek(0); msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


# ── LSH state ─────────────────────────────────────────────────────────────────

def load_state(state_path, threshold):
    if not state_path or not os.path.exists(state_path) or os.path.getsize(state_path) == 0:
        return None, None, None, 0
    try:
        with open(state_path, 'rb') as f:
            state = pickle.load(f)
        if state.get('num_perm') != NUM_PERM or state.get('threshold') != threshold:
            print(f'[dedup] state params mismatch, starting fresh')
            return None, None, None, 0
        cache = state['lsh_cache']
        seen_raw = state.get('dedup_key_cache', {})
        seen = {k: None for k in seen_raw} if isinstance(seen_raw, set) else (seen_raw if isinstance(seen_raw, dict) else {})
        next_cid = state.get('next_cluster_id') or ((max(cache.keys()) + 1) if cache else 0)
        return state['lsh_index'], cache, seen, next_cid
    except Exception as e:
        print(f'[dedup] failed to load state ({e}), starting fresh')
        return None, None, None, 0


def evict_oldest(lsh_index, lsh_cache, dedup_key_cache, max_keys):
    evicted_keys = evicted_clusters = 0
    excess = len(dedup_key_cache) - max_keys
    if excess > 0:
        for k in list(dedup_key_cache.keys())[:excess]:
            del dedup_key_cache[k]
        evicted_keys = excess
    excess = len(lsh_cache) - max_keys
    if excess > 0:
        for cid in list(lsh_cache.keys())[:excess]:
            try: lsh_index.remove(cid)
            except (KeyError, ValueError): pass
            del lsh_cache[cid]
        evicted_clusters = excess
    return evicted_keys, evicted_clusters


def dump_state_atomic(state_path, lsh_index, lsh_cache, dedup_key_cache, threshold, next_cluster_id):
    tmp = state_path + '.tmp'
    try:
        state = {
            'lsh_index': lsh_index, 'lsh_cache': lsh_cache,
            'dedup_key_cache': dedup_key_cache, 'next_cluster_id': next_cluster_id,
            'num_perm': NUM_PERM, 'threshold': threshold,
        }
        with open(tmp, 'wb') as f:
            pickle.dump(state, f); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, state_path)
    except Exception as e:
        print(f'[dedup] failed to save state: {e}')
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except Exception: pass


# ── Cursor ────────────────────────────────────────────────────────────────────

def load_cursor():
    p = get_cursor_path()
    if not os.path.exists(p):
        return None
    try:
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def save_cursor(cursor):
    p = get_cursor_path()
    tmp = p + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cursor, f, ensure_ascii=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception as e:
        print(f'[cursor] save failed: {e}')


# ── Normalize ─────────────────────────────────────────────────────────────────

def flatten_dict(d, prefix=''):
    res = {}
    for k, v in d.items():
        if isinstance(v, dict):
            res.update(flatten_dict(v, f'{prefix}{k}_'))
        else:
            res[f'{prefix}{k}'] = v
    return res


def normalize_single(alert):
    import uuid as _uuid
    if not isinstance(alert, dict):
        return None
    flat = flatten_dict(alert)
    norm = {}
    for std_key, raw_key in TDP_FIELD_MAP.items():
        norm[std_key] = flat.get(raw_key, 'none') if raw_key != 'none' else 'none'
    if norm.get('id') in ('none', None, ''):
        norm['id'] = str(_uuid.uuid3(_uuid.NAMESPACE_DNS, ''.join(str(v) for v in norm.values())))
    if norm.get('net_type') in ('none', None, ''):
        method = flat.get('method', 'none')
        norm['net_type'] = 'http' if method in HTTP_METHODS else ('none' if method == 'none' else 'other')
    norm['_source_type'] = 'tdp'
    return norm


# ── Filter ────────────────────────────────────────────────────────────────────

def is_scan_alert(threat_name):
    tnl = str(threat_name or '').lower()
    return ('扫描' in tnl) and ('webshell' not in tnl)


def is_http(alert):
    for field in ('application_layer_protocol', 'net_type', 'net_app_proto'):
        val = str(alert.get(field, '') or '').lower()
        if val and val != 'none' and 'http' in val:
            return True
    return False


def get_process_type(alert):
    threat_name = alert.get('threat_name', '')
    direction = str(alert.get('direction', '') or '').lower()
    scan = is_scan_alert(threat_name)
    http = is_http(alert)
    if scan:
        return f'alert_scan_direction_{direction}' if direction in ('in', 'out', 'lateral') else 'alert_scan_direction_in'
    if http:
        return f'alert_not_scan_http_direction_{direction}' if direction in ('in', 'out', 'lateral') else 'alert_not_scan_http_direction_in'
    return f'alert_not_scan_not_http_direction_{direction}' if direction in ('in', 'out', 'lateral') else 'alert_not_process'


# ── Dedup helpers ─────────────────────────────────────────────────────────────

def normalize_uri(uri):
    uri = str(uri or '')
    uri = re.sub(r'\d{4}-\d{2}-\d{2}', 'DATETIME', uri)
    uri = re.sub(r'[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}', 'UUID', uri, flags=re.IGNORECASE)
    uri = re.sub(r'(\.\./)+', 'TRAVERSAL', uri)
    uri = re.sub(r'\bNULL\b', 'NULL_REPLACED', uri)
    uri = re.sub(r'chr\$\d+\$\|\|chr\$\d+\$', 'CHR_SEQUENCE', uri)
    uri = re.sub(r'\b\d+={1,2}\d+\b', 'NUMBER_COMPARISON', uri)
    uri = re.sub(r'\b[a-fA-F0-9]{32}\b', 'HEXADECIMAL CHARACTERS', uri)
    return uri


def gen_minhash(text, permutations):
    shingles = [text[i:i+5] for i in range(len(text) - 4)]
    m = MinHash(num_perm=NUM_PERM, seed=MINHASH_SEED, permutations=permutations)
    for s in shingles:
        m.update(s.encode('utf-8'))
    return m


# ── TDP response unwrapping ───────────────────────────────────────────────────

def extract_alerts_from_response(resp):
    """tdp_log_search returns the inner 'data' field from TDP API (already unwrapped).

    Tolerate several shapes:
      - list  →  used as-is
      - dict with 'log' / 'logs' / 'list' / 'data' / 'records' / 'items' key
        (possibly nested one level)
    """
    if resp is None:
        return []
    if isinstance(resp, list):
        return list(resp)
    if isinstance(resp, dict):
        for key in ('log', 'logs', 'list', 'data', 'records', 'items', 'hits'):
            v = resp.get(key)
            if isinstance(v, list):
                return list(v)
            if isinstance(v, dict):
                for sub in ('list', 'data', 'records', 'items', 'hits'):
                    sv = v.get(sub)
                    if isinstance(sv, list):
                        return list(sv)
    return []


# ── JSONL writer ──────────────────────────────────────────────────────────────

def _count_alert_lines(file_path):
    count = 0
    try:
        with open(file_path, 'r', encoding='utf-8') as _f:
            for _line in _f:
                _s = _line.strip()
                if _s and '"_type"' not in _s:
                    count += 1
    except Exception:
        pass
    return count


def _find_active_file(out_dir):
    import glob
    pattern = os.path.join(out_dir, _JSONL_PREFIX + '_*.jsonl')
    existing = sorted(glob.glob(pattern))
    if not existing:
        return None, 0, 0
    latest = existing[-1]
    basename = os.path.basename(latest)
    try:
        seq = int(basename.replace(_JSONL_PREFIX + '_', '').replace('.jsonl', ''))
    except ValueError:
        seq = len(existing)
    count = _count_alert_lines(latest)
    return latest, count, seq


def _write_jsonl(out_dir, alerts, now):
    written = []
    active_path, active_count, seq = _find_active_file(out_dir)
    remaining = list(alerts)
    while remaining:
        available = MAX_RECORDS_PER_FILE - active_count
        if available <= 0 or active_path is None:
            seq += 1
            active_path = os.path.join(out_dir, f'{_JSONL_PREFIX}_{seq:03d}.jsonl')
            active_count = 0
            available = MAX_RECORDS_PER_FILE
            header = {
                '_type': 'file_header',
                'created_at': now.isoformat(),
                'date': now.strftime('%Y-%m-%d'),
                'workflow': WORKFLOW_NAME,
                'seq': seq,
            }
            with open(active_path, 'w', encoding='utf-8') as _hf:
                _hf.write(json.dumps(header, ensure_ascii=False) + '\n')
        batch = remaining[:available]
        remaining = remaining[available:]
        with open(active_path, 'a', encoding='utf-8') as _af:
            for _alert in batch:
                _af.write(json.dumps(_alert, ensure_ascii=False, default=str) + '\n')
        active_count += len(batch)
        if active_path not in written:
            written.append(active_path)
        if remaining:
            active_path = None
            active_count = 0
    return written


# ── Inputs ────────────────────────────────────────────────────────────────────

pull_interval_s    = float(inputs.get('pull_interval_s', 60))
initial_lookback_s = int(inputs.get('initial_lookback_s', 300))
max_iterations     = int(inputs.get('max_iterations', 0))   # 0 = infinite
max_runtime_s      = float(inputs.get('max_runtime_s', 0))  # 0 = no time limit
batch_size         = int(inputs.get('batch_size', 1000))
net_data_types     = inputs.get('net_data_types', ['attack'])
if isinstance(net_data_types, str):
    net_data_types = [s.strip() for s in net_data_types.split(',') if s.strip()]
sql_filter         = str(inputs.get('sql', "threat.level = 'attack'") or "threat.level = 'attack'")
assets_group       = inputs.get('assets_group') or []
filter_enabled     = bool(inputs.get('filter_enabled', True))
dedup_enabled      = bool(inputs.get('dedup_enabled', True))
threshold          = float(inputs.get('threshold', 0.7))
strict_fields      = inputs.get('strict_fields', ['sip', 'dip'])
lsh_fields         = inputs.get('lsh_fields', ['req_http_url', 'req_body', 'rsp_body'])
max_field_len      = int(inputs.get('max_field_len', 500))
max_dedup_keys     = int(inputs.get('max_dedup_keys', 100000))
reset_cursor       = bool(inputs.get('reset_cursor', False))
log_progress_every = max(1, int(inputs.get('log_progress_every', 1)))

if max_dedup_keys < 1:
    max_dedup_keys = 100000
if pull_interval_s < 0.1:
    pull_interval_s = 0.1
if batch_size < 1:
    batch_size = 1
if batch_size > 10000:
    batch_size = 10000

print(f'[init] workflow={WORKFLOW_NAME}')
print(f'[init] pull_interval_s={pull_interval_s}, initial_lookback_s={initial_lookback_s}, '
      f'batch_size={batch_size}, max_iterations={max_iterations}, max_runtime_s={max_runtime_s}')
print(f'[init] sql={sql_filter!r}, net_data_types={net_data_types}, assets_group={list(assets_group) if assets_group else []}')
print(f'[init] filter_enabled={filter_enabled}, dedup_enabled={dedup_enabled}, '
      f'threshold={threshold}, max_dedup_keys={max_dedup_keys}')
print(f'[init] output_root={get_workflow_root()}')

# ── Cursor init ───────────────────────────────────────────────────────────────

now_ts = int(time.time())
if reset_cursor:
    cur = None
    print('[cursor] reset_cursor=True, starting from initial_lookback_s')
else:
    cur = load_cursor()

if cur and isinstance(cur.get('next_from'), int):
    last_to = int(cur['next_from'])
    print(f'[cursor] resumed: next_from={last_to} ({datetime.datetime.fromtimestamp(last_to)})')
else:
    last_to = now_ts - initial_lookback_s
    print(f'[cursor] fresh start: next_from={last_to} ({datetime.datetime.fromtimestamp(last_to)})')

# ── MinHash permutations (init once) ──────────────────────────────────────────

_permutations = MinHash(num_perm=NUM_PERM, seed=MINHASH_SEED).permutations
state_path, lock_path = (get_state_paths(threshold) if dedup_enabled else (None, None))

# ── Aggregate stats ───────────────────────────────────────────────────────────

stats_all = {
    'iterations':       0,
    'pulls_succeeded':  0,
    'pulls_failed':     0,
    'raw_total':        0,
    'normalized_total': 0,
    'filtered_total':   0,
    'enriched_total':   0,
    'unique_total':     0,
    'duplicates_total': 0,
    'written_files':    [],
    'last_window_from': last_to,
    'last_window_to':   None,
    'last_error':       None,
}

start_t  = time.time()
iter_cnt = 0
stop_reason = 'completed'

# ── Main loop ─────────────────────────────────────────────────────────────────

try:
    while True:
        iter_cnt += 1
        stats_all['iterations'] = iter_cnt

        if max_iterations and iter_cnt > max_iterations:
            stop_reason = f'reached max_iterations={max_iterations}'
            print(f'[loop] {stop_reason}')
            break
        if max_runtime_s and (time.time() - start_t) > max_runtime_s:
            stop_reason = f'reached max_runtime_s={max_runtime_s}'
            print(f'[loop] {stop_reason}')
            break

        time_to_ts = int(time.time())
        time_from  = last_to
        if time_to_ts <= time_from:
            # window not advanced yet (e.g., very short pull_interval); sleep and retry
            time.sleep(pull_interval_s)
            continue

        stats_all['last_window_from'] = time_from
        stats_all['last_window_to']   = time_to_ts

        # ── Pull from TDP ─────────────────────────────────────────────────────
        tdp_kwargs = {
            'action':        'search',
            'time_from':     time_from,
            'time_to':       time_to_ts,
            'net_data_type': list(net_data_types),
            'sql':           sql_filter,
            'size':          batch_size,
        }
        if assets_group:
            tdp_kwargs['assets_group'] = list(assets_group)

        try:
            resp = tool.run('tdp_log_search', **tdp_kwargs)
            stats_all['pulls_succeeded'] += 1
        except Exception as _e:
            stats_all['pulls_failed'] += 1
            stats_all['last_error'] = f'tdp_log_search failed: {_e}'
            print(f'[pull] iter={iter_cnt}: tdp_log_search failed: {_e}')
            # Do NOT advance the cursor on failure: we'll retry the same window next round.
            time.sleep(pull_interval_s)
            continue

        raw_alerts = extract_alerts_from_response(resp)
        if iter_cnt % log_progress_every == 0:
            print(f'[pull] iter={iter_cnt}: window=[{time_from},{time_to_ts}] '
                  f'({datetime.datetime.fromtimestamp(time_from)} → '
                  f'{datetime.datetime.fromtimestamp(time_to_ts)}), raw={len(raw_alerts)}')
        stats_all['raw_total'] += len(raw_alerts)

        # ── Normalize ─────────────────────────────────────────────────────────
        normalized = []
        for a in raw_alerts:
            n = normalize_single(a)
            if n is not None:
                normalized.append(n)
        stats_all['normalized_total'] += len(normalized)

        # ── Filter ────────────────────────────────────────────────────────────
        if filter_enabled:
            filtered = []
            for a in normalized:
                a = dict(a)
                ptype = get_process_type(a)
                a['_process_type'] = ptype
                a['_threat_type']  = str(a.get('threat_name', 'general') or 'general')
                if ptype in NEED_ANALYSIS:
                    filtered.append(a)
        else:
            filtered = [
                {**a,
                 '_process_type': 'filter_disabled',
                 '_threat_type':  str(a.get('threat_name', 'general') or 'general')}
                for a in normalized
            ]
        stats_all['filtered_total'] += len(filtered)

        # ── Dedup ─────────────────────────────────────────────────────────────
        enriched = []
        if dedup_enabled and filtered:
            lock_fh = acquire_lock(lock_path)
            try:
                lsh_index, lsh_cache, dedup_key_cache, next_cluster_id = load_state(state_path, threshold)
                if lsh_index is None:
                    lsh_index = MinHashLSH(threshold=threshold, num_perm=NUM_PERM)
                    lsh_cache, dedup_key_cache, next_cluster_id = {}, {}, 0
                cid_box = [next_cluster_id]
                for a in filtered:
                    a = dict(a)
                    text_strict = '. '.join(str(a.get(f, ''))[:max_field_len] for f in strict_fields)
                    text_lsh    = normalize_uri('. '.join(str(a.get(f, ''))[:max_field_len] for f in lsh_fields))
                    mh = gen_minhash(text_lsh.lower(), _permutations)
                    sim_keys = lsh_index.query(mh)
                    if sim_keys:
                        cands = sim_keys[:100]
                        sims  = [mh.jaccard(lsh_cache[k]) for k in cands]
                        cluster_id = cands[sims.index(max(sims))]
                    else:
                        cluster_id = cid_box[0]
                        cid_box[0] += 1
                        lsh_index.insert(cluster_id, mh)
                        lsh_cache[cluster_id] = mh
                    a['_lsh_cluster_id'] = cluster_id
                    dk = hashlib.md5(f'{text_strict}. {cluster_id}'.encode('utf-8')).hexdigest()
                    a['dedup_key'] = dk
                    already = dk in dedup_key_cache
                    if already:
                        del dedup_key_cache[dk]
                    dedup_key_cache[dk] = None
                    a['is_duplicate'] = already
                    enriched.append(a)
                evict_oldest(lsh_index, lsh_cache, dedup_key_cache, max_dedup_keys)
                if len(lsh_cache) > LSH_CLUSTER_WARN_THRESHOLD or len(dedup_key_cache) > LSH_CLUSTER_WARN_THRESHOLD:
                    print(f'[dedup] WARNING: persisted state holds {len(lsh_cache)} clusters and '
                          f'{len(dedup_key_cache)} dedup_keys (warn={LSH_CLUSTER_WARN_THRESHOLD})')
                dump_state_atomic(state_path, lsh_index, lsh_cache, dedup_key_cache, threshold, cid_box[0])
            finally:
                release_lock(lock_fh)
        else:
            for a in filtered:
                a = dict(a)
                text_strict = '. '.join(str(a.get(f, ''))[:max_field_len] for f in strict_fields)
                text_lsh    = '. '.join(str(a.get(f, ''))[:max_field_len] for f in lsh_fields)
                dk = hashlib.md5(f'{text_strict}. {text_lsh}'.encode('utf-8')).hexdigest()
                a['_lsh_cluster_id'] = None
                a['dedup_key']       = dk
                a['is_duplicate']    = False
                enriched.append(a)

        # Unique within this batch (first-seen by dedup_key)
        seen_keys = set()
        unique_count = 0
        for a in enriched:
            k = a.get('dedup_key')
            if k not in seen_keys:
                seen_keys.add(k)
                unique_count += 1
        dup_count = len(enriched) - unique_count
        stats_all['enriched_total']   += len(enriched)
        stats_all['unique_total']     += unique_count
        stats_all['duplicates_total'] += dup_count

        if enriched and iter_cnt % log_progress_every == 0:
            print(f'[dedup] iter={iter_cnt}: enriched={len(enriched)}, '
                  f'unique={unique_count}, duplicates={dup_count}')

        # ── Write to disk ─────────────────────────────────────────────────────
        if enriched:
            try:
                _now = datetime.datetime.now()
                out_dir = get_output_dir(_now)
                written_paths = _write_jsonl(out_dir, enriched, _now)
                for p in written_paths:
                    if p not in stats_all['written_files']:
                        stats_all['written_files'].append(p)
                if iter_cnt % log_progress_every == 0:
                    print(f'[write] iter={iter_cnt}: {len(enriched)} → {written_paths[-1] if written_paths else ""}')
            except Exception as _we:
                stats_all['last_error'] = f'write failed: {_we}'
                print(f'[write] iter={iter_cnt}: failed: {_we}\n{traceback.format_exc()}')

        # ── Advance cursor ────────────────────────────────────────────────────
        last_to = time_to_ts
        save_cursor({
            'next_from':       last_to,
            'updated_at':      datetime.datetime.now().isoformat(),
            'iter':            iter_cnt,
            'workflow':        WORKFLOW_NAME,
        })

        time.sleep(pull_interval_s)

except KeyboardInterrupt:
    stop_reason = 'KeyboardInterrupt'
    print('[loop] interrupted by user')
except Exception as _loop_err:
    stop_reason = f'unhandled error: {_loop_err}'
    stats_all['last_error'] = f'unhandled: {_loop_err}'
    print(f'[loop] unhandled error: {_loop_err}\n{traceback.format_exc()}')

# ── Outputs ───────────────────────────────────────────────────────────────────

summary = (
    f'{WORKFLOW_NAME} done: iters={stats_all["iterations"]}, '
    f'pulls(ok={stats_all["pulls_succeeded"]}, fail={stats_all["pulls_failed"]}), '
    f'raw={stats_all["raw_total"]}, normalized={stats_all["normalized_total"]}, '
    f'filtered={stats_all["filtered_total"]}, enriched={stats_all["enriched_total"]}, '
    f'unique={stats_all["unique_total"]} (compression '
    f'{(stats_all["duplicates_total"] / stats_all["enriched_total"]) if stats_all["enriched_total"] else 0:.1%}), '
    f'files_written={len(stats_all["written_files"])}, stop={stop_reason}'
)
print(f'[done] {summary}')

outputs['stats']         = stats_all
outputs['summary']       = summary
outputs['stop_reason']   = stop_reason
outputs['final_cursor']  = last_to
outputs['output_paths']  = list(stats_all['written_files'])
outputs['output_path']   = stats_all['written_files'][-1] if stats_all['written_files'] else ''

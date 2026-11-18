"""
Writing triaged alerts to the SOC alert store, on SQLite.

The triage workflow hands its first-seen alerts to ``persist_triage_alerts``
(through the ``soc`` service a workflow's python nodes get, see
``flocks.coordination.workflow_services``).  One row is kept per ``dedup_key``:
a key seen again updates the triage fields of the row it already has and leaves
the original alert fields alone.

``_ensure_soc_db_schema`` also brings a store written by an older suite up to
date: rows an older version stored as followers or duplicates of a key move to
``alert_records_superseded`` rather than being deleted.
"""

from __future__ import annotations

import datetime as _datetime
import json
import os
import re
import sqlite3
import time

from . import _paths

def _ensure_soc_db_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_records (
            row_id TEXT PRIMARY KEY,
            record_id TEXT,
            asset_date TEXT NOT NULL,
            source_file TEXT NOT NULL,
            line_number INTEGER NOT NULL,
            event_time INTEGER,
            source_type TEXT,
            threat_name TEXT,
            dedup_key TEXT,
            is_duplicate INTEGER NOT NULL DEFAULT 0,
            record_json TEXT NOT NULL
        )
    """)
    columns = {row[1] for row in conn.execute('PRAGMA table_info(alert_records)')}
    dedup_key_added = 'dedup_key' not in columns
    if dedup_key_added:
        conn.execute('ALTER TABLE alert_records ADD COLUMN dedup_key TEXT')

    # One row per dedup_key (its first-seen, non-duplicate row), held by a
    # partial unique index. An older non-unique index under that name stays;
    # the unique one then takes the next free name.
    unique_index_name = 'idx_alert_records_first_seen_dedup_key'
    indexes = list(conn.execute('PRAGMA index_list(alert_records)'))
    unique_index_ready = any(
        str(row[1]).startswith(unique_index_name) and bool(row[2]) for row in indexes
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_alert_records_duplicate ON alert_records(is_duplicate)')
    has_persisted_duplicates = conn.execute("""
        SELECT 1 FROM alert_records
        WHERE is_duplicate = 1
          AND dedup_key IS NOT NULL
          AND dedup_key <> ''
        LIMIT 1
    """).fetchone() is not None
    if not unique_index_ready or has_persisted_duplicates:
        row_key = """NULLIF(TRIM(CASE
            WHEN dedup_key IS NOT NULL AND TRIM(dedup_key) <> '' THEN dedup_key
            WHEN json_valid(record_json) THEN CAST(json_extract(record_json, '$.dedup_key') AS TEXT)
        END), '')"""
        superseded = f"""
            WITH keyed AS (
                SELECT rowid AS rid, COALESCE(is_duplicate, 0) AS dup, {row_key} AS dedup
                FROM alert_records
            ),
            keepers AS (
                SELECT MIN(rid) AS rid FROM keyed WHERE dedup IS NOT NULL AND dup = 0 GROUP BY dedup
            ),
            superseded AS (
                SELECT rid FROM keyed WHERE dedup IS NOT NULL AND rid NOT IN (SELECT rid FROM keepers)
            )
        """
        if conn.execute(f'{superseded} SELECT 1 FROM superseded LIMIT 1').fetchone() is not None:
            # Followers and duplicates an older version stored under a key are
            # moved to alert_records_superseded instead of being deleted.
            import uuid

            conn.execute('CREATE TABLE IF NOT EXISTS alert_records_superseded AS SELECT * FROM alert_records WHERE 0')
            kept = {row[1] for row in conn.execute('PRAGMA table_info(alert_records_superseded)')}
            record_columns = [row[1] for row in conn.execute('PRAGMA table_info(alert_records)')]
            for column, definition in [(column, '') for column in record_columns] + [
                ('source_rowid', ' INTEGER'), ('superseded_batch', ' TEXT'), ('superseded_at', ' TEXT'),
            ]:
                if column not in kept:
                    conn.execute(f'ALTER TABLE alert_records_superseded ADD COLUMN "{column}"{definition}')
            column_list = ', '.join(f'"{column}"' for column in record_columns)
            batch = uuid.uuid4().hex
            conn.execute(f"""
                {superseded}
                INSERT INTO alert_records_superseded ({column_list}, source_rowid, superseded_batch, superseded_at)
                SELECT {column_list}, rowid, ?, ? FROM alert_records
                WHERE rowid IN (SELECT rid FROM superseded)
            """, (batch, _datetime.datetime.now().isoformat()))
            # Only the rows just copied, in the same transaction.
            conn.execute("""
                DELETE FROM alert_records
                WHERE rowid IN (
                    SELECT source_rowid FROM alert_records_superseded WHERE superseded_batch = ?
                )
            """, (batch,))
        # Every row still holding a key is its key's first-seen row, so the
        # trimmed key (or the one in record_json) cannot collide.
        conn.execute(f'UPDATE alert_records SET dedup_key = {row_key} WHERE dedup_key IS NOT {row_key}')
        if not unique_index_ready:
            taken = {row[1] for row in indexes}
            index_name, suffix = unique_index_name, 1
            while index_name in taken:
                suffix += 1
                index_name = f'{unique_index_name}_{suffix}'
            conn.execute(f"""
                CREATE UNIQUE INDEX {index_name}
                ON alert_records(dedup_key)
                WHERE dedup_key IS NOT NULL AND dedup_key <> ''
            """)

    conn.execute('CREATE INDEX IF NOT EXISTS idx_alert_records_asset_date ON alert_records(asset_date)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_alert_records_event_time ON alert_records(event_time)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_alert_records_source_type ON alert_records(source_type)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_alert_records_threat_name ON alert_records(threat_name)')


def _event_time_value(alert):
    for key in ('time', 'event_time', 'timestamp', 'timestamp_real', 'occur_time', 'created_at'):
        value = alert.get(key)
        if value in (None, ''):
            continue
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 100000000000:
                ts = ts / 1000.0
            return int(ts)
        text = str(value).strip()
        if not text:
            continue
        try:
            ts = float(text)
            if ts > 100000000000:
                ts = ts / 1000.0
            return int(ts)
        except Exception:
            pass
        normalized = text.replace('Z', '+00:00')
        try:
            return int(_datetime.datetime.fromisoformat(normalized).timestamp())
        except Exception:
            pass
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M'):
            try:
                return int(_datetime.datetime.strptime(text, fmt).timestamp())
            except Exception:
                continue
    return int(time.time())


def _asset_date_value(alert, event_time):
    value = alert.get('asset_date') or alert.get('_asset_date') or alert.get('date')
    if value:
        text = str(value).strip()
        if re.match(r'^\d{4}-\d{2}-\d{2}$', text):
            return text
    try:
        return _datetime.datetime.fromtimestamp(int(event_time)).strftime('%Y-%m-%d')
    except Exception:
        return _datetime.datetime.now().strftime('%Y-%m-%d')


def _source_type_value(alert):
    for key in ('source_type', '_source_type', 'data_source', 'log_type', 'vendor', 'device_type'):
        value = alert.get(key)
        if value not in (None, ''):
            return str(value)
    return ''


def _record_id_value(alert):
    for key in ('record_id', 'id', 'uuid', 'event_id', 'dedup_key'):
        value = alert.get(key)
        if value not in (None, ''):
            return str(value)
    return ''


def _stable_row_id(alert, source_file, line_number, event_time):
    existing = alert.get('row_id') or alert.get('_row_id')
    if existing:
        return str(existing)
    import hashlib as _hashlib
    basis = {
        'record_id': _record_id_value(alert),
        'dedup_key': alert.get('dedup_key', ''),
        'time': event_time,
        'source_file': source_file,
        'line_number': line_number,
        'sip': alert.get('sip', ''),
        'sport': alert.get('sport', ''),
        'dip': alert.get('dip', ''),
        'dport': alert.get('dport', ''),
        'threat_rule_id': alert.get('threat_rule_id') or alert.get('rule_id') or '',
    }
    raw = json.dumps(basis, sort_keys=True, ensure_ascii=False)
    return _hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _load_existing_soc_rows(conn, dedup_keys):
    existing = {}
    unique_keys = list(dict.fromkeys(str(key).strip() for key in dedup_keys if str(key).strip()))
    for start in range(0, len(unique_keys), 500):
        chunk = unique_keys[start:start + 500]
        placeholders = ','.join('?' for _ in chunk)
        rows = conn.execute(f"""
            SELECT row_id, record_id, asset_date, source_file, line_number,
                   event_time, source_type, threat_name, dedup_key,
                   is_duplicate, record_json
            FROM alert_records
            WHERE dedup_key IN ({placeholders})
        """, chunk)
        for row in rows:
            try:
                record = json.loads(row[10])
            except Exception:
                record = {}
            if not isinstance(record, dict):
                record = {}
            existing[row[8]] = {
                'row_id': row[0],
                'record_id': row[1],
                'asset_date': row[2],
                'source_file': row[3],
                'line_number': row[4],
                'event_time': row[5],
                'source_type': row[6],
                'threat_name': row[7],
                'record': record,
            }
    return existing


def _merge_triage_record(existing_record, incoming_record):
    merged = dict(existing_record) if isinstance(existing_record, dict) else {}
    triage_fields = (
        'has_dedup_key',
        'triage_source',
        'triage_status',
        'triage_attack_verdict',
        'risk_level',
        'report_title',
        'triage_report',
        'triage_attack_success',
        'triage_ms',
        'triage_error',
        '_triage_run_id',
        '_triage_persisted_at',
    )
    for key in triage_fields:
        if key in incoming_record:
            merged[key] = incoming_record[key]
        elif key in {'triage_ms', 'triage_error'}:
            merged.pop(key, None)
    return merged


def _triage_write_soc_db(db_path, alerts, run_id, default_source_file=''):
    db_path = str(db_path)
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    default_source_file = str(default_source_file or '')

    persisted_at = _datetime.datetime.now().isoformat()
    candidates = []
    seen_dedup_keys = set()
    for idx, alert in enumerate(alerts, 1):
        if not isinstance(alert, dict):
            continue
        dedup_key = str(alert.get('dedup_key') or '').strip()
        if not dedup_key or dedup_key in seen_dedup_keys:
            continue
        seen_dedup_keys.add(dedup_key)
        record = dict(alert)
        record['dedup_key'] = dedup_key
        record['is_duplicate'] = False
        record['_triage_run_id'] = run_id
        record['_triage_persisted_at'] = persisted_at
        source_file = str(
            record.get('source_file')
            or record.get('_source_file')
            or record.get('file_path')
            or default_source_file
            or 'stream_alert_triage'
        )
        try:
            line_number = int(record.get('line_number') or record.get('_line_number') or idx)
        except Exception:
            line_number = idx
        event_time = _event_time_value(record)
        candidates.append({
            'row_id': _stable_row_id(record, source_file, line_number, event_time),
            'record_id': _record_id_value(record),
            'asset_date': _asset_date_value(record, event_time),
            'source_file': source_file,
            'line_number': line_number,
            'event_time': event_time,
            'source_type': _source_type_value(record),
            'threat_name': str(record.get('threat_name') or record.get('rule_name') or ''),
            'dedup_key': dedup_key,
            'record': record,
        })

    insert_rows = []
    update_rows = []
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.execute('BEGIN IMMEDIATE')
        _ensure_soc_db_schema(conn)
        existing_by_key = _load_existing_soc_rows(
            conn, [candidate['dedup_key'] for candidate in candidates],
        )
        for candidate in candidates:
            existing = existing_by_key.get(candidate['dedup_key'])
            if existing:
                merged_record = _merge_triage_record(existing['record'], candidate['record'])
                merged_record['dedup_key'] = candidate['dedup_key']
                merged_record['is_duplicate'] = False
                update_rows.append((
                    candidate['dedup_key'],
                    json.dumps(merged_record, ensure_ascii=False),
                    existing['row_id'],
                ))
                continue
            insert_rows.append((
                candidate['row_id'],
                candidate['record_id'],
                candidate['asset_date'],
                candidate['source_file'],
                candidate['line_number'],
                candidate['event_time'],
                candidate['source_type'],
                candidate['threat_name'],
                candidate['dedup_key'],
                0,
                json.dumps(candidate['record'], ensure_ascii=False),
            ))

        if insert_rows:
            conn.executemany("""
                INSERT INTO alert_records (
                    row_id, record_id, asset_date, source_file, line_number,
                    event_time, source_type, threat_name, dedup_key,
                    is_duplicate, record_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, insert_rows)
        if update_rows:
            conn.executemany("""
                UPDATE alert_records
                SET dedup_key = ?, is_duplicate = 0, record_json = ?
                WHERE row_id = ?
            """, update_rows)
        conn.commit()

    persisted_rows = len(insert_rows) + len(update_rows)
    return {
        'path': db_path,
        'table': 'alert_records',
        'rows': persisted_rows,
        'inserted_rows': len(insert_rows),
        'updated_rows': len(update_rows),
    }


# ── LLM provider warm-up (avoid cold-start race in _parallel_4_branches) ──────
#
# Background: when this node starts, the LLM provider (e.g. threatbook-cn-llm)
# is lazy-initialized on the first call.  Inside `_parallel_4_branches` we
# submit 4 LLM calls to a ThreadPoolExecutor simultaneously; whichever one
# wins the race may race against provider registration and fail with
# "provider 'xxx' not exists" while subsequent calls succeed.  A single
# synchronous warm-up call before any concurrent fan-out forces the provider
# to finish registering on the main thread, eliminating the race entirely.
#
# Failure of the warm-up is non-fatal — we just log and continue.  The first
# real LLM call will still see the same error and surface it normally.


def ensure_schema() -> None:
    """Create the alert store, or bring an older one up to date."""
    db_path = _paths.soc_db()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.execute('BEGIN IMMEDIATE')
        _ensure_soc_db_schema(conn)
        conn.commit()


def persist_triage_alerts(alerts, run_id, source_file=''):
    """Store triaged alerts, one row per dedup_key; says where and how many rows it wrote."""
    result = _triage_write_soc_db(_paths.soc_db(), alerts, run_id, source_file)
    return {'backend': 'sqlite', 'unchanged_rows': 0, **result}

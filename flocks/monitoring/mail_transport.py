"""Existing email transport plus a durable IMAP catch-up cursor for monitoring."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from flocks.channel.base import OutboundContext
from flocks.channel.outbound.deliver import OutboundDelivery
from flocks.channel.registry import default_registry
from flocks.channel.builtin.email.config import parse_allowed_senders
from flocks.task.store import TaskStore

# Temporary development policy, explicitly requested for target-machine testing.
# From/allowlist matching does not authenticate a sender. Restore this to True
# before production; saved unverified feedback must not authorize new writes.
REQUIRE_AUTHENTICATED_FEEDBACK = False


class TransportNotReady(ValueError):
    """A known preflight failure; no SMTP request has been attempted."""


def mailbox_key(cfg):
    return hashlib.sha256(json.dumps([cfg.get(k, '') for k in ('imapHost', 'imapPort', 'username', 'address')]).encode()).hexdigest()


def transport(recipient=None):
    plugin = default_registry.get('email')
    cfg = getattr(plugin, '_resolved', {})
    if not plugin or not plugin.status.connected or not cfg:
        raise ValueError('请先连接 Flocks 邮件通道')
    if REQUIRE_AUTHENTICATED_FEEDBACK and not cfg.get('authservId'):
        raise ValueError('邮件通道需配置可信 authservId，以核验责任人回信身份')
    if recipient and not cfg.get('allowAll') and recipient not in parse_allowed_senders(cfg):
        raise ValueError('责任人邮箱不在邮件通道允许收件人列表中')
    return plugin, cfg


async def send(notice, session_id):
    try:
        _, cfg = transport(notice['recipient'])
    except ValueError as exc:
        raise TransportNotReady(str(exc)) from exc
    if mailbox_key(cfg) != notice['mailbox']:
        raise TransportNotReady('邮件账号已变化，请重新保存邮件跟进配置')
    ctx = OutboundContext(channel_id='email', to=notice['recipient'], text=notice['body'],
                          subject=notice['subject'], message_id=notice['message_id'],
                          new_thread=True, single_message=True, format_hint='plain')
    results = await OutboundDelivery.deliver(ctx, max_retries=1, session_id=session_id)
    return len(results) == 1 and results[0].success and results[0].message_id == notice['message_id']


def poll_range(cfg, imap):
    """Called on the IMAP thread. Search even already-read mail after opt-in.

    No cursor is advanced until the entire bounded fetch batch is consumed.
    SMTP and IMAP do not participate in our SQLite transactions.
    """
    path = TaskStore.get_db_path()
    if not path.exists():
        return None
    with sqlite3.connect(path, timeout=30) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='monitor_mail_settings'").fetchone():
            return None
        key = mailbox_key(cfg)
        since = db.execute('SELECT MIN(stamp) FROM (SELECT updated_at AS stamp FROM monitor_mail_settings WHERE mailbox=? '
                           'UNION ALL SELECT created_at AS stamp FROM monitor_mail_notices WHERE mailbox=?)', (key, key)).fetchone()[0]
        if not since:
            return None
        response = imap.response('UIDVALIDITY')[1]
        validity = str(response[0].decode() if response and isinstance(response[0], bytes) else response[0] if response else '')
        if not validity.isdigit():
            raise ValueError('IMAP UIDVALIDITY missing; cannot safely checkpoint monitoring mail')
        row = db.execute('SELECT validity,last_uid FROM monitor_mail_cursors WHERE mailbox=?', (key,)).fetchone()
        if row and row[0] == validity:
            return key, validity, ['UID', f'{row[1] + 1}:*'], row[1]
        date = datetime.fromisoformat(since).strftime('%d-%b-%Y')
        return key, validity, ['SINCE', date], 0


def checkpoint(poll, last_uid):
    if not poll or last_uid is None:
        return
    with sqlite3.connect(TaskStore.get_db_path(), timeout=30) as db:
        db.execute('INSERT INTO monitor_mail_cursors VALUES(?,?,?) ON CONFLICT(mailbox) DO UPDATE '
                   'SET validity=excluded.validity,last_uid=excluded.last_uid', (poll[0], poll[1], last_uid))


def record_unparsed(poll, uid):
    """Retain a durable locator before passing an unsupported message.

    The original message stays in IMAP. No body, sender or credentials enter
    diagnostics; an operator can inspect this record and the mailbox later.
    """
    with sqlite3.connect(TaskStore.get_db_path(), timeout=30) as db:
        db.execute('INSERT OR IGNORE INTO monitor_mail_unparsed VALUES(?,?,?,?)',
                   (poll[0], poll[1], int(uid), datetime.now(timezone.utc).isoformat()))


async def diagnose_poll(cfg, error_type):
    """Exportable transport failure with no mailbox address or exception body."""
    from .store import rows
    from . import diagnostics as diag
    try:
        for row in await rows('SELECT owner,scope FROM monitor_mail_settings WHERE mailbox=?', (mailbox_key(cfg),)):
            async with diag.trace_scope(row['owner'], row['scope'], 'mail-poll'):
                diag.event('mail.result', failure=True, stage='mail.receive', mail_state='pending', reason='unavailable', error_type=error_type)
    except Exception:
        pass  # Observability never prevents mailbox recovery.

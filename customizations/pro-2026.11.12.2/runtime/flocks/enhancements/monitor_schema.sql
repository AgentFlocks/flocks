
CREATE TABLE IF NOT EXISTS monitor_installations (
 owner TEXT NOT NULL, scope TEXT NOT NULL, project TEXT NOT NULL, policy TEXT NOT NULL,
 installed INTEGER NOT NULL DEFAULT 1, ready INTEGER NOT NULL DEFAULT 0,
 reason TEXT, scheduler_id TEXT, activation_pending INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner, scope));
CREATE TABLE IF NOT EXISTS monitor_daily_sessions (
 owner TEXT NOT NULL, project TEXT NOT NULL, scope TEXT NOT NULL, business_date TEXT NOT NULL,
 session_id TEXT NOT NULL UNIQUE, timezone TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'creating',
 PRIMARY KEY(owner, project, scope, business_date));
CREATE TABLE IF NOT EXISTS monitor_slots (
 scheduler_id TEXT NOT NULL, scheduled_for TEXT NOT NULL, execution_id TEXT UNIQUE,
 status TEXT NOT NULL, PRIMARY KEY(scheduler_id, scheduled_for));
CREATE TABLE IF NOT EXISTS monitor_attempts (
 sequence BIGSERIAL PRIMARY KEY, id TEXT NOT NULL UNIQUE, execution_id TEXT NOT NULL,
 owner TEXT NOT NULL, project TEXT NOT NULL, scope TEXT NOT NULL, business_date TEXT NOT NULL,
 session_id TEXT NOT NULL, message_id TEXT NOT NULL, scheduled_for TEXT, started_at TEXT NOT NULL,
 finished_at TEXT, status TEXT NOT NULL, error TEXT, result TEXT NOT NULL DEFAULT '{}',
 navigation_confirmed INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS monitor_attempts_day ON monitor_attempts(owner, scope, business_date);
CREATE INDEX IF NOT EXISTS monitor_attempts_project_day ON monitor_attempts(owner, scope, project, business_date, sequence);
CREATE TABLE IF NOT EXISTS monitor_steps (
 id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, message_id TEXT NOT NULL, part_id TEXT NOT NULL,
 tool TEXT NOT NULL, input TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
 finished_at TEXT, output TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS monitor_observations (
 attempt_id TEXT NOT NULL, event_key TEXT NOT NULL, data TEXT NOT NULL,
 PRIMARY KEY(attempt_id, event_key));
CREATE INDEX IF NOT EXISTS monitor_observations_event ON monitor_observations(event_key, attempt_id);
CREATE TABLE IF NOT EXISTS monitor_cursors (
 owner TEXT NOT NULL, scope TEXT NOT NULL, device TEXT NOT NULL, through_time INTEGER NOT NULL,
 PRIMARY KEY(owner, scope, device));
CREATE TABLE IF NOT EXISTS monitor_investigations (
 owner TEXT NOT NULL, project TEXT NOT NULL, event_key TEXT NOT NULL,
 event TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '[]', result TEXT NOT NULL DEFAULT '{}',
 state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL, PRIMARY KEY(owner,project,event_key));
CREATE TABLE IF NOT EXISTS monitor_investigation_versions (
 owner TEXT NOT NULL, project TEXT NOT NULL, event_key TEXT NOT NULL, revision INTEGER NOT NULL,
 event TEXT NOT NULL, evidence TEXT NOT NULL, result TEXT NOT NULL, state TEXT NOT NULL,
 attempts INTEGER NOT NULL, archived_at TEXT NOT NULL,
 PRIMARY KEY(owner,project,event_key,revision));
CREATE TABLE IF NOT EXISTS monitor_model_recovery (
 owner TEXT NOT NULL, project TEXT NOT NULL, device TEXT NOT NULL,
 provider TEXT NOT NULL, model TEXT NOT NULL, failures INTEGER NOT NULL DEFAULT 0,
 model_failure TEXT, next_retry_at TEXT, probe_until TEXT, updated_at TEXT NOT NULL,
 PRIMARY KEY(owner,project,device,provider,model));
CREATE TABLE IF NOT EXISTS monitor_event_followup (
 owner TEXT NOT NULL, scope TEXT NOT NULL, project TEXT NOT NULL, device TEXT NOT NULL,
 event_key TEXT NOT NULL, policy_revision TEXT NOT NULL, event_version TEXT NOT NULL,
 event TEXT NOT NULL, decision TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL DEFAULT 'watching',
 watch_count INTEGER NOT NULL DEFAULT 0, last_round TEXT, queued_round TEXT,
 target_status INTEGER, disposition_id TEXT, result TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(owner,scope,project,device,event_key));
CREATE INDEX IF NOT EXISTS monitor_event_followup_due ON monitor_event_followup(owner,scope,project,state,queued_round);
CREATE TABLE IF NOT EXISTS monitor_dispositions (
 rowid BIGSERIAL UNIQUE,
 id TEXT NOT NULL, owner TEXT NOT NULL, scope TEXT NOT NULL, event_key TEXT NOT NULL,
 comment TEXT NOT NULL, status TEXT NOT NULL, observed_status INTEGER, error TEXT,
 session_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 mode TEXT NOT NULL DEFAULT 'manual', target_status INTEGER NOT NULL DEFAULT 40,
 project TEXT, decision TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(owner, scope, id));
CREATE INDEX IF NOT EXISTS monitor_dispositions_event ON monitor_dispositions(owner,scope,event_key,created_at);
CREATE TABLE IF NOT EXISTS monitor_auto_settings (
 owner TEXT NOT NULL, scope TEXT NOT NULL, project TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
 revision TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(owner,scope));
CREATE TABLE IF NOT EXISTS monitor_auto_queue (
 owner TEXT NOT NULL, scope TEXT NOT NULL, project TEXT NOT NULL, event_key TEXT NOT NULL,
 checked_at TEXT NOT NULL DEFAULT '', reason TEXT, PRIMARY KEY(owner,scope,project,event_key));
CREATE TABLE IF NOT EXISTS monitor_mail_settings (
 owner TEXT NOT NULL, scope TEXT NOT NULL, project TEXT NOT NULL, recipient TEXT NOT NULL,
 responsible_name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0, revision TEXT NOT NULL,
 mailbox TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(owner,scope));
CREATE TABLE IF NOT EXISTS monitor_mail_notices (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, scope TEXT NOT NULL, project TEXT NOT NULL,
 event_key TEXT NOT NULL, recipient TEXT NOT NULL, revision TEXT NOT NULL, mailbox TEXT NOT NULL,
 message_id TEXT NOT NULL UNIQUE, subject TEXT NOT NULL, body TEXT NOT NULL,
 event TEXT NOT NULL, state TEXT NOT NULL, error TEXT, session_id TEXT, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, UNIQUE(owner,scope,project,event_key));
CREATE TABLE IF NOT EXISTS monitor_device_targets (
 owner TEXT NOT NULL, scope TEXT NOT NULL, project TEXT NOT NULL, device TEXT NOT NULL,
 device_name TEXT NOT NULL, recipient TEXT NOT NULL, responsible_name TEXT NOT NULL,
 revision TEXT NOT NULL, mailbox TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(owner,scope,project,device));
CREATE TABLE IF NOT EXISTS monitor_mail_replies (
 sequence BIGSERIAL PRIMARY KEY, id TEXT NOT NULL UNIQUE, owner TEXT NOT NULL,
 project TEXT NOT NULL, mailbox TEXT NOT NULL, message_id TEXT NOT NULL, sender TEXT NOT NULL,
 payload TEXT NOT NULL, state TEXT NOT NULL, result TEXT, error TEXT, received_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, UNIQUE(mailbox,message_id));
CREATE TABLE IF NOT EXISTS monitor_mail_items (
 id TEXT PRIMARY KEY, reply_id TEXT NOT NULL, notice_id TEXT NOT NULL, owner TEXT NOT NULL,
 project TEXT NOT NULL, target INTEGER NOT NULL, reason TEXT NOT NULL, state TEXT NOT NULL,
 error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(reply_id,notice_id));
CREATE TABLE IF NOT EXISTS monitor_mail_manual (
 rowid BIGSERIAL UNIQUE,
 id TEXT PRIMARY KEY, notice_id TEXT NOT NULL, owner TEXT NOT NULL, project TEXT NOT NULL,
 state TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS monitor_mail_manual_notice ON monitor_mail_manual(owner,notice_id,created_at);
CREATE TABLE IF NOT EXISTS monitor_mail_retries (
 id TEXT PRIMARY KEY, notice_id TEXT NOT NULL, owner TEXT NOT NULL, project TEXT NOT NULL,
 state TEXT NOT NULL, mail_state TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS monitor_mail_retries_notice ON monitor_mail_retries(owner,notice_id);
CREATE TABLE IF NOT EXISTS monitor_mail_cursors (
 mailbox TEXT PRIMARY KEY, validity TEXT NOT NULL, last_uid INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS monitor_mail_unparsed (
 mailbox TEXT NOT NULL, validity TEXT NOT NULL, uid INTEGER NOT NULL, recorded_at TEXT NOT NULL,
 PRIMARY KEY(mailbox,validity,uid));
CREATE TABLE IF NOT EXISTS monitor_reports (
 owner TEXT NOT NULL, scope TEXT NOT NULL, business_date TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', version INTEGER NOT NULL DEFAULT 0,
 content TEXT, error TEXT, PRIMARY KEY(owner, scope, business_date));

ALTER TABLE monitor_investigations ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE monitor_investigations ADD COLUMN IF NOT EXISTS failure_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE monitor_investigations ADD COLUMN IF NOT EXISTS next_retry_at TEXT;
ALTER TABLE monitor_investigations ADD COLUMN IF NOT EXISTS error_kind TEXT;
ALTER TABLE monitor_investigations ADD COLUMN IF NOT EXISTS retry_exhausted INTEGER NOT NULL DEFAULT 0;
ALTER TABLE monitor_investigations ADD COLUMN IF NOT EXISTS confirmed_risk TEXT;
ALTER TABLE monitor_attempts ADD COLUMN IF NOT EXISTS summary TEXT;
ALTER TABLE monitor_attempts ADD COLUMN IF NOT EXISTS next_step TEXT;
ALTER TABLE monitor_attempts ADD COLUMN IF NOT EXISTS end_message_id TEXT;
ALTER TABLE monitor_attempts ADD COLUMN IF NOT EXISTS end_published INTEGER NOT NULL DEFAULT 0;
ALTER TABLE monitor_mail_notices ADD COLUMN IF NOT EXISTS sent_at TEXT;
ALTER TABLE monitor_mail_manual ADD COLUMN IF NOT EXISTS notes TEXT NOT NULL DEFAULT '';
ALTER TABLE monitor_mail_retries ADD COLUMN IF NOT EXISTS message_id TEXT;
ALTER TABLE monitor_reports ADD COLUMN IF NOT EXISTS summary_content TEXT;

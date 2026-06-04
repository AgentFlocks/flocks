# Flocks Logging Layout

Flocks writes logs under `FLOCKS_LOG_DIR`, or `FLOCKS_ROOT/logs`, or `~/.flocks/logs`.

## Directory Layout

The log root contains only process logs and daily log directories:

```text
logs/
  backend.log
  webui.log
  YYYY-MM-DD/
    flocks.log
    errors.log
```

- `backend.log`: backend process stdout/stderr, appended as a single root-level file.
- `webui.log`: WebUI process stdout/stderr, appended as a single root-level file.
- `YYYY-MM-DD/flocks.log`: main structured application log for that day.
- `YYYY-MM-DD/errors.log`: WARN/ERROR lines for quick troubleshooting.

Daily `flocks.log` and `errors.log` files are not size-rotated. Flocks retains daily directories for 30 days by default and deletes older `YYYY-MM-DD/` directories during logging startup and day rollover.

## Environment Variables

Current logging variable:

- `FLOCKS_LOG_RETENTION_DAYS`: number of days to keep daily directories and legacy timestamp logs. Default: `30`.

Removed rotation variables:

- `FLOCKS_LOG_MAX_BYTES`
- `FLOCKS_LOG_MAX_MB`
- `FLOCKS_LOG_BACKUP_COUNT`

These variables no longer affect structured log files because daily logs are append-only and do not create `.log.1` / `.log.2` backup files.

## Migration Notes

- `dev.log` is no longer created. `Log.init(dev=True)` is kept for compatibility, but file output uses the same daily `flocks.log` / `errors.log` layout.
- `workflow.log` is no longer created by workflow logging. Workflow logs should be emitted through structured Flocks logging or stderr.
- `update.log` is no longer created. Upgrade journal lines are appended to that day's `errors.log`.
- Legacy root timestamp logs like `YYYY-MM-DDTHHMMSS.log` and their `.log.N` backups are retained until they are older than `FLOCKS_LOG_RETENTION_DAYS`, then cleaned up.

## Process Log Growth

`backend.log` and `webui.log` intentionally do not rotate because the log layout avoids `.log.N` suffixes and avoids automatic truncation. In normal local usage, `backend.log` has been observed at about 0.5 MB/day and `webui.log` is negligible, but noisy stdout/stderr or repeated exceptions can grow `backend.log` much faster.

If `backend.log` becomes large, archive or delete it manually while the service is stopped.

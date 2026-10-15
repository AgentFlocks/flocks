import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { History, Loader2, MessageSquareText, MessagesSquare, RefreshCw, Save, ScrollText, type LucideIcon } from 'lucide-react';
import { useToast } from '@/components/common/Toast';
import { requestConfirm } from '@/components/common/ConfirmDialog';
import {
  dataRetentionApi,
  type DataRetentionLastRun,
  type DataRetentionPolicy,
  type DataRetentionState,
} from '@/api/dataRetention';
import { formatBytes } from '@/api/workspace';

/** Choices offered for every kind of data; 0 keeps it forever. */
const RETENTION_PRESETS = [7, 14, 30, 90, 180, 365, 0];
const POLL_INTERVAL_MS = 2000;
const POLL_LIMIT_MS = 60_000;

type PolicyKey = keyof DataRetentionPolicy;

function policyOf(state: DataRetentionPolicy): DataRetentionPolicy {
  return { logDays: state.logDays, messageDays: state.messageDays, sessionDays: state.sessionDays };
}

function samePolicy(a: DataRetentionPolicy, b: DataRetentionPolicy): boolean {
  return a.logDays === b.logDays && a.messageDays === b.messageDays && a.sessionDays === b.sessionDays;
}

function RetentionRow({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 border-b border-zinc-200 py-5 last:border-b-0 dark:border-zinc-800 md:grid md:grid-cols-[minmax(0,1fr)_20rem] md:items-center md:gap-8">
      <div className="flex min-w-0 items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-zinc-100 text-zinc-500 dark:bg-zinc-900 dark:text-zinc-400">
          <Icon className="h-5 w-5" />
        </span>
        <div className="min-w-0">
          <h4 className="text-sm font-semibold text-zinc-950 dark:text-zinc-100">{title}</h4>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">{description}</p>
        </div>
      </div>
      <div className="flex w-full justify-start md:justify-end">{children}</div>
    </div>
  );
}

export default function DataRetentionSection() {
  const { t, i18n } = useTranslation('nav');
  const { error: showToastError, success: showToastSuccess } = useToast();
  const [state, setState] = useState<DataRetentionState | null>(null);
  const [draft, setDraft] = useState<DataRetentionPolicy | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  // The save whose cleanup we are waiting to finish (null when not waiting on a save).
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [pollTimedOut, setPollTimedOut] = useState(false);
  const [pollTick, setPollTick] = useState(0);
  const pollStartRef = useRef<number | null>(null);
  const loadFailedMessage = t('dataRetentionLoadFailed');
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const next = await dataRetentionApi.get();
      if (mountedRef.current) {
        setState(next);
        setPollTimedOut(false);
      }
      return next;
    } catch (err: any) {
      if (mountedRef.current) showToastError(loadFailedMessage, err?.response?.data?.detail || err?.message);
      return null;
    }
  }, [loadFailedMessage, showToastError]);

  // Manual refresh after a timeout. Only on success (refresh() returned state and
  // already cleared the timed-out flag) do we restart the polling window, so a
  // cleanup that is still running keeps being followed. If the refresh itself
  // fails, refresh() leaves the timed-out flag set and shows a toast, so the
  // button stays instead of vanishing into a blank "never ran" state.
  const manualRefresh = useCallback(async () => {
    const next = await refresh();
    if (next) {
      pollStartRef.current = null;
      setPollTick((tick) => tick + 1);
    }
  }, [refresh]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    dataRetentionApi.get()
      .then((next) => {
        if (cancelled) return;
        setState(next);
        setDraft(policyOf(next));
      })
      .catch((err: any) => {
        if (!cancelled) showToastError(loadFailedMessage, err?.response?.data?.detail || err?.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loadFailedMessage, showToastError]);

  // A cleanup is in flight from our view if the server says it is running, or we
  // just saved and the last run hasn't finished after the save yet. This also
  // picks up a cleanup another admin started before we opened the page.
  const cleanupPending = (state?.running ?? false)
    || (savedAt !== null && (state?.lastRun?.finishedAt ?? 0) < savedAt);

  // Poll while a cleanup is pending; give up after a minute so the status can
  // never be stuck on "cleaning" if a run is lost or a request fails. ``pollTick``
  // re-arms the timer after every attempt (even a failed one, which leaves the
  // state unchanged), so polling keeps going until it finishes or times out.
  useEffect(() => {
    if (!cleanupPending) {
      pollStartRef.current = null;
      return undefined;
    }
    if (pollStartRef.current === null) pollStartRef.current = Date.now();
    if (Date.now() - pollStartRef.current > POLL_LIMIT_MS) {
      setPollTimedOut(true);
      pollStartRef.current = null;
      setSavedAt(null);
      return undefined;
    }
    const timer = window.setTimeout(async () => {
      await refresh();
      if (mountedRef.current) setPollTick((tick) => tick + 1);
    }, POLL_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [cleanupPending, pollTick, refresh]);

  const optionLabel = useCallback((days: number) => {
    if (days === 0) return t('dataRetentionForever');
    if (days === 365) return t('dataRetentionOneYear');
    return t('dataRetentionDays', { count: days });
  }, [t]);

  const optionsFor = useCallback((current: number) => {
    // A value set by hand in flocks.json (or FLOCKS_LOG_RETENTION_DAYS) still shows up as its own choice.
    const days = RETENTION_PRESETS.filter((value) => value > 0);
    if (current > 0 && !days.includes(current)) days.push(current);
    return [...days.sort((a, b) => a - b), 0].map((value) => ({ value, label: optionLabel(value) }));
  }, [optionLabel]);

  const changed = useMemo(
    () => Boolean(state && draft && !samePolicy(policyOf(state), draft)),
    [draft, state],
  );

  const updateDraft = (key: PolicyKey, value: number) => {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  };

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const preview = await dataRetentionApi.preview(draft);
      const willDelete = preview.sessions + preview.messages + preview.logPaths + preview.recordFiles > 0;
      if (willDelete) {
        const confirmed = await requestConfirm({
          title: t('dataRetentionConfirmTitle'),
          description: t('dataRetentionConfirmDescription', {
            sessions: preview.sessions,
            messages: preview.messages,
            logs: preview.logPaths,
            records: preview.recordFiles,
            size: formatBytes(preview.logBytes + preview.recordBytes),
          }),
          confirmText: t('dataRetentionConfirmAction'),
          variant: 'danger',
        });
        if (!confirmed) return;
      }
      const requestedAt = Date.now();
      const next = await dataRetentionApi.update(draft);
      if (!mountedRef.current) return;
      setState(next);
      setDraft(policyOf(next));
      setPollTimedOut(false);
      setSavedAt(requestedAt);
      showToastSuccess(t(willDelete ? 'dataRetentionSavedCleaning' : 'dataRetentionSaved'));
    } catch (err: any) {
      showToastError(t('dataRetentionSaveFailed'), err?.response?.data?.detail || err?.message);
    } finally {
      if (mountedRef.current) setSaving(false);
    }
  };

  const formatTime = (ms: number) => new Date(ms).toLocaleString(i18n.language);

  const lastRunText = (lastRun: DataRetentionLastRun | null | undefined) => {
    if (pollTimedOut) return t('dataRetentionStatusUnknown');
    if (cleanupPending) return t('dataRetentionRunning');
    if (!lastRun) return t('dataRetentionNeverRun');
    if (lastRun.failed) return t('dataRetentionLastRunFailed', { time: formatTime(lastRun.finishedAt) });
    let summary = t('dataRetentionLastRun', {
      time: formatTime(lastRun.finishedAt),
      sessions: lastRun.sessions ?? 0,
      messages: lastRun.messages ?? 0,
      logs: lastRun.logPaths ?? 0,
      records: lastRun.recordFiles ?? 0,
      size: formatBytes(lastRun.freedBytes ?? 0),
    });
    if (lastRun.skippedBusy) summary += t('dataRetentionSkippedBusy', { count: lastRun.skippedBusy });
    // Some items failed to delete; the admin should check the system logs.
    if (lastRun.errors && lastRun.errors > 0) summary += t('dataRetentionPartialFailure', { count: lastRun.errors });
    return summary;
  };

  const rows: Array<{ key: PolicyKey; icon: LucideIcon; title: string; description: string }> = [
    { key: 'logDays', icon: ScrollText, title: t('dataRetentionLogs'), description: t('dataRetentionLogsDescription') },
    {
      key: 'messageDays',
      icon: MessageSquareText,
      title: t('dataRetentionMessages'),
      description: t('dataRetentionMessagesDescription'),
    },
    {
      key: 'sessionDays',
      icon: MessagesSquare,
      title: t('dataRetentionSessions'),
      description: t('dataRetentionSessionsDescription'),
    },
  ];

  return (
    <section
      aria-labelledby="data-retention-title"
      className="mt-6 rounded-xl border border-zinc-200 bg-white px-6 dark:border-zinc-800 dark:bg-zinc-900"
    >
      <div className="flex items-start gap-3 border-b border-zinc-200 py-5 dark:border-zinc-800">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-red-50 text-red-600 dark:bg-red-950/40 dark:text-red-400">
          <History className="h-5 w-5" />
        </span>
        <div className="min-w-0">
          <h3 id="data-retention-title" className="text-sm font-semibold text-zinc-950 dark:text-zinc-100">
            {t('dataRetention')}
          </h3>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">{t('dataRetentionDescription')}</p>
        </div>
      </div>

      {rows.map((row) => (
        <RetentionRow key={row.key} icon={row.icon} title={row.title} description={row.description}>
          <select
            aria-label={row.title}
            value={draft ? draft[row.key] : ''}
            disabled={loading || saving || !draft}
            onChange={(event) => updateDraft(row.key, Number(event.target.value))}
            className="h-9 w-full rounded-lg border border-zinc-200 bg-white px-3 text-sm text-zinc-900 outline-none transition-colors focus:border-zinc-400 disabled:cursor-wait disabled:opacity-60 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100 dark:focus:border-zinc-600 md:max-w-64"
          >
            {draft && optionsFor(draft[row.key]).map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </RetentionRow>
      ))}

      <div className="flex flex-col gap-3 py-5 md:flex-row md:items-center md:justify-between md:gap-8">
        <p className="flex min-w-0 flex-wrap items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400" aria-live="polite">
          {loading ? null : lastRunText(state?.lastRun)}
          {pollTimedOut && (
            <button
              type="button"
              onClick={() => void manualRefresh()}
              className="inline-flex items-center gap-1 rounded text-zinc-700 underline underline-offset-2 hover:text-zinc-950 dark:text-zinc-300 dark:hover:text-zinc-50"
            >
              <RefreshCw className="h-3 w-3" />
              {t('dataRetentionRefresh')}
            </button>
          )}
        </p>
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={loading || saving || !changed}
          className="inline-flex h-9 shrink-0 items-center justify-center gap-2 rounded-md bg-zinc-950 px-4 text-sm font-semibold text-white transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:bg-zinc-200 disabled:text-zinc-500 dark:bg-zinc-100 dark:text-zinc-950 dark:hover:bg-zinc-200 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500"
        >
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          {t('dataRetentionSave')}
        </button>
      </div>
    </section>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArchiveRestore, Loader2, RefreshCw, Search, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { sessionApi } from '@/api/session';
import { useToast } from '@/components/common/Toast';
import type { Session } from '@/types';

const PAGE_SIZE = 50;
const SYSTEM_SESSION_OWNER_USER_ID = 'api-token-service';

function getProjectLabel(session: Session): string {
  const projectName = session.projectName?.trim();
  if (projectName) return projectName;
  const normalizedDirectory = session.directory.replace(/[\\/]+$/g, '');
  const directoryName = normalizedDirectory.split(/[\\/]/).filter(Boolean).pop();
  return directoryName || session.projectID;
}

async function runBatchAction(
  ids: string[],
  action: (id: string) => Promise<unknown>,
): Promise<{ succeeded: string[]; failed: string[] }> {
  const results = await Promise.all(ids.map(async (id) => {
    try {
      await action(id);
      return { id, succeeded: true };
    } catch {
      return { id, succeeded: false };
    }
  }));
  return {
    succeeded: results.filter((result) => result.succeeded).map((result) => result.id),
    failed: results.filter((result) => !result.succeeded).map((result) => result.id),
  };
}

export default function ArchivedDataPanel() {
  const { t } = useTranslation('session');
  const toast = useToast();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [searchValue, setSearchValue] = useState('');
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [restoringIds, setRestoringIds] = useState<Set<string>>(new Set());
  const [pendingDeleteIds, setPendingDeleteIds] = useState<string[]>([]);
  const [deleting, setDeleting] = useState(false);
  const requestSeqRef = useRef(0);

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(searchValue.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [searchValue]);

  const fetchSessions = useCallback(async (append = false) => {
    const requestSeq = ++requestSeqRef.current;
    append ? setLoadingMore(true) : setLoading(true);
    setError(null);
    try {
      const offset = append ? sessions.length : 0;
      const response = await sessionApi.list({
        status: 'archived',
        view: 'list',
        manager: true,
        roots: true,
        search: search || undefined,
        limit: PAGE_SIZE,
        offset,
      });
      if (requestSeq !== requestSeqRef.current) return;
      const rows = Array.isArray(response) ? response : [];
      setSessions((current) => {
        if (!append) return rows;
        const knownIds = new Set(current.map((session) => session.id));
        return [...current, ...rows.filter((session) => !knownIds.has(session.id))];
      });
      setHasMore(rows.length >= PAGE_SIZE);
      if (!append) setSelectedIds(new Set());
    } catch (err: any) {
      if (requestSeq !== requestSeqRef.current) return;
      setError(err?.response?.data?.detail || err?.message || t('archivedData.loadFailed'));
    } finally {
      if (requestSeq === requestSeqRef.current) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [search, sessions.length, t]);

  useEffect(() => {
    void fetchSessions(false);
  }, [search]);

  const manageableSessions = useMemo(
    () => sessions.filter((session) => session.canDelete !== false),
    [sessions],
  );
  const allSelected = manageableSessions.length > 0
    && manageableSessions.every((session) => selectedIds.has(session.id));

  const removeRows = useCallback((ids: string[]) => {
    const removed = new Set(ids);
    setSessions((current) => current.filter((session) => !removed.has(session.id)));
    setSelectedIds((current) => {
      const next = new Set(current);
      ids.forEach((id) => next.delete(id));
      return next;
    });
  }, []);

  const restoreSessions = useCallback(async (ids: string[]) => {
    if (ids.length === 0) return;
    setRestoringIds((current) => new Set([...current, ...ids]));
    const { succeeded, failed } = await runBatchAction(ids, sessionApi.restore);
    removeRows(succeeded);
    setRestoringIds((current) => {
      const next = new Set(current);
      ids.forEach((id) => next.delete(id));
      return next;
    });
    if (succeeded.length > 0) toast.success(t('archivedData.restoreSuccess', { count: succeeded.length }));
    if (failed.length > 0) toast.error(t('archivedData.restoreFailed', { count: failed.length }));
  }, [removeRows, t, toast]);

  const permanentlyDelete = useCallback(async () => {
    if (pendingDeleteIds.length === 0 || deleting) return;
    setDeleting(true);
    const { succeeded, failed } = await runBatchAction(
      pendingDeleteIds,
      sessionApi.delete,
    );
    removeRows(succeeded);
    setDeleting(false);
    setPendingDeleteIds([]);
    if (succeeded.length > 0) toast.success(t('archivedData.deleteSuccess', { count: succeeded.length }));
    if (failed.length > 0) toast.error(t('archivedData.deleteFailed', { count: failed.length }));
  }, [deleting, pendingDeleteIds, removeRows, t, toast]);

  const toggleSelection = (id: string) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  return (
    <div className="mx-auto w-full max-w-7xl">
      <header className="border-b border-zinc-200 pb-6 dark:border-zinc-800">
        <h1 className="text-2xl font-bold tracking-normal text-zinc-950 dark:text-zinc-50">
          {t('archivedData.title')}
        </h1>
        <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">
          {t('archivedData.description')}
        </p>
      </header>

      <div className="mt-6 flex items-center gap-3">
        <label className="relative block w-full sm:max-w-sm">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
          <input
            value={searchValue}
            onChange={(event) => setSearchValue(event.target.value)}
            placeholder={t('archivedData.searchPlaceholder')}
            aria-label={t('archivedData.searchPlaceholder')}
            className="h-10 w-full rounded-lg border border-zinc-200 bg-white pl-9 pr-3 text-sm outline-none focus:border-zinc-400 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100"
          />
        </label>
        <button
          type="button"
          onClick={() => void fetchSessions(false)}
          disabled={loading}
          aria-label={t('archivedData.refresh')}
          title={t('archivedData.refresh')}
          className="ml-auto inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 disabled:opacity-50 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {selectedIds.size > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-2 rounded-lg border border-zinc-200 bg-zinc-50 p-3 dark:border-zinc-800 dark:bg-zinc-900/60">
          <span className="mr-auto text-sm text-zinc-600 dark:text-zinc-300">
            {t('archivedData.selected', { count: selectedIds.size })}
          </span>
          <button
            type="button"
            onClick={() => void restoreSessions(Array.from(selectedIds))}
            aria-label={t('archivedData.restoreSelected')}
            title={t('archivedData.restoreSelected')}
            className="inline-flex h-9 w-9 items-center justify-center rounded-md text-zinc-600 transition-colors hover:bg-white hover:text-zinc-950 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-50"
          >
            <ArchiveRestore className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => setPendingDeleteIds(Array.from(selectedIds))}
            className="inline-flex h-9 items-center gap-2 rounded-md bg-red-600 px-3 text-sm font-semibold text-white hover:bg-red-700"
          >
            <Trash2 className="h-4 w-4" />
            {t('archivedData.deleteSelected')}
          </button>
        </div>
      )}

      <div className="mt-4 overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800">
        {loading ? (
          <div className="flex min-h-48 items-center justify-center gap-2 text-sm text-zinc-500">
            <Loader2 className="h-5 w-5 animate-spin" />
            {t('archivedData.loading')}
          </div>
        ) : error ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-3 p-6 text-center">
            <p className="text-sm text-red-600 dark:text-red-300">{error}</p>
            <button type="button" onClick={() => void fetchSessions(false)} className="text-sm font-semibold underline">
              {t('archivedData.retry')}
            </button>
          </div>
        ) : sessions.length === 0 ? (
          <div className="flex min-h-48 flex-col items-center justify-center p-6 text-center text-zinc-500">
            <ArchiveRestore className="mb-3 h-8 w-8 text-zinc-300 dark:text-zinc-700" />
            <p className="text-sm font-semibold text-zinc-700 dark:text-zinc-300">{t('archivedData.empty')}</p>
            <p className="mt-1 text-xs">{t('archivedData.emptyDescription')}</p>
          </div>
        ) : (
          <>
            <div className="hidden grid-cols-[2.25rem_minmax(10rem,2fr)_minmax(6.5rem,0.8fr)_minmax(7rem,1fr)_9.5rem_7rem] items-center gap-4 border-b border-zinc-200 bg-zinc-50 px-4 py-2.5 text-sm font-normal text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400 md:grid">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={() => setSelectedIds(allSelected ? new Set() : new Set(manageableSessions.map((session) => session.id)))}
                aria-label={t('archivedData.selectAll')}
                className="h-4 w-4 justify-self-center"
              />
              <span>{t('archivedData.session')}</span>
              <span>{t('archivedData.owner')}</span>
              <span>{t('archivedData.project')}</span>
              <span>{t('archivedData.archivedAt')}</span>
              <span className="text-center">{t('archivedData.actions')}</span>
            </div>
            {sessions.map((session) => {
              const canManage = session.canDelete !== false;
              const restoring = restoringIds.has(session.id);
              const isSystemOwned = session.ownerUserID === SYSTEM_SESSION_OWNER_USER_ID
                || (!session.ownerUserID && session.ownerUsername === SYSTEM_SESSION_OWNER_USER_ID);
              const ownerLabel = isSystemOwned
                ? t('archivedData.systemOwner')
                : session.ownerUsername || t('archivedData.systemOwner');
              const projectLabel = getProjectLabel(session);
              return (
                <div key={session.id} className="grid gap-3 border-b border-zinc-100 px-4 py-3.5 text-sm font-normal last:border-b-0 dark:border-zinc-900 md:grid-cols-[2.25rem_minmax(10rem,2fr)_minmax(6.5rem,0.8fr)_minmax(7rem,1fr)_9.5rem_7rem] md:items-center md:gap-4">
                  <input
                    type="checkbox"
                    checked={selectedIds.has(session.id)}
                    onChange={() => toggleSelection(session.id)}
                    disabled={!canManage}
                    aria-label={t('archivedData.selectSession', { title: session.title })}
                    className="h-4 w-4 justify-self-center"
                  />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-normal text-zinc-900 dark:text-zinc-100">{session.title}</p>
                  </div>
                  <p className="truncate text-sm font-normal text-zinc-600 dark:text-zinc-400" title={ownerLabel}>
                    {ownerLabel}
                  </p>
                  <p className="truncate text-sm font-normal text-zinc-600 dark:text-zinc-400" title={projectLabel}>
                    {projectLabel}
                  </p>
                  <p className="text-sm font-normal text-zinc-500">
                    {session.time.archived ? new Date(session.time.archived).toLocaleString() : '—'}
                  </p>
                  <div className="flex items-center justify-center gap-3">
                    <button
                      type="button"
                      onClick={() => void restoreSessions([session.id])}
                      disabled={!canManage || restoring}
                      aria-label={t('archivedData.restore')}
                      title={t('archivedData.restore')}
                      className="text-sm font-normal text-blue-600 transition-colors hover:text-blue-700 disabled:opacity-40 dark:text-blue-300 dark:hover:text-blue-200"
                    >
                      {t('archivedData.restore')}
                    </button>
                    <button
                      type="button"
                      onClick={() => setPendingDeleteIds([session.id])}
                      disabled={!canManage}
                      aria-label={t('archivedData.delete')}
                      title={t('archivedData.delete')}
                      className="text-sm font-normal text-red-600 transition-colors hover:text-red-700 disabled:opacity-40 dark:text-red-300 dark:hover:text-red-200"
                    >
                      {t('archivedData.delete')}
                    </button>
                  </div>
                </div>
              );
            })}
          </>
        )}
      </div>

      {hasMore && !loading && (
        <div className="mt-4 flex justify-center">
          <button
            type="button"
            onClick={() => void fetchSessions(true)}
            disabled={loadingMore}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-zinc-200 px-4 text-sm font-semibold hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-800 dark:hover:bg-zinc-900"
          >
            {loadingMore && <Loader2 className="h-4 w-4 animate-spin" />}
            {t('archivedData.loadMore')}
          </button>
        </div>
      )}

      {pendingDeleteIds.length > 0 && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/45 p-4" role="presentation">
          <div role="dialog" aria-modal="true" aria-labelledby="permanent-delete-title" className="w-full max-w-md rounded-xl bg-white p-6 shadow-2xl dark:bg-zinc-900">
            <h2 id="permanent-delete-title" className="text-lg font-bold text-zinc-950 dark:text-zinc-50">
              {t('archivedData.deleteTitle')}
            </h2>
            <p className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-300">
              {t('archivedData.deleteDescription', { count: pendingDeleteIds.length })}
            </p>
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setPendingDeleteIds([])}
                disabled={deleting}
                className="h-9 rounded-md border border-zinc-200 px-4 text-sm font-semibold dark:border-zinc-700"
              >
                {t('cancel')}
              </button>
              <button
                type="button"
                onClick={() => void permanentlyDelete()}
                disabled={deleting}
                className="inline-flex h-9 items-center gap-2 rounded-md bg-red-600 px-4 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50"
              >
                {deleting && <Loader2 className="h-4 w-4 animate-spin" />}
                {t('archivedData.confirmDelete')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

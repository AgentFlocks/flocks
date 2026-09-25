import PageDialog from '../PageDialog';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { IconRestore, IconLoader2, IconRefresh, IconSearch, IconTrash } from '@tabler/icons-react';
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
      <header className="border-b border-line pb-6 ">
        <h1 className="text-title-page font-bold tracking-normal text-fg">
          {t('archivedData.title')}
        </h1>
        <p className="mt-2 text-sm text-fg-muted ">
          {t('archivedData.description')}
        </p>
      </header>

      <div className="mt-6 flex items-center gap-3">
        <label className="relative block w-full sm:max-w-sm">
          <IconSearch aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" />
          <input
            value={searchValue}
            onChange={(event) => setSearchValue(event.target.value)}
            placeholder={t('archivedData.searchPlaceholder')}
            aria-label={t('archivedData.searchPlaceholder')}
            className="ui-input h-10 w-full rounded-control border border-line bg-surface pl-9 pr-3 text-sm focus:border-line-control"
          />
        </label>
        <button
          type="button"
          onClick={() => void fetchSessions(false)}
          disabled={loading}
          aria-label={t('archivedData.refresh')}
          title={t('archivedData.refresh')}
          className="ui-btn-icon inline-flex items-center justify-center ml-auto inline-flex   shrink-0 items-center justify-center rounded-control text-fg-muted transition-colors hover:bg-hover hover:text-fg disabled:bg-disabled disabled:text-fg-disabled "
        >
          <IconRefresh aria-hidden="true" className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {selectedIds.size > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-2 rounded-control border border-line bg-subtle p-3 ">
          <span className="mr-auto text-sm text-fg-secondary ">
            {t('archivedData.selected', { count: selectedIds.size })}
          </span>
          <button
            type="button"
            onClick={() => void restoreSessions(Array.from(selectedIds))}
            aria-label={t('archivedData.restoreSelected')}
            title={t('archivedData.restoreSelected')}
            className="ui-btn-icon inline-flex items-center justify-center inline-flex   items-center justify-center rounded-control text-fg-secondary transition-colors hover:bg-surface hover:text-fg "
          >
            <IconRestore aria-hidden="true" className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => setPendingDeleteIds(Array.from(selectedIds))}
            className="inline-flex h-9 items-center gap-2 rounded-control bg-action-danger px-3 text-sm font-semibold text-action-fg hover:bg-action-danger"
          >
            <IconTrash aria-hidden="true" className="h-4 w-4" />
            {t('archivedData.deleteSelected')}
          </button>
        </div>
      )}

      <div className="mt-4 overflow-hidden rounded-card border border-line ">
        {loading ? (
          <div className="flex min-h-48 items-center justify-center gap-2 text-sm text-fg-muted">
            <IconLoader2 aria-hidden="true" className="h-5 w-5 animate-spin" />
            {t('archivedData.loading')}
          </div>
        ) : error ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-3 p-6 text-center">
            <p className="text-sm text-danger ">{error}</p>
            <button type="button" onClick={() => void fetchSessions(false)} className="text-sm font-semibold underline">
              {t('archivedData.retry')}
            </button>
          </div>
        ) : sessions.length === 0 ? (
          <div className="flex min-h-48 flex-col items-center justify-center p-6 text-center text-fg-muted">
            <IconRestore aria-hidden="true" className="mb-3 h-8 w-8 text-fg-placeholder " />
            <p className="text-sm font-semibold text-fg-secondary ">{t('archivedData.empty')}</p>
            <p className="mt-1 text-xs">{t('archivedData.emptyDescription')}</p>
          </div>
        ) : (
          <>
            <div className="hidden grid-cols-[2.25rem_minmax(10rem,2fr)_minmax(6.5rem,0.8fr)_minmax(7rem,1fr)_9.5rem_7rem] items-center gap-4 border-b border-line bg-subtle px-4 py-2.5 text-sm font-normal text-fg-muted md:grid">
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
                <div key={session.id} className="grid gap-3 border-b border-line px-4 py-3.5 text-sm font-normal last:border-b-0 md:grid-cols-[2.25rem_minmax(10rem,2fr)_minmax(6.5rem,0.8fr)_minmax(7rem,1fr)_9.5rem_7rem] md:items-center md:gap-4">
                  <input
                    type="checkbox"
                    checked={selectedIds.has(session.id)}
                    onChange={() => toggleSelection(session.id)}
                    disabled={!canManage}
                    aria-label={t('archivedData.selectSession', { title: session.title })}
                    className="h-4 w-4 justify-self-center"
                  />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-normal text-fg ">{session.title}</p>
                  </div>
                  <p className="truncate text-sm font-normal text-fg-secondary " title={ownerLabel}>
                    {ownerLabel}
                  </p>
                  <p className="truncate text-sm font-normal text-fg-secondary " title={projectLabel}>
                    {projectLabel}
                  </p>
                  <p className="text-sm font-normal text-fg-muted">
                    {session.time.archived ? new Date(session.time.archived).toLocaleString() : '—'}
                  </p>
                  <div className="flex items-center justify-center gap-3">
                    <button
                      type="button"
                      onClick={() => void restoreSessions([session.id])}
                      disabled={!canManage || restoring}
                      aria-label={t('archivedData.restore')}
                      title={t('archivedData.restore')}
                      className="text-sm font-normal text-fg-link transition-colors hover:underline disabled:bg-disabled disabled:text-fg-disabled "
                    >
                      {t('archivedData.restore')}
                    </button>
                    <button
                      type="button"
                      onClick={() => setPendingDeleteIds([session.id])}
                      disabled={!canManage}
                      aria-label={t('archivedData.delete')}
                      title={t('archivedData.delete')}
                      className="text-sm font-normal text-danger transition-colors hover:text-danger disabled:bg-disabled disabled:text-fg-disabled "
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
            className="inline-flex h-9 items-center gap-2 rounded-control border border-line px-4 text-sm font-semibold hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled "
          >
            {loadingMore && <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}
            {t('archivedData.loadMore')}
          </button>
        </div>
      )}

      {pendingDeleteIds.length > 0 && (
        <PageDialog aria-labelledby="permanent-delete-title" onClose={deleting ? undefined : () => setPendingDeleteIds([])} className="fixed inset-0 z-[100] flex items-center justify-center bg-overlay-backdrop p-4" >
          <div    className="w-full max-w-md rounded-card bg-surface p-6 shadow-overlay ">
            <h2 id="permanent-delete-title" className="text-title-section font-bold text-fg">
              {t('archivedData.deleteTitle')}
            </h2>
            <p className="mt-2 text-sm text-fg-secondary ">
              {t('archivedData.deleteDescription', { count: pendingDeleteIds.length })}
            </p>
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setPendingDeleteIds([])}
                disabled={deleting}
                className="h-9 rounded-control border border-line px-4 text-sm font-semibold "
              >
                {t('cancel')}
              </button>
              <button
                type="button"
                onClick={() => void permanentlyDelete()}
                disabled={deleting}
                className="inline-flex h-9 items-center gap-2 rounded-control bg-action-danger px-4 text-sm font-semibold text-action-fg hover:bg-action-danger disabled:bg-disabled disabled:text-fg-disabled"
              >
                {deleting && <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}
                {t('archivedData.confirmDelete')}
              </button>
            </div>
          </div>
        </PageDialog>
      )}
    </div>
  );
}

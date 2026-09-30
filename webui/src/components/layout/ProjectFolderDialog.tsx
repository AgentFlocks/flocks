// Create or rename a history folder (a project directory) from the sidebar.
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowUp, FolderOpen, HardDrive, Loader2 } from 'lucide-react';
import apiClient from '@/api/client';
import { useToast } from '@/components/common/Toast';

export interface ProjectFolderSummary {
  id: string;
  name?: string | null;
  worktree: string;
}

interface FolderEntry {
  name: string;
  path: string;
}

interface FolderBrowserResponse {
  path: string;
  parent: string | null;
  roots: FolderEntry[];
  entries: FolderEntry[];
}

export function pathBasename(path: string): string {
  const parts = path.replace(/[\\/]+$/, '').split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

interface ProjectFolderDialogProps {
  mode: 'create' | 'rename';
  /** The folder being renamed. */
  project?: ProjectFolderSummary | null;
  onClose: () => void;
  onSaved: (project: ProjectFolderSummary) => void;
}

/**
 * A history folder is a registered project directory: its sessions run in
 * that directory. Creating one asks for a name and a directory (created when
 * missing); renaming only changes the name.
 */
export default function ProjectFolderDialog({ mode, project, onClose, onSaved }: ProjectFolderDialogProps) {
  const { t } = useTranslation('nav');
  const toast = useToast();
  const [name, setName] = useState(() => (mode === 'rename' ? project?.name?.trim() || pathBasename(project?.worktree ?? '') : ''));
  const [nameEdited, setNameEdited] = useState(mode === 'rename');
  const [worktree, setWorktree] = useState('');
  // Until a directory is typed or picked, it follows the name under the default root.
  const [worktreeEdited, setWorktreeEdited] = useState(false);
  const [defaultRoot, setDefaultRoot] = useState<string | null>(null);
  const [browser, setBrowser] = useState<FolderBrowserResponse | null>(null);
  const [browserLoading, setBrowserLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const requestIdRef = useRef(0);

  const loadBrowser = useCallback(async (path?: string) => {
    const requestId = ++requestIdRef.current;
    setBrowserLoading(true);
    try {
      const response = await apiClient.get('/api/project/folders', { params: { path: path?.trim() || undefined } });
      if (requestId !== requestIdRef.current) return;
      const next = response.data as FolderBrowserResponse;
      setBrowser(next);
      setWorktree(next.path);
      setWorktreeEdited(true);
      if (!nameEdited) setName(pathBasename(next.path));
    } catch (err: any) {
      if (requestId === requestIdRef.current) toast.error(t('history.folderBrowseFailed'), err?.message);
    } finally {
      if (requestId === requestIdRef.current) setBrowserLoading(false);
    }
  }, [nameEdited, t, toast]);

  useEffect(() => {
    if (mode !== 'create') return undefined;
    let cancelled = false;
    void apiClient.get('/api/project/folders')
      .then((response) => {
        if (!cancelled) setDefaultRoot((response.data as FolderBrowserResponse).path);
      })
      .catch(() => {
        // Without a default root the directory has to be typed or picked.
      });
    return () => {
      cancelled = true;
    };
  }, [mode]);

  useEffect(() => {
    if (mode !== 'create' || worktreeEdited || !defaultRoot) return;
    const folderName = name.trim().replace(/[\\/]+/g, '-');
    setWorktree(folderName ? `${defaultRoot.replace(/[\\/]+$/, '')}/${folderName}` : '');
  }, [defaultRoot, mode, name, worktreeEdited]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !submitting) onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose, submitting]);

  const submit = async () => {
    if (submitting) return;
    const nextName = name.trim();
    const nextWorktree = worktree.trim();
    if (!nextName) {
      toast.error(t('history.folderSaveFailed'), t('history.folderNameEmpty'));
      return;
    }
    if (mode === 'create' && !nextWorktree) {
      toast.error(t('history.folderSaveFailed'), t('history.folderPathEmpty'));
      return;
    }
    setSubmitting(true);
    try {
      const response = mode === 'create'
        ? await apiClient.post('/api/project', { name: nextName, worktree: nextWorktree })
        : await apiClient.patch(`/api/project/${project?.id}`, { name: nextName });
      onSaved(response.data as ProjectFolderSummary);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      toast.error(t('history.folderSaveFailed'), typeof detail === 'string' ? detail : err?.message);
    } finally {
      setSubmitting(false);
    }
  };

  const inputClass = 'h-9 w-full rounded-lg border border-zinc-300 bg-white px-2.5 text-sm text-zinc-900 outline-none transition-colors placeholder:text-zinc-400 focus:border-red-500 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100';

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-zinc-950/40 px-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !submitting) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-labelledby="project-folder-dialog-title" className="w-full max-w-[440px] rounded-2xl border border-zinc-200 bg-white p-5 shadow-xl dark:border-zinc-700 dark:bg-zinc-900">
        <h2 id="project-folder-dialog-title" className="mb-4 text-base font-semibold text-zinc-900 dark:text-zinc-50">
          {mode === 'create' ? t('history.newFolder') : t('history.renameFolder')}
        </h2>
        <form onSubmit={(event) => { event.preventDefault(); void submit(); }} className="space-y-3">
          <div>
            <label htmlFor="project-folder-name" className="mb-1.5 block text-xs font-medium text-zinc-600 dark:text-zinc-400">{t('history.folderName')}</label>
            <input
              id="project-folder-name"
              autoFocus
              value={name}
              maxLength={80}
              disabled={submitting}
              placeholder={t('history.folderNamePlaceholder')}
              onChange={(event) => { setName(event.target.value); setNameEdited(true); }}
              className={inputClass}
            />
          </div>
          {mode === 'create' && (
            <div>
              <label htmlFor="project-folder-path" className="mb-1.5 block text-xs font-medium text-zinc-600 dark:text-zinc-400">{t('history.folderPath')}</label>
              <div className="flex gap-2">
                <input
                  id="project-folder-path"
                  value={worktree}
                  disabled={submitting}
                  placeholder={t('history.folderPathPlaceholder')}
                  onChange={(event) => {
                    requestIdRef.current += 1;
                    setBrowserLoading(false);
                    setWorktree(event.target.value);
                    setWorktreeEdited(true);
                    if (!nameEdited) setName(pathBasename(event.target.value));
                  }}
                  className={`${inputClass} min-w-0 flex-1 font-mono text-xs`}
                />
                <button
                  type="button"
                  onClick={() => void loadBrowser(worktree)}
                  disabled={submitting || browserLoading}
                  className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg border border-zinc-300 px-3 text-xs font-medium text-zinc-600 transition-colors hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                >
                  {browserLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FolderOpen className="h-3.5 w-3.5" />}
                  {t('history.chooseFolder')}
                </button>
              </div>
              <p className="mt-1.5 text-xs text-zinc-400">{t('history.folderPathHint')}</p>
              {browser && (
                <div className="mt-2 overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800">
                  <div className="flex items-center gap-1 border-b border-zinc-100 bg-zinc-50 px-2 py-1.5 dark:border-zinc-800 dark:bg-zinc-950">
                    <button
                      type="button"
                      onClick={() => browser.parent && void loadBrowser(browser.parent)}
                      disabled={!browser.parent || browserLoading}
                      className="rounded p-1 text-zinc-500 hover:bg-zinc-200 disabled:opacity-30 dark:hover:bg-zinc-800"
                      title={t('history.parentFolder')}
                      aria-label={t('history.parentFolder')}
                    >
                      <ArrowUp className="h-3.5 w-3.5" />
                    </button>
                    <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-500" title={browser.path}>{browser.path}</span>
                  </div>
                  <div className="flex gap-1 overflow-x-auto border-b border-zinc-100 px-2 py-1 dark:border-zinc-800">
                    {browser.roots.map((root) => (
                      <button key={root.path} type="button" onClick={() => void loadBrowser(root.path)} title={root.path}
                        className="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-1 text-[11px] text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 dark:hover:bg-zinc-800 dark:hover:text-zinc-200">
                        <HardDrive className="h-3 w-3" />
                        {root.name}
                      </button>
                    ))}
                  </div>
                  <div className="max-h-44 overflow-y-auto p-1">
                    {browser.entries.length > 0 ? browser.entries.map((entry) => (
                      <button key={entry.path} type="button" onClick={() => void loadBrowser(entry.path)} title={entry.path}
                        className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800">
                        <FolderOpen className="h-3.5 w-3.5 shrink-0 text-zinc-400" />
                        <span className="truncate">{entry.name}</span>
                      </button>
                    )) : (
                      <div className="px-2 py-4 text-center text-xs text-zinc-400">{t('history.noSubfolders')}</div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} disabled={submitting}
              className="h-9 rounded-lg px-4 text-sm text-zinc-600 transition-colors hover:bg-zinc-100 disabled:opacity-50 dark:text-zinc-300 dark:hover:bg-zinc-800">
              {t('history.cancel')}
            </button>
            <button type="submit" disabled={submitting}
              className="inline-flex h-9 min-w-[88px] items-center justify-center rounded-lg bg-red-600 px-4 text-sm font-semibold text-white transition-colors hover:bg-red-700 disabled:opacity-60">
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : mode === 'create' ? t('history.createFolder') : t('history.saveName')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft, ChevronDown, Download, FileText, Folder, FolderPlus,
  Maximize2, RefreshCw, Search, SlidersHorizontal, Sparkles, X,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

import {
  sessionApi,
  type SessionContextFile,
  type SessionContextRoot,
  type SessionContextRootNode,
  type SessionContextSnapshot,
} from '@/api/session';
import { formatBytes, type WorkspaceNode } from '@/api/workspace';
import {
  FilePreviewRenderer,
  PreviewModal,
  type PreviewFileAccess,
} from '@/components/common/FilePreview';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { TodoList } from '@/components/common/TodoList';

interface PreviewState {
  node: WorkspaceNode;
  fileAccess: PreviewFileAccess;
  content: string | null;
  truncated: boolean;
  previewLimitBytes: number | null;
  downloadUrl: string;
}

export interface SessionContextPanelProps {
  sessionId: string;
  snapshot: SessionContextSnapshot | null;
  loading: boolean;
  error?: string | null;
  requestedResourceID?: string | null;
  onRequestedResourceConsumed?: () => void;
  onClose: () => void;
  onRefresh: () => Promise<void> | void;
  onFocusMessage: (messageId: string) => void;
}

type FileFilter = 'all' | 'outputs' | 'context';
type FileSort = 'updated' | 'name' | 'size';

function fileTimestamp(file: SessionContextFile): number {
  return file.modifiedAt || file.createdAt || 0;
}

function getErrorMessage(error: unknown): string {
  const candidate = error as any;
  return candidate?.response?.data?.detail || candidate?.message || 'Request failed';
}

function toWorkspaceNode(file: SessionContextFile): WorkspaceNode {
  return {
    name: file.displayName,
    path: file.resourceID,
    type: 'file',
    size: file.size ?? undefined,
    modified_at: file.modifiedAt ? file.modifiedAt / 1000 : undefined,
    is_text_file: file.isTextFile,
  };
}

function Section({
  title,
  count,
  icon,
  children,
  defaultOpen = true,
  actions,
}: {
  title: string;
  count?: number;
  icon: React.ReactNode;
  children: React.ReactNode;
  defaultOpen?: boolean;
  actions?: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="border-b border-zinc-100 last:border-b-0 dark:border-zinc-800">
      <div className="flex items-center gap-2 px-3 py-2.5">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <span className="text-zinc-500 dark:text-zinc-400">{icon}</span>
          <span className="truncate text-xs font-semibold text-zinc-700 dark:text-zinc-200">{title}</span>
          {typeof count === 'number' && (
            <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
              {count}
            </span>
          )}
          <ChevronDown className={`ml-auto h-3.5 w-3.5 text-zinc-400 transition-transform ${open ? 'rotate-180' : ''}`} />
        </button>
        {actions}
      </div>
      {open && <div className="px-3 pb-3">{children}</div>}
    </section>
  );
}

function EmptySection({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border border-dashed border-zinc-200 px-3 py-4 text-center text-xs text-zinc-400 dark:border-zinc-700">{children}</div>;
}

export default function SessionContextPanel({
  sessionId,
  snapshot,
  loading,
  error,
  requestedResourceID,
  onRequestedResourceConsumed,
  onClose,
  onRefresh,
  onFocusMessage,
}: SessionContextPanelProps) {
  const { t } = useTranslation('session');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<FileFilter>('all');
  const [sort, setSort] = useState<FileSort>('updated');
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [root, setRoot] = useState<SessionContextRoot | null>(null);
  const [rootPath, setRootPath] = useState('');
  const [rootItems, setRootItems] = useState<SessionContextRootNode[]>([]);
  const [rootLoading, setRootLoading] = useState(false);
  const [showFolderInput, setShowFolderInput] = useState(false);
  const [folderPath, setFolderPath] = useState('');
  const [folderBusy, setFolderBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const previewRequestRef = useRef(0);
  const rootRequestRef = useRef(0);

  useEffect(() => {
    previewRequestRef.current += 1;
    rootRequestRef.current += 1;
    setPreview(null);
    setRoot(null);
    setRootPath('');
    setRootItems([]);
    setFullscreen(false);
    setActionError(null);
  }, [sessionId]);

  const files = useMemo(() => {
    const all = [...(snapshot?.outputs || []), ...(snapshot?.contextFiles || [])];
    const normalizedQuery = query.trim().toLowerCase();
    const filtered = all.filter((file) => {
      if (filter !== 'all' && file.section !== filter) return false;
      return !normalizedQuery
        || file.displayName.toLowerCase().includes(normalizedQuery)
        || file.logicalPath.toLowerCase().includes(normalizedQuery)
        || file.mimeType.toLowerCase().includes(normalizedQuery);
    });
    return filtered.sort((left, right) => {
      if (sort === 'name') return left.displayName.localeCompare(right.displayName);
      if (sort === 'size') return (right.size || 0) - (left.size || 0);
      return fileTimestamp(right) - fileTimestamp(left);
    });
  }, [filter, query, snapshot, sort]);
  const outputFiles = useMemo(
    () => files.filter((file) => file.section === 'outputs'),
    [files],
  );
  const contextFiles = useMemo(
    () => files.filter((file) => file.section === 'context'),
    [files],
  );

  const openResource = useCallback(async (file: SessionContextFile) => {
    const requestId = ++previewRequestRef.current;
    const node = toWorkspaceNode(file);
    const fileAccess: PreviewFileAccess = {
      previewUrl: (resourceId) => sessionApi.contextFilePreviewUrl(sessionId, resourceId),
      downloadUrl: (resourceId) => sessionApi.contextFileDownloadUrl(sessionId, resourceId),
    };
    setActionError(null);
    setPreviewLoading(true);
    try {
      const content = file.isTextFile && file.status !== 'missing'
        ? await sessionApi.readContextFile(sessionId, file.resourceID)
        : null;
      if (requestId !== previewRequestRef.current) return;
      setPreview({
        node,
        fileAccess,
        content: content?.content ?? null,
        truncated: content?.truncated ?? false,
        previewLimitBytes: content?.previewLimitBytes ?? null,
        downloadUrl: fileAccess.downloadUrl(file.resourceID),
      });
      setRoot(null);
      setFullscreen(false);
    } catch (error) {
      if (requestId === previewRequestRef.current) setActionError(getErrorMessage(error));
    } finally {
      if (requestId === previewRequestRef.current) setPreviewLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    if (!requestedResourceID || !snapshot) return;
    const file = [...snapshot.outputs, ...snapshot.contextFiles]
      .find((item) => item.resourceID === requestedResourceID);
    if (file) {
      onRequestedResourceConsumed?.();
      void openResource(file);
    }
  }, [onRequestedResourceConsumed, openResource, requestedResourceID, snapshot]);

  const openRootFile = useCallback(async (selectedRoot: SessionContextRoot, item: SessionContextRootNode) => {
    const requestId = ++previewRequestRef.current;
    const node: WorkspaceNode = {
      name: item.name,
      path: item.path,
      type: 'file',
      size: item.size ?? undefined,
      modified_at: item.modifiedAt ? item.modifiedAt / 1000 : undefined,
      is_text_file: item.isTextFile,
    };
    const fileAccess: PreviewFileAccess = {
      previewUrl: (path) => sessionApi.contextRootPreviewUrl(sessionId, selectedRoot.id, path),
      downloadUrl: (path) => sessionApi.contextRootDownloadUrl(sessionId, selectedRoot.id, path),
    };
    setActionError(null);
    setPreviewLoading(true);
    try {
      const content = item.isTextFile
        ? await sessionApi.readContextRootFile(sessionId, selectedRoot.id, item.path)
        : null;
      if (requestId !== previewRequestRef.current) return;
      setPreview({
        node,
        fileAccess,
        content: content?.content ?? null,
        truncated: content?.truncated ?? false,
        previewLimitBytes: content?.previewLimitBytes ?? null,
        downloadUrl: fileAccess.downloadUrl(item.path),
      });
      setFullscreen(false);
    } catch (error) {
      if (requestId === previewRequestRef.current) setActionError(getErrorMessage(error));
    } finally {
      if (requestId === previewRequestRef.current) setPreviewLoading(false);
    }
  }, [sessionId]);

  const loadRoot = useCallback(async (selectedRoot: SessionContextRoot, path = '') => {
    const requestId = ++rootRequestRef.current;
    setActionError(null);
    setRootLoading(true);
    try {
      const response = await sessionApi.listContextRoot(sessionId, selectedRoot.id, path);
      if (requestId !== rootRequestRef.current) return;
      setRoot(selectedRoot);
      setRootPath(response.path);
      setRootItems(response.items);
      setPreview(null);
    } catch (error) {
      if (requestId === rootRequestRef.current) setActionError(getErrorMessage(error));
    } finally {
      if (requestId === rootRequestRef.current) setRootLoading(false);
    }
  }, [sessionId]);

  const addFolder = useCallback(async () => {
    const path = folderPath.trim();
    if (!path) return;
    setActionError(null);
    setFolderBusy(true);
    try {
      await sessionApi.addContextFolder(sessionId, path);
      setFolderPath('');
      setShowFolderInput(false);
      await onRefresh();
    } catch (error) {
      setActionError(getErrorMessage(error));
    } finally {
      setFolderBusy(false);
    }
  }, [folderPath, onRefresh, sessionId]);

  const removeFolder = useCallback(async (selectedRoot: SessionContextRoot) => {
    setActionError(null);
    try {
      await sessionApi.removeContextFolder(sessionId, selectedRoot.id);
      if (root?.id === selectedRoot.id) {
        setRoot(null);
        setRootItems([]);
        setRootPath('');
        setPreview(null);
      }
      await onRefresh();
    } catch (error) {
      setActionError(getErrorMessage(error));
    }
  }, [onRefresh, root?.id, sessionId]);

  if (preview) {
    return (
      <div className="relative flex h-full min-h-0 flex-col bg-white dark:bg-[#242b33]">
        <div className="flex h-[52px] flex-shrink-0 items-center gap-2 border-b border-zinc-100 px-3 dark:border-zinc-800">
          <button type="button" onClick={() => setPreview(null)} className="rounded p-1.5 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800">
            <ArrowLeft className="h-4 w-4" />
          </button>
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-zinc-800 dark:text-zinc-100">{preview.node.name}</span>
          <a href={preview.downloadUrl} download={preview.node.name} className="rounded p-1.5 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800" title={t('context.download')}>
            <Download className="h-4 w-4" />
          </a>
          <button type="button" onClick={() => setFullscreen(true)} className="rounded p-1.5 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800" title={t('context.fullscreen')}>
            <Maximize2 className="h-4 w-4" />
          </button>
          <button type="button" onClick={onClose} className="rounded p-1.5 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800">
            <X className="h-4 w-4" />
          </button>
        </div>
        {actionError && <div className="border-b border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">{actionError}</div>}
        <div className="min-h-0 flex-1">
          <FilePreviewRenderer
            node={preview.node}
            content={preview.content}
            editing={false}
            editContent={null}
            truncated={preview.truncated}
            previewLimitBytes={preview.previewLimitBytes}
            fileAccess={preview.fileAccess}
            onEditChange={() => undefined}
          />
        </div>
        {fullscreen && (
          <PreviewModal
            node={preview.node}
            content={preview.content}
            truncated={preview.truncated}
            previewLimitBytes={preview.previewLimitBytes}
            fileAccess={preview.fileAccess}
            onClose={() => setFullscreen(false)}
          />
        )}
      </div>
    );
  }

  if (root) {
    const parentPath = rootPath.includes('/') ? rootPath.slice(0, rootPath.lastIndexOf('/')) : '';
    return (
      <div className="relative flex h-full min-h-0 flex-col bg-white dark:bg-[#242b33]">
        <div className="flex h-[52px] flex-shrink-0 items-center gap-2 border-b border-zinc-100 px-3 dark:border-zinc-800">
          <button type="button" onClick={() => { setRoot(null); setRootItems([]); }} className="rounded p-1.5 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800">
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium text-zinc-800 dark:text-zinc-100">{root.displayName}</div>
            {rootPath && <div className="truncate text-[10px] text-zinc-400">{rootPath}</div>}
          </div>
          <button type="button" onClick={onClose} className="rounded p-1.5 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"><X className="h-4 w-4" /></button>
        </div>
        {actionError && <div className="border-b border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">{actionError}</div>}
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {rootPath && (
            <button type="button" onClick={() => void loadRoot(root, parentPath)} className="mb-2 flex w-full items-center gap-2 rounded-lg px-2 py-2 text-xs text-zinc-500 hover:bg-zinc-50 dark:hover:bg-zinc-800">
              <ArrowLeft className="h-3.5 w-3.5" />
              {t('context.parentFolder')}
            </button>
          )}
          {rootLoading ? <div className="py-8"><LoadingSpinner /></div> : rootItems.length === 0 ? (
            <EmptySection>{t('context.emptyFolder')}</EmptySection>
          ) : (
            <div className="space-y-1">
              {rootItems.map((item) => (
                <button
                  key={item.path}
                  type="button"
                  onClick={() => item.type === 'directory'
                    ? void loadRoot(root, item.path)
                    : void openRootFile(root, item)}
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left hover:bg-zinc-50 dark:hover:bg-zinc-800"
                >
                  {item.type === 'directory' ? <Folder className="h-4 w-4 text-amber-500" /> : <FileText className="h-4 w-4 text-zinc-500" />}
                  <span className="min-w-0 flex-1 truncate text-xs text-zinc-700 dark:text-zinc-200">{item.name}</span>
                  {item.type === 'file' && item.size != null && <span className="text-[10px] text-zinc-400">{formatBytes(item.size)}</span>}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="relative flex h-full min-h-0 flex-col bg-white dark:bg-[#242b33]">
      <div className="flex h-[52px] flex-shrink-0 items-center gap-2 border-b border-zinc-100 px-3 dark:border-zinc-800">
        <Sparkles className="h-4 w-4 text-violet-500" />
        <span className="min-w-0 flex-1 truncate text-sm font-semibold text-zinc-800 dark:text-zinc-100">{t('context.title')}</span>
        <button type="button" onClick={() => void onRefresh()} className="rounded p-1.5 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800" title={t('context.refresh')}>
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
        <button type="button" onClick={onClose} className="rounded p-1.5 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"><X className="h-4 w-4" /></button>
      </div>

      <div className="flex items-center gap-2 border-b border-zinc-100 px-3 py-2 dark:border-zinc-800">
        <div className="relative min-w-0 flex-1">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-400" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('context.searchPlaceholder')}
            className="h-8 w-full rounded-lg border border-zinc-200 bg-zinc-50 pl-8 pr-2 text-xs outline-none focus:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
          />
        </div>
        <SlidersHorizontal className="h-3.5 w-3.5 text-zinc-400" />
        <select value={filter} onChange={(event) => setFilter(event.target.value as FileFilter)} className="h-8 rounded-lg border border-zinc-200 bg-white px-2 text-xs text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
          <option value="all">{t('context.filter.all')}</option>
          <option value="outputs">{t('context.outputs')}</option>
          <option value="context">{t('context.contextFiles')}</option>
        </select>
        <select value={sort} onChange={(event) => setSort(event.target.value as FileSort)} className="h-8 rounded-lg border border-zinc-200 bg-white px-2 text-xs text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
          <option value="updated">{t('context.sort.updated')}</option>
          <option value="name">{t('context.sort.name')}</option>
          <option value="size">{t('context.sort.size')}</option>
        </select>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {(actionError || error) && <div className="m-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">{actionError || error}</div>}
        {snapshot?.historyTruncated && (
          <div className="mx-3 mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            {t('context.recentHistoryOnly')}
          </div>
        )}
        {loading && !snapshot ? <div className="py-10"><LoadingSpinner /></div> : (
          <>
            <Section title={t('context.progress')} count={snapshot?.progress.length || 0} icon={<SlidersHorizontal className="h-4 w-4" />} defaultOpen={Boolean(snapshot?.progress.length)}>
              {snapshot?.progress.length ? <TodoList items={snapshot.progress} /> : <EmptySection>{t('context.noProgress')}</EmptySection>}
            </Section>

            <Section title={t('context.outputs')} count={snapshot?.outputs.length || 0} icon={<Sparkles className="h-4 w-4" />}>
              {outputFiles.length === 0 ? <EmptySection>{t('context.noOutputs')}</EmptySection> : (
                <FileRows files={outputFiles} onOpen={openResource} onFocusMessage={onFocusMessage} sessionId={sessionId} />
              )}
            </Section>

            <Section
              title={t('context.contextFiles')}
              count={(snapshot?.contextFiles.length || 0) + (snapshot?.roots.length || 0)}
              icon={<Folder className="h-4 w-4" />}
              actions={snapshot?.canManageFolders ? (
                <button type="button" onClick={() => setShowFolderInput((value) => !value)} className="rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800" title={t('context.addFolder')}>
                  <FolderPlus className="h-3.5 w-3.5" />
                </button>
              ) : undefined}
            >
              {snapshot?.canManageFolders && showFolderInput && (
                <div className="mb-2 flex gap-1.5">
                  <input value={folderPath} onChange={(event) => setFolderPath(event.target.value)} placeholder={t('context.folderPathPlaceholder')} className="h-8 min-w-0 flex-1 rounded-lg border border-zinc-200 px-2 text-xs outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100" />
                  <button type="button" disabled={folderBusy || !folderPath.trim()} onClick={() => void addFolder()} className="rounded-lg bg-zinc-800 px-2.5 text-xs text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900">
                    {folderBusy ? '…' : t('context.add')}
                  </button>
                </div>
              )}
              <div className="space-y-1">
                {(snapshot?.roots || []).map((item) => (
                  <div key={item.id} className="group flex items-center gap-1 rounded-lg hover:bg-zinc-50 dark:hover:bg-zinc-800">
                    <button type="button" disabled={item.status !== 'available'} onClick={() => void loadRoot(item)} className="flex min-w-0 flex-1 items-center gap-2 px-2 py-2 text-left disabled:opacity-50">
                      <Folder className="h-4 w-4 text-amber-500" />
                      <span className="min-w-0 flex-1 truncate text-xs text-zinc-700 dark:text-zinc-200">{item.displayName}</span>
                      <span className="text-[10px] text-zinc-400">{item.kind === 'project' ? t('context.project') : t('context.folder')}</span>
                    </button>
                    {item.kind === 'folder' && (
                      <button type="button" onClick={() => void removeFolder(item)} className="mr-1 rounded p-1 text-zinc-300 opacity-0 hover:bg-zinc-100 hover:text-red-500 group-hover:opacity-100 dark:hover:bg-zinc-700"><X className="h-3 w-3" /></button>
                    )}
                  </div>
                ))}
                {contextFiles.length > 0 && (
                  <FileRows files={contextFiles} onOpen={openResource} onFocusMessage={onFocusMessage} sessionId={sessionId} />
                )}
                {(snapshot?.roots.length || 0) === 0 && contextFiles.length === 0 && <EmptySection>{t('context.noContext')}</EmptySection>}
              </div>
            </Section>

            {(snapshot?.skills.length || 0) > 0 && (
              <Section title={t('context.skills')} count={snapshot?.skills.length || 0} icon={<Sparkles className="h-4 w-4" />} defaultOpen={false}>
                <div className="space-y-1">
                  {snapshot!.skills.map((skill) => (
                    <div key={skill.name} className="flex items-center gap-2 rounded-lg px-2 py-2 text-xs text-zinc-700 dark:text-zinc-200">
                      <Sparkles className="h-3.5 w-3.5 text-violet-500" />
                      <span className="min-w-0 flex-1 truncate">{skill.name}</span>
                      <span className="text-[10px] text-zinc-400">{t('context.loaded')}</span>
                    </div>
                  ))}
                </div>
              </Section>
            )}
          </>
        )}
      </div>
      {previewLoading && <div className="absolute inset-0 z-10 grid place-items-center bg-white/60 dark:bg-zinc-900/60"><LoadingSpinner /></div>}
    </div>
  );
}

function FileRows({
  files,
  onOpen,
  onFocusMessage,
  sessionId,
}: {
  files: SessionContextFile[];
  onOpen: (file: SessionContextFile) => Promise<void>;
  onFocusMessage: (messageId: string) => void;
  sessionId: string;
}) {
  const { t } = useTranslation('session');
  return (
    <div className="space-y-1">
      {files.map((file) => (
        <div key={file.resourceID} className="group flex items-center gap-1 rounded-lg hover:bg-zinc-50 dark:hover:bg-zinc-800">
          <button type="button" disabled={file.status === 'missing'} onClick={() => void onOpen(file)} className="flex min-w-0 flex-1 items-center gap-2 px-2 py-2 text-left disabled:opacity-50">
            <FileText className="h-4 w-4 text-zinc-500" />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs font-medium text-zinc-700 dark:text-zinc-200">{file.displayName}</span>
              <span className="block truncate text-[10px] text-zinc-400">{file.logicalPath}</span>
            </span>
            {file.size != null && <span className="text-[10px] text-zinc-400">{formatBytes(file.size)}</span>}
          </button>
          <button type="button" onClick={() => onFocusMessage(file.sourceMessageID)} className="rounded p-1 text-zinc-300 opacity-0 hover:bg-zinc-100 hover:text-zinc-600 group-hover:opacity-100 dark:hover:bg-zinc-700" title={t('context.locateMessage')}>
            <ArrowLeft className="h-3 w-3" />
          </button>
          <a href={sessionApi.contextFileDownloadUrl(sessionId, file.resourceID)} download={file.displayName} className="mr-1 rounded p-1 text-zinc-300 opacity-0 hover:bg-zinc-100 hover:text-zinc-600 group-hover:opacity-100 dark:hover:bg-zinc-700" title={t('context.download')}>
            <Download className="h-3 w-3" />
          </a>
        </div>
      ))}
    </div>
  );
}

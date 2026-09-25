import { pageActionLabel } from '../pageActionLabel';
import { Suspense, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import {
  IconArrowLeft,
  IconChevronDown,
  IconDownload,
  IconFileText,
  IconFolder,
  IconFolderPlus,
  IconArrowsDiagonal,
  IconRefresh,
  IconSearch,
  IconAdjustmentsHorizontal,
  IconSparkles,
  IconX,
} from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';

import {
  sessionApi,
  type SessionContextFile,
  type SessionContextRoot,
  type SessionContextRootNode,
  type SessionContextSkill,
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
import { extractErrorMessage } from '@/utils/error';

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
  loadingMore?: boolean;
  onLoadMore?: () => Promise<void> | void;
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
    <section className="border-b border-line last:border-b-0 ">
      <div className="flex items-center gap-2 px-3 py-2.5">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <span className="text-fg-muted ">{icon}</span>
          <span className="truncate text-xs font-semibold text-fg-secondary ">{title}</span>
          {typeof count === 'number' && (
            <span className="rounded-tag bg-subtle px-1.5 py-0.5 text-xs text-fg-muted ">
              {count}
            </span>
          )}
          <IconChevronDown aria-hidden="true" className={`ml-auto h-4 w-4 text-fg-muted transition-transform ${open ? 'rotate-180' : ''}`} />
        </button>
        {actions}
      </div>
      {open && <div className="px-3 pb-3">{children}</div>}
    </section>
  );
}

function EmptySection({ children }: { children: React.ReactNode }) {
  return <div className="rounded-control border border-dashed border-line px-3 py-4 text-center text-xs text-fg-muted ">{children}</div>;
}

export default function SessionContextPanel({
  sessionId,
  snapshot,
  loading,
  loadingMore = false,
  onLoadMore,
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
  const [rootHasMore, setRootHasMore] = useState(false);
  const [rootNextOffset, setRootNextOffset] = useState<number | null>(null);
  const [showFolderInput, setShowFolderInput] = useState(false);
  const [folderPath, setFolderPath] = useState('');
  const [folderBusy, setFolderBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const scope = useMemo(() => ({ sessionId, active: true }), [sessionId]);
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const navigationRef = useRef<AbortController | null>(null);
  const consumedResourceRef = useRef<string | null>(null);

  const cancelNavigation = useCallback(() => {
    navigationRef.current?.abort();
    navigationRef.current = null;
    setPreviewLoading(false);
    setRootLoading(false);
    setFullscreen(false);
    setActionError(null);
  }, []);

  const beginNavigation = useCallback(() => {
    if (scopeRef.current !== scope || !scope.active) return null;
    cancelNavigation();
    const controller = new AbortController();
    navigationRef.current = controller;
    return {
      signal: controller.signal,
      isCurrent: () => scopeRef.current === scope && scope.active
        && navigationRef.current === controller && !controller.signal.aborted,
    };
  }, [cancelNavigation, scope]);

  useEffect(() => {
    scope.active = true;
    consumedResourceRef.current = null;
    cancelNavigation();
    setPreview(null);
    setRoot(null);
    setRootPath('');
    setRootItems([]);
    setRootHasMore(false);
    setRootNextOffset(null);
    setFolderBusy(false);
    setFolderPath('');
    setShowFolderInput(false);
    return () => {
      scope.active = false;
      navigationRef.current?.abort();
      navigationRef.current = null;
    };
  }, [cancelNavigation, scope]);

  const closePanel = () => {
    cancelNavigation();
    onClose();
  };
  const backFromPreview = () => {
    cancelNavigation();
    setPreview(null);
  };
  const backFromRoot = () => {
    cancelNavigation();
    setRoot(null);
    setRootItems([]);
    setRootPath('');
    setRootHasMore(false);
    setRootNextOffset(null);
  };

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

  const openResource = useCallback(async (resource: SessionContextFile | string) => {
    const request = beginNavigation();
    if (!request) return;
    setPreviewLoading(true);
    try {
      const file = typeof resource === 'string'
        ? await sessionApi.getContextFile(sessionId, resource, request.signal)
        : resource;
      if (!request.isCurrent()) return;
      const node = toWorkspaceNode(file);
      const fileAccess: PreviewFileAccess = {
        previewUrl: (resourceId) => sessionApi.contextFilePreviewUrl(sessionId, resourceId),
        downloadUrl: (resourceId) => sessionApi.contextFileDownloadUrl(sessionId, resourceId),
      };
      const content = file.isTextFile && file.status !== 'missing'
        ? await sessionApi.readContextFile(sessionId, file.resourceID, request.signal)
        : null;
      if (!request.isCurrent()) return;
      setPreview({
        node,
        fileAccess,
        content: content?.content ?? null,
        truncated: content?.truncated ?? false,
        previewLimitBytes: content?.previewLimitBytes ?? null,
        downloadUrl: fileAccess.downloadUrl(file.resourceID),
      });
      setRoot(null);
    } catch (error) {
      if (request.isCurrent()) setActionError(extractErrorMessage(error, 'Request failed'));
    } finally {
      if (request.isCurrent()) {
        navigationRef.current = null;
        setPreviewLoading(false);
      }
    }
  }, [beginNavigation, sessionId]);

  useEffect(() => {
    if (!requestedResourceID) {
      consumedResourceRef.current = null;
      return;
    }
    if (consumedResourceRef.current === requestedResourceID) return;
    consumedResourceRef.current = requestedResourceID;
    const file = snapshot?.sessionID === sessionId
      ? [...snapshot.outputs, ...snapshot.contextFiles].find((item) => item.resourceID === requestedResourceID)
      : undefined;
    onRequestedResourceConsumed?.();
    // Message cards may point outside the loaded history. Resolve metadata directly.
    void openResource(file ?? requestedResourceID);
  }, [onRequestedResourceConsumed, openResource, requestedResourceID, sessionId, snapshot]);

  const openRootFile = useCallback(async (selectedRoot: SessionContextRoot, item: SessionContextRootNode) => {
    const request = beginNavigation();
    if (!request) return;
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
        ? await sessionApi.readContextRootFile(sessionId, selectedRoot.id, item.path, request.signal)
        : null;
      if (!request.isCurrent()) return;
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
      if (request.isCurrent()) setActionError(extractErrorMessage(error, 'Request failed'));
    } finally {
      if (request.isCurrent()) {
        navigationRef.current = null;
        setPreviewLoading(false);
      }
    }
  }, [beginNavigation, sessionId]);

  const loadRoot = useCallback(async (selectedRoot: SessionContextRoot, path = '', offset = 0) => {
    if (offset > 0 && navigationRef.current) return;
    const request = beginNavigation();
    if (!request) return;
    setRootLoading(true);
    setRoot(selectedRoot);
    setRootPath(path);
    setPreview(null);
    if (offset === 0) {
      setRootItems([]);
      setRootHasMore(false);
      setRootNextOffset(null);
    }
    try {
      const response = await sessionApi.listContextRoot(sessionId, selectedRoot.id, { path, offset }, request.signal);
      if (!request.isCurrent()) return;
      setRootItems((current) => offset === 0 ? response.items : [
        ...current, ...response.items.filter((item) => !current.some((loaded) => loaded.path === item.path)),
      ]);
      setRootHasMore(response.hasMore);
      setRootNextOffset(response.nextOffset);
    } catch (error) {
      if (request.isCurrent()) setActionError(extractErrorMessage(error, 'Request failed'));
    } finally {
      if (request.isCurrent()) {
        navigationRef.current = null;
        setRootLoading(false);
      }
    }
  }, [beginNavigation, sessionId]);

  const addFolder = useCallback(async () => {
    const path = folderPath.trim();
    if (!path) return;
    setActionError(null);
    setFolderBusy(true);
    try {
      await sessionApi.addContextFolder(sessionId, path);
      if (scopeRef.current !== scope || !scope.active) return;
      setFolderPath('');
      setShowFolderInput(false);
      await onRefresh();
    } catch (error) {
      if (scopeRef.current === scope && scope.active) setActionError(extractErrorMessage(error, 'Request failed'));
    } finally {
      if (scopeRef.current === scope && scope.active) setFolderBusy(false);
    }
  }, [folderPath, onRefresh, scope, sessionId]);

  const removeFolder = useCallback(async (selectedRoot: SessionContextRoot) => {
    setActionError(null);
    try {
      await sessionApi.removeContextFolder(sessionId, selectedRoot.id);
      if (scopeRef.current !== scope || !scope.active) return;
      if (root?.id === selectedRoot.id) {
        cancelNavigation();
        setRoot(null);
        setRootItems([]);
        setRootPath('');
        setPreview(null);
      }
      await onRefresh();
    } catch (error) {
      if (scopeRef.current === scope && scope.active) setActionError(extractErrorMessage(error, 'Request failed'));
    }
  }, [cancelNavigation, onRefresh, root?.id, scope, sessionId]);

  const previewPending = previewLoading && (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 top-[52px] z-10 grid place-items-center bg-surface "><LoadingSpinner /></div>
  );

  if (preview) {
    return (
      <div className="relative flex h-full min-h-0 flex-col bg-surface ">
        <div className="flex h-[52px] flex-shrink-0 items-center gap-2 border-b border-line px-3 ">
          <button type="button" onClick={backFromPreview} aria-label={t('context.back')} className="ui-btn-icon inline-flex items-center justify-center rounded-control p-1.5 text-fg-muted hover:bg-hover ">
            <IconArrowLeft aria-hidden="true" className="h-4 w-4" />
          </button>
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-fg ">{preview.node.name}</span>
          <a href={preview.downloadUrl} download={preview.node.name} className="rounded-tag p-1.5 text-fg-muted hover:bg-hover " title={t('context.download')}>
            <IconDownload aria-hidden="true" className="h-4 w-4" />
          </a>
          <button type="button" onClick={() => setFullscreen(true)} className="ui-btn-icon inline-flex items-center justify-center rounded-control p-1.5 text-fg-muted hover:bg-hover " title={t('context.fullscreen')}>
            <IconArrowsDiagonal aria-hidden="true" className="h-4 w-4" />
          </button>
          <button type="button" onClick={closePanel} aria-label={t('context.close')} className="ui-btn-icon inline-flex items-center justify-center rounded-control p-1.5 text-fg-muted hover:bg-hover ">
            <IconX aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>
        {actionError && <div className="border-b border-danger bg-danger-soft px-3 py-2 text-xs text-danger">{actionError}</div>}
        <div className="min-h-0 flex-1">
          {/* Loading preview translations must not suspend and reset the Session panel. */}
          <Suspense fallback={<LoadingSpinner className="py-10" />}>
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
          </Suspense>
        </div>
        {previewPending}
        {fullscreen && (
          <Suspense fallback={null}>
            <PreviewModal
              node={preview.node}
              content={preview.content}
              truncated={preview.truncated}
              previewLimitBytes={preview.previewLimitBytes}
              fileAccess={preview.fileAccess}
              onClose={() => setFullscreen(false)}
            />
          </Suspense>
        )}
      </div>
    );
  }

  if (root) {
    const parentPath = rootPath.includes('/') ? rootPath.slice(0, rootPath.lastIndexOf('/')) : '';
    return (
      <div className="relative flex h-full min-h-0 flex-col bg-surface ">
        <div className="flex h-[52px] flex-shrink-0 items-center gap-2 border-b border-line px-3 ">
          <button type="button" onClick={backFromRoot} aria-label={t('context.back')} className="ui-btn-icon inline-flex items-center justify-center rounded-control p-1.5 text-fg-muted hover:bg-hover ">
            <IconArrowLeft aria-hidden="true" className="h-4 w-4" />
          </button>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium text-fg ">{root.displayName}</div>
            {rootPath && <div className="truncate text-xs text-fg-muted">{rootPath}</div>}
          </div>
          <button type="button" onClick={closePanel} aria-label={t('context.close')} className="ui-btn-icon inline-flex items-center justify-center rounded-control p-1.5 text-fg-muted hover:bg-hover "><IconX aria-hidden="true" className="h-4 w-4" /></button>
        </div>
        {actionError && <div className="border-b border-danger bg-danger-soft px-3 py-2 text-xs text-danger">{actionError}</div>}
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {rootPath && (
            <button type="button" onClick={() => void loadRoot(root, parentPath)} className="mb-2 flex w-full items-center gap-2 rounded-control px-2 py-2 text-xs text-fg-muted hover:bg-hover ">
              <IconArrowLeft aria-hidden="true" className="h-4 w-4" />
              {t('context.parentFolder')}
            </button>
          )}
          {rootLoading && rootItems.length === 0 ? <div className="py-8"><LoadingSpinner /></div> : rootItems.length === 0 && !rootHasMore ? (
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
                  className="flex w-full items-center gap-2 rounded-control px-2 py-2 text-left hover:bg-hover "
                >
                  {item.type === 'directory' ? <IconFolder aria-hidden="true" className="h-4 w-4 text-warning" /> : <IconFileText aria-hidden="true" className="h-4 w-4 text-fg-muted" />}
                  <span className="min-w-0 flex-1 truncate text-xs text-fg-secondary ">{item.name}</span>
                  {item.type === 'file' && item.size != null && <span className="text-xs text-fg-muted">{formatBytes(item.size)}</span>}
                </button>
              ))}
            </div>
          )}
          {rootHasMore && rootNextOffset != null && (
            <button type="button" disabled={rootLoading || previewLoading} onClick={() => void loadRoot(root, rootPath, rootNextOffset)} className="mt-3 w-full rounded-control border border-line px-3 py-2 text-xs text-fg-secondary disabled:bg-disabled disabled:text-fg-disabled ">
              {t(rootLoading ? 'context.loadingMore' : 'context.loadMore')}
            </button>
          )}
        </div>
        {previewPending}
      </div>
    );
  }

  return (
    <div className="relative flex h-full min-h-0 flex-col bg-surface ">
      <div className="flex h-[52px] flex-shrink-0 items-center gap-2 border-b border-line px-3 ">
        {previewLoading ? (
          <button type="button" onClick={cancelNavigation} aria-label={t('context.back')} className="ui-btn-icon inline-flex items-center justify-center rounded-control p-1.5 text-fg-muted"><IconArrowLeft aria-hidden="true" className="h-4 w-4" /></button>
        ) : <IconSparkles aria-hidden="true" className="h-4 w-4 text-ai" />}
        <span className="min-w-0 flex-1 truncate text-sm font-semibold text-fg ">{t('context.title')}</span>
        <button type="button" onClick={() => void onRefresh()} className="ui-btn-icon inline-flex items-center justify-center rounded-control p-1.5 text-fg-muted hover:bg-hover " title={t('context.refresh')}>
          <IconRefresh aria-hidden="true" className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
        <button type="button" onClick={closePanel} aria-label={t('context.close')} className="ui-btn-icon inline-flex items-center justify-center rounded-control p-1.5 text-fg-muted hover:bg-hover "><IconX aria-hidden="true" className="h-4 w-4" /></button>
      </div>

      <div className="flex items-center gap-2 border-b border-line px-3 py-2 ">
        <div className="relative min-w-0 flex-1">
          <IconSearch aria-hidden="true" className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('context.searchPlaceholder')}
            className="ui-input h-8 w-full rounded-control border border-line bg-subtle pl-8 pr-2 text-xs focus:border-line-control"
          />
        </div>
        <IconAdjustmentsHorizontal aria-hidden="true" className="h-4 w-4 text-fg-muted" />
        <select value={filter} onChange={(event) => setFilter(event.target.value as FileFilter)} className="ui-select h-8 rounded-control border border-line bg-surface px-2 text-xs text-fg-secondary">
          <option value="all">{t('context.filter.all')}</option>
          <option value="outputs">{t('context.outputs')}</option>
          <option value="context">{t('context.contextFiles')}</option>
        </select>
        <select value={sort} onChange={(event) => setSort(event.target.value as FileSort)} className="ui-select h-8 rounded-control border border-line bg-surface px-2 text-xs text-fg-secondary">
          <option value="updated">{t('context.sort.updated')}</option>
          <option value="name">{t('context.sort.name')}</option>
          <option value="size">{t('context.sort.size')}</option>
        </select>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {(actionError || error) && <div className="m-3 rounded-control border border-danger bg-danger-soft px-3 py-2 text-xs text-danger">{actionError || error}</div>}
        {snapshot?.hasMore && snapshot?.nextBefore && onLoadMore && (
          <button type="button" disabled={loading} onClick={() => void onLoadMore()} className="mx-3 mt-3 rounded-control border border-line px-3 py-2 text-xs text-fg-secondary disabled:bg-disabled disabled:text-fg-disabled ">
            {t(loadingMore ? 'context.loadingMore' : 'context.loadEarlier')}
          </button>
        )}
        {loading && !snapshot ? <div className="py-10"><LoadingSpinner /></div> : (
          <>
            <Section title={t('context.progress')} count={snapshot?.progress.length || 0} icon={<IconAdjustmentsHorizontal aria-hidden="true" className="h-4 w-4" />} defaultOpen={Boolean(snapshot?.progress.length)}>
              {snapshot?.progress.length ? <TodoList items={snapshot.progress} /> : <EmptySection>{t('context.noProgress')}</EmptySection>}
            </Section>

            <Section title={t('context.outputs')} count={snapshot?.outputs.length || 0} icon={<IconSparkles aria-hidden="true" className="h-4 w-4" />}>
              {outputFiles.length === 0 ? <EmptySection>{t('context.noOutputs')}</EmptySection> : (
                <FileRows files={outputFiles} onOpen={openResource} onFocusMessage={onFocusMessage} sessionId={sessionId} />
              )}
            </Section>

            <Section
              title={t('context.contextFiles')}
              count={(snapshot?.contextFiles.length || 0) + (snapshot?.roots.length || 0)}
              icon={<IconFolder aria-hidden="true" className="h-4 w-4" />}
              actions={snapshot?.canManageFolders ? (
                <button type="button" onClick={() => setShowFolderInput((value) => !value)} className="ui-btn-icon inline-flex items-center justify-center rounded-control p-1 text-fg-muted hover:bg-hover hover:text-fg-secondary " title={t('context.addFolder')}>
                  <IconFolderPlus aria-hidden="true" className="h-4 w-4" />
                </button>
              ) : undefined}
            >
              {snapshot?.canManageFolders && showFolderInput && (
                <div className="mb-2 flex gap-1.5">
                  <input value={folderPath} onChange={(event) => setFolderPath(event.target.value)} placeholder={t('context.folderPathPlaceholder')} className="ui-input h-8 min-w-0 flex-1 rounded-control border border-line px-2 text-xs" />
                  <button type="button" disabled={folderBusy || !folderPath.trim()} onClick={() => void addFolder()} className="rounded-control bg-subtle px-2.5 text-xs text-fg disabled:bg-disabled disabled:text-fg-disabled ">
                    {folderBusy ? '…' : t('context.add')}
                  </button>
                </div>
              )}
              <div className="space-y-1">
                {(snapshot?.roots || []).map((item) => (
                  <div key={item.id} className="group flex items-center gap-1 rounded-control hover:bg-hover ">
                    <button type="button" disabled={item.status !== 'available'} onClick={() => void loadRoot(item)} className="flex min-w-0 flex-1 items-center gap-2 px-2 py-2 text-left disabled:bg-disabled disabled:text-fg-disabled">
                      <IconFolder aria-hidden="true" className="h-4 w-4 text-warning" />
                      <span className="min-w-0 flex-1 truncate text-xs text-fg-secondary ">{item.displayName}</span>
                      <span className="text-xs text-fg-muted">{item.kind === 'project' ? t('context.project') : t('context.folder')}</span>
                    </button>
                    {item.kind === 'folder' && (
                      <button aria-label={pageActionLabel('close')} type="button" onClick={() => void removeFolder(item)} className="ui-btn-icon inline-flex items-center justify-center mr-1 rounded-control p-1 text-fg-placeholder opacity-0 hover:bg-hover hover:text-danger group-hover:opacity-100 "><IconX aria-hidden="true" className="h-4 w-4" /></button>
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
              <Section title={t('context.skills')} count={snapshot?.skills.length || 0} icon={<IconSparkles aria-hidden="true" className="h-4 w-4" />} defaultOpen={false}>
                <div className="space-y-1">
                  {snapshot!.skills.map((skill) => <SkillRow key={skill.name} skill={skill} />)}
                </div>
              </Section>
            )}
          </>
        )}
      </div>
      {previewPending}
    </div>
  );
}

function SkillRow({ skill }: { skill: SessionContextSkill }) {
  const { t } = useTranslation('session');
  const failed = skill.status === 'error';
  const error = failed ? skill.error : undefined;
  const label = skill.status === 'loaded' ? t('context.loaded')
    : skill.status === 'loading' ? t('context.loading')
      : failed ? t('context.failed') : t('context.unknown');
  const rowClassName = 'flex items-center gap-2 rounded-control px-2 py-2 text-xs text-fg-secondary ';
  const row = (
    <>
      <IconSparkles aria-hidden="true" className="h-4 w-4 shrink-0 text-ai" />
      <span className="min-w-0 flex-1 truncate">{skill.name}</span>
      <span className={`shrink-0 text-xs ${failed ? 'text-danger ' : 'text-fg-muted'}`}>{label}</span>
    </>
  );
  if (!error) return <div className={rowClassName}>{row}</div>;
  return <SkillError key={error} error={error} rowClassName={rowClassName}>{row}</SkillError>;
}

function SkillError({ error, rowClassName, children }: { error: string; rowClassName: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const errorId = useId();
  return (
    <div className="rounded-control">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={errorId}
        onClick={() => setOpen((value) => !value)}
        className={`${rowClassName} w-full text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-focus`}
      >
        {children}
        <IconChevronDown aria-hidden="true" className={`h-4 w-4 shrink-0 text-fg-muted ${open ? 'rotate-180' : ''}`} />
      </button>
      <p id={errorId} hidden={!open} className="whitespace-pre-wrap px-2 pb-2 text-xs text-danger [overflow-wrap:anywhere] ">{error}</p>
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
        <div key={file.fileKey || file.resourceID} className="group flex items-center gap-1 rounded-control hover:bg-hover ">
          <button type="button" disabled={file.status === 'missing'} onClick={() => void onOpen(file)} className="flex min-w-0 flex-1 items-center gap-2 px-2 py-2 text-left disabled:bg-disabled disabled:text-fg-disabled">
            <IconFileText aria-hidden="true" className="h-4 w-4 text-fg-muted" />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs font-medium text-fg-secondary ">{file.displayName}</span>
              <span className="block truncate text-xs text-fg-muted">{file.logicalPath}</span>
            </span>
            {file.size != null && <span className="text-xs text-fg-muted">{formatBytes(file.size)}</span>}
          </button>
          <button type="button" onClick={() => onFocusMessage(file.sourceMessageID)} className="ui-btn-icon inline-flex items-center justify-center rounded-control p-1 text-fg-placeholder opacity-0 hover:bg-hover hover:text-fg-secondary group-hover:opacity-100 " title={t('context.locateMessage')}>
            <IconArrowLeft aria-hidden="true" className="h-4 w-4" />
          </button>
          <a href={sessionApi.contextFileDownloadUrl(sessionId, file.resourceID)} download={file.displayName} className="mr-1 rounded-tag p-1 text-fg-placeholder opacity-0 hover:bg-hover hover:text-fg-secondary group-hover:opacity-100 " title={t('context.download')}>
            <IconDownload aria-hidden="true" className="h-4 w-4" />
          </a>
        </div>
      ))}
    </div>
  );
}

import { pageActionLabel } from '../pageActionLabel';
import { useState, useEffect, useCallback, useMemo, useRef, useReducer } from 'react';
import { useLocation } from 'react-router-dom';
import KnowledgeTab from './KnowledgeTab';
import {
  IconAlertTriangle,
  IconArrowLeft,
  IconArrowsDiagonal,
  IconBrain,
  IconChevronDown,
  IconChevronRight,
  IconChevronUp,
  IconDatabase,
  IconDeviceFloppy,
  IconDownload,
  IconEdit,
  IconFolderOpen,
  IconFolderPlus,
  IconRefresh,
  IconSearch,
  IconTrash,
  IconUpload,
  IconX,
} from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { useToast } from '@/components/common/Toast';
import { useConfirm } from '@/components/common/ConfirmDialog';
import { extractErrorMessage } from '@/utils/error';
import {
  workspaceAPI, WorkspaceNode, WorkspaceProject, formatBytes, formatDate, fileIcon,
} from '@/api/workspace';
import { FilePreviewRenderer, PreviewModal, type PreviewFileAccess } from '@/components/common/FilePreview';
import { WorkspaceFileSplit, FileListTable, FileListRow, FileListActions, PreviewPanelHeader, fileActionClass, previewActionClass } from '@/components/common/WorkspaceFileView';

// ─── Types ────────────────────────────────────────────────────────────────

type Tab = 'files' | 'memory' | 'knowledge';
type SortField = 'name' | 'size' | 'modified';
type SortDirection = 'asc' | 'desc';
const WORKSPACE_PREVIEW_FILE_ACCESS: PreviewFileAccess = {
  previewUrl: (path) => workspaceAPI.previewUrl(path),
  downloadUrl: (path) => workspaceAPI.downloadUrl(path),
};

const MEMORY_PREVIEW_FILE_ACCESS: PreviewFileAccess = {
  previewUrl: (path) => workspaceAPI.memoryPreviewUrl(path),
  downloadUrl: (path) => workspaceAPI.memoryDownloadUrl(path),
};

interface SortState {
  field: SortField;
  direction: SortDirection;
}

// Preview/edit panel state consolidated into a single object
interface PanelState {
  node: WorkspaceNode | null;
  content: string | null;
  editContent: string | null;
  truncated: boolean;
  previewLimitBytes: number | null;
  editing: boolean;
  saving: boolean;
}

const PANEL_INIT: PanelState = {
  node: null, content: null, editContent: null, truncated: false, previewLimitBytes: null, editing: false, saving: false,
};

type PanelAction =
  | { type: 'select'; node: WorkspaceNode }
  | { type: 'content_loaded'; content: string; truncated?: boolean; previewLimitBytes?: number | null }
  | { type: 'start_edit' }
  | { type: 'edit_change'; text: string }
  | { type: 'save_start' }
  | { type: 'save_done'; content: string }
  | { type: 'cancel_edit' }
  | { type: 'close' };

function panelReducer(state: PanelState, action: PanelAction): PanelState {
  switch (action.type) {
    case 'select':
      return { ...PANEL_INIT, node: action.node };
    case 'content_loaded':
      return {
        ...state,
        content: action.content,
        truncated: action.truncated ?? false,
        previewLimitBytes: action.previewLimitBytes ?? null,
      };
    case 'start_edit':
      return { ...state, editing: true, editContent: state.content ?? '' };
    case 'edit_change':
      return { ...state, editContent: action.text };
    case 'save_start':
      return { ...state, saving: true };
    case 'save_done':
      return {
        ...state,
        saving: false,
        editing: false,
        editContent: null,
        content: action.content,
        // Keep the header metadata in sync with what was just written; the
        // directory reload below preserves the panel instead of closing it.
        node: state.node
          ? { ...state.node, size: new TextEncoder().encode(action.content).length, modified_at: Date.now() / 1000 }
          : state.node,
      };
    case 'cancel_edit':
      return { ...state, editing: false, editContent: null };
    case 'close':
      return PANEL_INIT;
    default:
      return state;
  }
}

// ─── Main Page ────────────────────────────────────────────────────────────

export default function WorkspacePage() {
  const location = useLocation();
  const [activeTab, setActiveTab] = useState<Tab>(() => new URLSearchParams(location.search).get('tab') === 'knowledge' ? 'knowledge' : 'files');
  const { t } = useTranslation('workspace');
  useEffect(() => {
    setActiveTab(new URLSearchParams(location.search).get('tab') === 'knowledge' ? 'knowledge' : 'files');
  }, [location.key, location.search]);

  return (
    <div className="flex h-[calc(100vh-3rem)] min-h-[560px] flex-col">
      <PageHeader
        title="Workspace"
        description={t('description')}
        icon={<IconFolderOpen aria-hidden="true" className="w-8 h-8" />}
      />

      <div className="flex gap-1 px-1 mb-4 border-b border-line">
        <TabButton active={activeTab === 'files'} onClick={() => setActiveTab('files')} icon={<IconFolderOpen aria-hidden="true" className="w-4 h-4" />} label={t('tabs.files')} />
        <TabButton active={activeTab === 'memory'} onClick={() => setActiveTab('memory')} icon={<IconBrain aria-hidden="true" className="w-4 h-4" />} label={t('tabs.memory')} />
        <TabButton active={activeTab === 'knowledge'} onClick={() => setActiveTab('knowledge')} icon={<IconDatabase aria-hidden="true" className="w-4 h-4" />} label={t('tabs.knowledge')} />
      </div>

      <div className="min-h-0 flex-1 overflow-hidden">
        {activeTab === 'files' ? <FilesTab /> : activeTab === 'memory' ? <MemoryTab /> : null}
        <KnowledgeTab active={activeTab === 'knowledge'} />
      </div>
    </div>
  );
}

function TabButton({ active, onClick, icon, label }: {
  active: boolean; onClick: () => void; icon: React.ReactNode; label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px ${
        active ? 'border-line-control text-fg' : 'border-transparent text-fg-muted hover:text-fg-secondary hover:border-line-control'
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

function SortHeaderButton({
  label,
  field,
  sort,
  onClick,
  align = 'left',
}: {
  label: string;
  field: SortField;
  sort: SortState;
  onClick: (field: SortField) => void;
  align?: 'left' | 'right';
}) {
  const active = sort.field === field;
  const Icon = sort.direction === 'asc' ? IconChevronUp : IconChevronDown;

  return (
    <button
      type="button"
      onClick={() => onClick(field)}
      className={`inline-flex items-center gap-1 rounded-tag px-1 py-0.5 transition-colors hover:bg-hover hover:text-fg-secondary ${
        align === 'right' ? 'justify-end' : 'justify-start'
      }`}
    >
      <span>{label}</span>
      <Icon aria-hidden="true" className={`w-4 h-4 ${active ? 'opacity-100' : 'opacity-0'}`} />
    </button>
  );
}

// ─── Files Tab ────────────────────────────────────────────────────────────

function FilesTab() {
  const { success: toastSuccess, error: toastError } = useToast();
  const confirm = useConfirm();
  const { t } = useTranslation('workspace');

  // Navigation state
  const [loading, setLoading] = useState(true);
  const [currentPath, setCurrentPath] = useState('');
  const [items, setItems] = useState<WorkspaceNode[]>([]);
  const [sort, setSort] = useState<SortState>({ field: 'name', direction: 'asc' });

  // Preview/edit panel — consolidated into a reducer
  const [panel, dispatchPanel] = useReducer(panelReducer, PANEL_INIT);

  // IconUpload / new-dir state
  const [uploading, setUploading] = useState(false);
  const [newDir, setNewDir] = useState<{ show: boolean; name: string }>({ show: false, name: '' });
  const [dragOver, setDragOver] = useState(false);
  const [previewModalOpen, setPreviewModalOpen] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const latestDirRequestIdRef = useRef(0);
  const didInitRef = useRef(false);

  const loadFileContent = useCallback(async (path: string) => {
    const res = await workspaceAPI.readFile(path);
    dispatchPanel({
      type: 'content_loaded',
      content: res.data.content,
      truncated: res.data.truncated,
      previewLimitBytes: res.data.preview_limit_bytes,
    });
  }, []);

  const loadDir = useCallback(async (path: string, options?: { preservePanel?: boolean }) => {
    const requestId = latestDirRequestIdRef.current + 1;
    latestDirRequestIdRef.current = requestId;
    setLoading(true);
    if (!options?.preservePanel) {
      dispatchPanel({ type: 'close' });
    }
    try {
      const res = await workspaceAPI.list(path);
      if (requestId !== latestDirRequestIdRef.current) {
        return;
      }
      setItems(Array.isArray(res.data) ? res.data : []);
      setCurrentPath(path);
    } catch (e: any) {
      if (requestId !== latestDirRequestIdRef.current) {
        return;
      }
      toastError(t('files.toast.loadDirFailed'), extractErrorMessage(e));
    } finally {
      if (requestId === latestDirRequestIdRef.current) {
        setLoading(false);
      }
    }
  }, [t, toastError]);

  useEffect(() => {
    if (didInitRef.current) {
      return;
    }
    didInitRef.current = true;
    loadDir('');
  }, [loadDir]);

  const isOutputsDirectory = currentPath === 'outputs' || currentPath.startsWith('outputs/');

  useEffect(() => {
    setSort({ field: 'name', direction: isOutputsDirectory ? 'desc' : 'asc' });
  }, [isOutputsDirectory]);

  const handleSelectNode = useCallback(async (node: WorkspaceNode) => {
    if (node.type === 'directory') {
      loadDir(node.path);
      return;
    }
    dispatchPanel({ type: 'select', node });
    if (node.is_text_file) {
      try {
        await loadFileContent(node.path);
      } catch (e: any) {
        toastError(t('files.toast.readFileFailed'), extractErrorMessage(e));
      }
    }
  }, [loadDir, loadFileContent, toastError, t]);

  const handleRefresh = useCallback(async () => {
    await loadDir(currentPath, { preservePanel: true });

    if (panel.node?.is_text_file) {
      try {
        await loadFileContent(panel.node.path);
      } catch (e: any) {
        toastError(t('files.toast.readFileFailed'), extractErrorMessage(e));
      }
    }
  }, [currentPath, loadDir, loadFileContent, panel.node, toastError, t]);

  const handleSave = useCallback(async () => {
    if (!panel.node || panel.editContent === null || panel.truncated) return;
    dispatchPanel({ type: 'save_start' });
    try {
      await workspaceAPI.writeFile(panel.node.path, panel.editContent);
      dispatchPanel({ type: 'save_done', content: panel.editContent });
      toastSuccess(t('files.toast.saveSuccess'));
      // Reload sizes/mtimes in the listing without closing the file the user just saved.
      loadDir(currentPath, { preservePanel: true });
    } catch (e: any) {
      dispatchPanel({ type: 'cancel_edit' });
      toastError(t('files.toast.saveFailed'), extractErrorMessage(e));
    }
  }, [panel.node, panel.editContent, currentPath, loadDir, toastError, toastSuccess, t]);

  const handleDelete = useCallback(async (node: WorkspaceNode) => {
    const ok = await confirm({
      title: t('files.confirm.deleteTitle'),
      description: t('files.confirm.deleteDesc', { name: node.name }),
      confirmText: t('files.confirm.deleteBtn'),
      variant: 'danger',
    });
    if (!ok) return;
    try {
      if (node.type === 'file') {
        await workspaceAPI.deleteFile(node.path);
      } else {
        await workspaceAPI.deleteDir(node.path);
      }
      toastSuccess(t('files.toast.deleteSuccess'));
      if (panel.node?.path === node.path) dispatchPanel({ type: 'close' });
      loadDir(currentPath);
    } catch (e: any) {
      toastError(t('files.toast.deleteFailed'), extractErrorMessage(e));
    }
  }, [confirm, panel.node, currentPath, loadDir, toastError, toastSuccess, t]);

  const handleUpload = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      const res = await workspaceAPI.upload(Array.from(files), currentPath);
      const uploaded = res.data.uploaded;
      const errors = uploaded.filter((u) => u.error);
      const ok = uploaded.filter((u) => !u.error);
      if (ok.length > 0) toastSuccess(t('files.toast.uploadSuccess', { count: ok.length }));
      if (errors.length > 0) toastError(t('files.toast.uploadPartialFail', { count: errors.length }), errors.map((e) => e.error).join('; '));
      loadDir(currentPath);
    } catch (e: any) {
      toastError(t('files.toast.uploadFailed'), extractErrorMessage(e));
    } finally {
      setUploading(false);
    }
  }, [currentPath, loadDir, toastError, toastSuccess, t]);

  const handleCreateDir = useCallback(async () => {
    const name = newDir.name.trim();
    if (!name) return;
    const path = currentPath ? `${currentPath}/${name}` : name;
    try {
      await workspaceAPI.createDir(path);
      setNewDir({ show: false, name: '' });
      loadDir(currentPath);
    } catch (e: any) {
      toastError(t('files.toast.createDirFailed'), extractErrorMessage(e));
    }
  }, [newDir.name, currentPath, loadDir, toastError, t]);

  const handleReveal = useCallback(async (node: WorkspaceNode) => {
    try {
      await workspaceAPI.reveal(node.path);
      toastSuccess(t('files.toast.revealSuccess'));
    } catch (e: any) {
      toastError(t('files.toast.revealFailed'), extractErrorMessage(e));
    }
  }, [toastError, toastSuccess, t]);

  const handleSort = useCallback((field: SortField) => {
    setSort((current) => ({
      field,
      direction: current.field === field && current.direction === 'asc' ? 'desc' : 'asc',
    }));
  }, []);

  const sortedItems = useMemo(() => {
    const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
    return [...items].sort((a, b) => {
      let result = 0;
      if (sort.field === 'name') {
        result = collator.compare(a.name, b.name);
      } else if (sort.field === 'size') {
        result = (a.type === 'file' ? (a.size ?? 0) : 0) - (b.type === 'file' ? (b.size ?? 0) : 0);
      } else {
        result = (a.modified_at ?? 0) - (b.modified_at ?? 0);
      }
      if (result === 0) {
        result = collator.compare(a.name, b.name);
      }
      return sort.direction === 'asc' ? result : -result;
    });
  }, [items, sort]);

  const breadcrumbs = currentPath ? ['', ...currentPath.split('/')] : [''];

  const renderList = (fileListWidth: number) => {
    const showSizeColumn = fileListWidth >= 560;
    const showModifiedColumn = fileListWidth >= 760;
    return (
      <>
        {/* Toolbar */}
        <div className="flex items-center gap-2 px-4 py-2 border-b border-line flex-shrink-0">
          <div className="flex items-center gap-1 flex-1 min-w-0 text-sm text-fg-secondary">
            {breadcrumbs.map((crumb, i) => {
              const path = breadcrumbs.slice(1, i + 1).join('/');
              const isLast = i === breadcrumbs.length - 1;
              return (
                <span key={i} className="flex items-center gap-1">
                  {i > 0 && <IconChevronRight aria-hidden="true" className="w-4 h-4 text-fg-placeholder flex-shrink-0" />}
                  <button
                    onClick={() => !isLast && loadDir(path)}
                    className={`truncate ${isLast ? 'text-fg font-medium' : 'text-info hover:underline'}`}
                  >
                    {crumb === '' ? 'workspace' : crumb}
                  </button>
                </span>
              );
            })}
          </div>
          <div className="flex items-center gap-1 flex-shrink-0">
            {currentPath && (
              <button onClick={() => loadDir(currentPath.split('/').slice(0, -1).join('/'))} title={t('files.back')} className="ui-btn-icon inline-flex items-center justify-center p-1.5 text-fg-muted hover:text-fg-secondary hover:bg-hover rounded-control">
                <IconArrowLeft aria-hidden="true" className="w-4 h-4" />
              </button>
            )}
            <button
              onClick={handleRefresh}
              disabled={loading}
              title={t('files.refresh')}
              className="ui-btn-icon inline-flex items-center justify-center p-1.5 text-fg-muted hover:text-fg-secondary hover:bg-hover rounded-control disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed"
            >
              <IconRefresh aria-hidden="true" className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button onClick={() => setNewDir({ show: true, name: '' })} title={t('files.newDir')} className="ui-btn-icon inline-flex items-center justify-center p-1.5 text-fg-muted hover:text-fg-secondary hover:bg-hover rounded-control">
              <IconFolderPlus aria-hidden="true" className="w-4 h-4" />
            </button>
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              title={t('files.upload')}
              className="ui-btn-icon inline-flex items-center justify-center p-1.5 text-fg-muted hover:text-fg-secondary hover:bg-hover rounded-control"
            >
              <IconUpload aria-hidden="true" className="w-4 h-4" />
            </button>
            <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(e) => handleUpload(e.target.files)} />
          </div>
        </div>

        {newDir.show && (
          <div className="flex items-center gap-2 px-4 py-2 bg-subtle border-b border-line">
            <IconFolderPlus aria-hidden="true" className="w-4 h-4 text-fg-secondary flex-shrink-0" />
            <input
              autoFocus
              value={newDir.name}
              onChange={(e) => setNewDir((d) => ({ ...d, name: e.target.value }))}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleCreateDir();
                if (e.key === 'Escape') setNewDir({ show: false, name: '' });
              }}
              placeholder={t('files.dirNamePlaceholder')}
              className="ui-input flex-1 text-sm bg-transparent border-none text-fg"
            />
            <button onClick={handleCreateDir} className="text-xs px-2 py-1 bg-subtle text-fg rounded-tag hover:bg-hover">{t('files.create')}</button>
            <button aria-label={pageActionLabel('close')} onClick={() => setNewDir({ show: false, name: '' })} className="ui-btn-icon inline-flex items-center justify-center text-fg-muted hover:text-fg-secondary">
              <IconX aria-hidden="true" className="w-4 h-4" />
            </button>
          </div>
        )}

        <div
          className={`min-h-0 flex-1 overflow-y-auto relative ${dragOver ? 'ring-2 ring-focus ring-inset bg-info-soft' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); handleUpload(e.dataTransfer.files); }}
        >
          {dragOver && (
            <div className="absolute inset-0 flex items-center justify-center z-10 pointer-events-none">
              <div className="flex flex-col items-center gap-2 text-info">
                <IconUpload aria-hidden="true" className="w-8 h-8" />
                <span className="text-sm font-medium">{t('files.dropHere')}</span>
              </div>
            </div>
          )}

          {loading ? (
            <div className="flex items-center justify-center h-32"><LoadingSpinner /></div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-32 text-fg-muted">
              <IconFolderOpen aria-hidden="true" className="w-8 h-8 mb-2 text-fg-muted" />
              <p className="text-sm">{t('files.emptyDir')}</p>
            </div>
          ) : (
            <FileListTable header={<>
                  <th className="w-8 px-4 py-2 text-left text-xs font-medium text-fg-muted dark:text-fg-muted"></th>
                  <th className="px-2 py-2 text-left text-xs font-medium text-fg-muted dark:text-fg-muted" aria-sort={sort.field === 'name' ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                    <SortHeaderButton label={t('files.columns.name')} field="name" sort={sort} onClick={handleSort} />
                  </th>
                  {showSizeColumn && (
                    <th className="w-24 px-4 py-2 text-right text-xs font-medium text-fg-muted " aria-sort={sort.field === 'size' ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                      <SortHeaderButton label={t('files.columns.size')} field="size" sort={sort} onClick={handleSort} align="right" />
                    </th>
                  )}
                  {showModifiedColumn && (
                    <th className="w-36 px-4 py-2 text-right text-xs font-medium text-fg-muted " aria-sort={sort.field === 'modified' ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                      <SortHeaderButton label={t('files.columns.modified')} field="modified" sort={sort} onClick={handleSort} align="right" />
                    </th>
                  )}
                  <th className="w-20"></th>
            </>}>
                {sortedItems.map((item) => (
                  <FileListRow
                    key={item.path}
                    onClick={() => handleSelectNode(item)}
                    selected={panel.node?.path === item.path}
                  >
                    <td className="px-4 py-2 text-sm">{fileIcon(item)}</td>
                    <td className="max-w-0 truncate px-2 py-2 font-medium text-fg ">
                      <span className="block truncate">{item.name}</span>
                    </td>
                    {showSizeColumn && (
                      <td className="whitespace-nowrap px-4 py-2 text-right tabular-nums text-fg-muted ">
                        {item.type === 'file' ? formatBytes(item.size ?? 0) : '—'}
                      </td>
                    )}
                    {showModifiedColumn && (
                      <td className="whitespace-nowrap px-4 py-2 text-right text-xs text-fg-muted ">
                        {formatDate(item.modified_at)}
                      </td>
                    )}
                    <td className="px-2 py-2">
                      <FileListActions>
                        {item.type === 'file' && (
                          <a href={workspaceAPI.downloadUrl(item.path)} download={item.name} title={t('files.download')} className={fileActionClass}>
                            <IconDownload aria-hidden="true" className="w-3.5 h-3.5" />
                          </a>
                        )}
                        <button onClick={() => handleReveal(item)} title={t('files.reveal')} className={fileActionClass}>
                          <IconFolderOpen aria-hidden="true" className="w-3.5 h-3.5" />
                        </button>
                        <button onClick={() => handleDelete(item)} title={t('files.delete')} className="ui-btn-icon inline-flex items-center justify-center p-1 text-fg-muted hover:text-fg-secondary rounded-control hover:bg-hover">
                          <IconTrash aria-hidden="true" className="w-4 h-4" />
                        </button>
                      </FileListActions>
                    </td>
                  </FileListRow>
                ))}
            </FileListTable>
          )}
        </div>

        {uploading && (
          <div className="px-4 py-2 bg-subtle text-sm text-fg-secondary flex items-center gap-2 border-t border-line">
            <IconRefresh aria-hidden="true" className="w-4 h-4 animate-spin" />
            {t('files.uploading')}
          </div>
        )}
      </>
    );
  };

  return (
    <>
      <WorkspaceFileSplit
        list={renderList}
        preview={panel.node && <>
          <PreviewPanelHeader
            title={panel.node.name}
            icon={fileIcon(panel.node)}
            actions={<>
              {panel.node.is_text_file && !panel.editing && !panel.truncated && (
                <button onClick={() => dispatchPanel({ type: 'start_edit' })} title={t('files.edit')} className="ui-btn-icon inline-flex items-center justify-center p-1.5 text-fg-muted hover:text-fg-secondary hover:bg-hover rounded-control">
                  <IconEdit aria-hidden="true" className="w-4 h-4" />
                </button>
              )}
              {panel.editing && (
                <>
                  <button onClick={handleSave} disabled={panel.saving} title={t('files.save')} className="ui-btn-icon inline-flex items-center justify-center p-1.5 text-success hover:bg-success-soft rounded-control">
                    <IconDeviceFloppy aria-hidden="true" className="w-4 h-4" />
                  </button>
                  <button onClick={() => dispatchPanel({ type: 'cancel_edit' })} title={t('files.cancel')} className="ui-btn-icon inline-flex items-center justify-center p-1.5 text-fg-muted hover:text-fg-secondary hover:bg-hover rounded-control">
                    <IconX aria-hidden="true" className="w-4 h-4" />
                  </button>
                </>
              )}
              <a href={workspaceAPI.downloadUrl(panel.node.path)} download={panel.node.name} title={t('files.download')} className={previewActionClass}>
                <IconDownload aria-hidden="true" className="w-4 h-4" />
              </a>
              <button onClick={() => handleReveal(panel.node!)} title={t('files.reveal')} className={previewActionClass}>
                <IconFolderOpen aria-hidden="true" className="w-4 h-4" />
              </button>
              <button onClick={() => setPreviewModalOpen(true)} title={t('files.preview.fullscreen')} className={previewActionClass}>
                <IconArrowsDiagonal aria-hidden="true" className="w-4 h-4" />
              </button>
              <button onClick={() => dispatchPanel({ type: 'close' })} title={t('files.close')} className={previewActionClass}>
                <IconX aria-hidden="true" className="w-4 h-4" />
              </button>
            </>}
            meta={<>
              <span>{formatBytes(panel.node.size ?? 0)}</span>
              <span>{formatDate(panel.node.modified_at)}</span>
            </>}
          />

          <div className="flex-1 min-h-0 overflow-hidden">
            <FilePreviewRenderer
              node={panel.node}
              content={panel.content}
              editing={panel.editing}
              editContent={panel.editContent}
              truncated={panel.truncated}
              previewLimitBytes={panel.previewLimitBytes}
              fileAccess={WORKSPACE_PREVIEW_FILE_ACCESS}
              onEditChange={(text) => dispatchPanel({ type: 'edit_change', text })}
              onReveal={handleReveal}
            />
          </div>
        </>}
      />
      {panel.node && previewModalOpen && (
        <PreviewModal
          node={panel.node}
          content={panel.content}
          truncated={panel.truncated}
          previewLimitBytes={panel.previewLimitBytes}
          fileAccess={WORKSPACE_PREVIEW_FILE_ACCESS}
          onClose={() => setPreviewModalOpen(false)}
          onReveal={handleReveal}
        />
      )}
    </>
  );
}

// ─── Memory Tab ───────────────────────────────────────────────────────────

type MemoryLoadState = 'idle' | 'loading' | 'error';

function countMemoryFiles(nodes: WorkspaceNode[]): number {
  return nodes.reduce((count, node) => (
    count + (node.type === 'file' ? 1 : countMemoryFiles(node.children ?? []))
  ), 0);
}

function collectMemoryFiles(nodes: WorkspaceNode[]): WorkspaceNode[] {
  return nodes.flatMap((node) => (
    node.type === 'file' ? [node] : collectMemoryFiles(node.children ?? [])
  ));
}

function memoryPathParts(path: string): string[] {
  return path.replace(/\\/g, '/').split('/');
}

function buildMemoryView(
  nodes: WorkspaceNode[],
  visibleProjects: WorkspaceProject[],
): WorkspaceNode[] {
  const userMemory = nodes.find((node) => node.path === 'USER.md');
  const globalMemory = nodes.find((node) => node.path === 'MEMORY.md');
  const projects = nodes.find((node) => node.path === 'projects');
  const daily = nodes.find((node) => node.path === 'daily');
  const projectById = new Map(visibleProjects.map((project) => [project.id, project]));
  const consumedRootPaths = new Set<string>();
  const view: WorkspaceNode[] = [];

  if (userMemory) {
    view.push(userMemory);
    consumedRootPaths.add(userMemory.path);
  }
  if (globalMemory) {
    view.push(globalMemory);
    consumedRootPaths.add(globalMemory.path);
  }
  if (projects) {
    consumedRootPaths.add(projects.path);
    const projectMemories = collectMemoryFiles(projects.children ?? []).flatMap((node) => {
      const pathParts = memoryPathParts(node.path);
      const project = pathParts.length === 3 ? projectById.get(pathParts[1]) : undefined;
      if (!project || pathParts[2] !== 'MEMORY.md') return [];
      return [{
        ...node,
        project_name: project.name?.trim()
          || project.worktree.split(/[\\/]/).filter(Boolean).pop()
          || project.id,
        project_worktree: project.worktree,
      }];
    });
    view.push(...projectMemories);
  }
  if (daily) {
    view.push(daily);
    consumedRootPaths.add(daily.path);
  }
  view.push(...nodes.filter((node) => !consumedRootPaths.has(node.path)));
  return view;
}

function memoryNodeLabel(node: WorkspaceNode): string {
  if (node.project_name) return `${node.project_name} / MEMORY.md`;
  const pathParts = memoryPathParts(node.path);
  if (pathParts.length === 3 && pathParts[0] === 'projects') {
    return `${pathParts[1]}/${pathParts[2]}`;
  }
  return node.name;
}

function filterMemoryTree(nodes: WorkspaceNode[], query: string): WorkspaceNode[] {
  if (!query) return nodes;

  return nodes.flatMap((node) => {
    const searchableText = [node.path, node.project_name, node.project_worktree]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    if (searchableText.includes(query)) return [node];
    if (node.type === 'file') return [];

    const children = filterMemoryTree(node.children ?? [], query);
    return children.length > 0 ? [{ ...node, children }] : [];
  });
}

interface MemoryTreeNodeProps {
  node: WorkspaceNode;
  depth: number;
  expandedPaths: Set<string>;
  forceExpanded: boolean;
  selectedPath?: string;
  onToggle: (path: string) => void;
  onSelect: (node: WorkspaceNode) => void;
}

function MemoryTreeNode({
  node,
  depth,
  expandedPaths,
  forceExpanded,
  selectedPath,
  onToggle,
  onSelect,
}: MemoryTreeNodeProps) {
  const { t } = useTranslation('workspace');
  const isDirectory = node.type === 'directory';
  const isExpanded = forceExpanded || expandedPaths.has(node.path);

  return (
    <div>
      <button
        type="button"
        aria-expanded={isDirectory ? isExpanded : undefined}
        onClick={() => (isDirectory ? onToggle(node.path) : onSelect(node))}
        className={`w-full flex items-center gap-2 py-2.5 pr-3 border-t border-line text-left transition-colors ${
          selectedPath === node.path ? 'bg-ai-soft text-ai' : 'hover:bg-hover text-fg-secondary'
        }`}
        style={{ paddingLeft: `${12 + depth * 16}px` }}
      >
        {isDirectory ? (
          isExpanded
            ? <IconChevronDown aria-hidden="true" className="w-4 h-4 flex-shrink-0 text-fg-muted" />
            : <IconChevronRight aria-hidden="true" className="w-4 h-4 flex-shrink-0 text-fg-muted" />
        ) : (
          <span className="w-3.5 flex-shrink-0" />
        )}
        <span className="text-sm flex-shrink-0">{fileIcon(node)}</span>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium truncate">{memoryNodeLabel(node)}</div>
          {isDirectory ? (
            <div className="text-xs text-fg-muted">
              {t('memory.fileCount', { count: countMemoryFiles(node.children ?? []) })}
            </div>
          ) : (
            <div className="text-xs text-fg-muted">{formatDate(node.modified_at)} · {formatBytes(node.size ?? 0)}</div>
          )}
        </div>
      </button>
      {isDirectory && isExpanded && (node.children ?? []).map((child) => (
        <MemoryTreeNode
          key={child.path}
          node={child}
          depth={depth + 1}
          expandedPaths={expandedPaths}
          forceExpanded={forceExpanded}
          selectedPath={selectedPath}
          onToggle={onToggle}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

function MemoryTab() {
  const { success: toastSuccess, error: toastError } = useToast();
  const { t } = useTranslation('workspace');
  const [files, setFiles] = useState<WorkspaceNode[]>([]);
  const [visibleProjects, setVisibleProjects] = useState<WorkspaceProject[]>([]);
  const [loadState, setLoadState] = useState<MemoryLoadState>('loading');
  const [selected, setSelected] = useState<WorkspaceNode | null>(null);

  // Distinguish "loading content" from "content failed" to avoid
  // the '加载中...' placeholder getting stuck on error.
  const [contentState, setContentState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [content, setContent] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [previewLimitBytes, setPreviewLimitBytes] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [previewModalOpen, setPreviewModalOpen] = useState(false);

  const [search, setSearch] = useState('');
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(new Set());
  const latestMemoryRequestIdRef = useRef(0);

  const load = useCallback(async () => {
    setLoadState('loading');
    try {
      const [memoryResponse, projectResponse] = await Promise.all([
        workspaceAPI.listMemory(),
        workspaceAPI.listVisibleProjects(),
      ]);
      setFiles(Array.isArray(memoryResponse.data) ? memoryResponse.data : []);
      setVisibleProjects(Array.isArray(projectResponse.data) ? projectResponse.data : []);
      setLoadState('idle');
    } catch (e: any) {
      setLoadState('error');
      toastError(t('memory.loadMemoryFailed'), extractErrorMessage(e));
    }
  }, [toastError, t]);

  useEffect(() => { load(); }, [load]);

  const handleSelect = async (node: WorkspaceNode) => {
    const requestId = latestMemoryRequestIdRef.current + 1;
    latestMemoryRequestIdRef.current = requestId;
    setSelected(node);
    setPreviewModalOpen(false);
    setContent(null);
    setTruncated(false);
    setPreviewLimitBytes(null);
    setEditing(false);
    setEditContent(null);

    if (!node.is_text_file) {
      setContentState('ready');
      return;
    }

    setContentState('loading');
    try {
      const res = await workspaceAPI.readMemoryFile(node.path);
      if (requestId !== latestMemoryRequestIdRef.current) {
        return;
      }
      setContent(res.data.content);
      setTruncated(res.data.truncated ?? false);
      setPreviewLimitBytes(res.data.preview_limit_bytes ?? null);
      setContentState('ready');
    } catch (e: any) {
      if (requestId !== latestMemoryRequestIdRef.current) {
        return;
      }
      setContentState('error');
      toastError(t('memory.readFileFailed'), extractErrorMessage(e));
    }
  };

  const handleSave = async () => {
    if (!selected || editContent === null || truncated) return;
    setSaving(true);
    try {
      await workspaceAPI.writeMemoryFile(selected.path, editContent);
      const savedContent = editContent;
      setContent(savedContent);
      setEditing(false);
      setEditContent(null);
      setSelected((current) => current ? {
        ...current,
        size: new TextEncoder().encode(savedContent).length,
        modified_at: Date.now() / 1000,
      } : current);
      toastSuccess(t('files.toast.saveSuccess'));
      await load();
    } catch (e: any) {
      toastError(t('files.toast.saveFailed'), extractErrorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const normalizedSearch = search.trim().toLowerCase();
  const memoryView = useMemo(
    () => buildMemoryView(files, visibleProjects),
    [files, visibleProjects],
  );
  const filtered = useMemo(
    () => filterMemoryTree(memoryView, normalizedSearch),
    [memoryView, normalizedSearch],
  );
  const fileCount = useMemo(() => countMemoryFiles(memoryView), [memoryView]);

  const handleToggle = useCallback((path: string) => {
    setExpandedPaths((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  return (
    <div className="flex h-full gap-4">
      <div className="w-72 flex-shrink-0 bg-surface rounded-card border border-line flex flex-col overflow-hidden">
        <div className="px-3 py-2.5 border-b border-line flex items-center gap-2">
          <IconBrain aria-hidden="true" className="w-4 h-4 text-ai flex-shrink-0" />
          <span className="text-sm font-medium text-fg-secondary">{t('memory.title')}</span>
          <span className="ml-auto text-xs text-fg-muted bg-subtle px-1.5 py-0.5 rounded-pill">{fileCount}</span>
          <button onClick={load} title={t('memory.refresh')} className="ui-btn-icon inline-flex items-center justify-center text-fg-muted hover:text-fg-secondary">
            <IconRefresh aria-hidden="true" className="w-4 h-4" />
          </button>
        </div>

        <div className="px-3 py-2 border-b border-line">
          <div className="relative">
            <IconSearch aria-hidden="true" className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-fg-muted" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('memory.searchPlaceholder')}
              className="ui-input w-full pl-8 pr-3  text-sm border border-line rounded-control"
            />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {loadState === 'loading' ? (
            <div className="flex items-center justify-center h-24"><LoadingSpinner /></div>
          ) : loadState === 'error' ? (
            <div className="flex flex-col items-center justify-center h-24 text-fg-muted text-sm gap-2">
              <IconAlertTriangle aria-hidden="true" className="w-5 h-5 text-warning" />
              <span>{t('memory.loadFailed')}</span>
              <button onClick={load} className="text-xs text-info hover:underline">{t('memory.retry')}</button>
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-24 text-fg-muted text-sm">
              <IconBrain aria-hidden="true" className="w-6 h-6 mb-1 text-fg-muted" />
              {memoryView.length === 0 ? t('memory.noFiles') : t('memory.noMatch')}
            </div>
          ) : (
            filtered.map((node) => (
              <MemoryTreeNode
                key={node.path}
                node={node}
                depth={0}
                expandedPaths={expandedPaths}
                forceExpanded={Boolean(normalizedSearch)}
                selectedPath={selected?.path}
                onToggle={handleToggle}
                onSelect={handleSelect}
              />
            ))
          )}
        </div>

      </div>

      <div className="flex-1 min-w-0 bg-surface rounded-card border border-line flex flex-col overflow-hidden">
        {selected ? (
          <>
            <div className="flex items-center gap-3 px-4 py-2.5 border-b border-line flex-shrink-0">
              <span className="text-sm flex-shrink-0">{fileIcon(selected)}</span>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-fg truncate">
                  {memoryNodeLabel(selected)}
                </div>
                {selected.project_worktree && (
                  <div className="text-xs text-fg-muted truncate" title={selected.project_worktree}>
                    {selected.project_worktree}
                  </div>
                )}
              </div>
              <span className="text-xs text-fg-muted">{formatBytes(selected.size ?? 0)}</span>
              <span className="text-xs text-fg-muted">{formatDate(selected.modified_at)}</span>
              {selected.is_text_file && selected.editable === true && !editing && !truncated && contentState === 'ready' && (
                <button
                  onClick={() => {
                    setEditContent(content ?? '');
                    setEditing(true);
                  }}
                  title={t('files.edit')}
                  className="ui-btn-icon inline-flex items-center justify-center p-1.5 text-fg-muted hover:text-fg-secondary hover:bg-hover rounded-control"
                >
                  <IconEdit aria-hidden="true" className="w-4 h-4" />
                </button>
              )}
              {editing && (
                <>
                  <button onClick={handleSave} disabled={saving} title={t('files.save')} className="ui-btn-icon inline-flex items-center justify-center p-1.5 text-success hover:bg-success-soft rounded-control">
                    <IconDeviceFloppy aria-hidden="true" className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => {
                      setEditing(false);
                      setEditContent(null);
                    }}
                    title={t('files.cancel')}
                    className="ui-btn-icon inline-flex items-center justify-center p-1.5 text-fg-muted hover:text-fg-secondary hover:bg-hover rounded-control"
                  >
                    <IconX aria-hidden="true" className="w-4 h-4" />
                  </button>
                </>
              )}
              <a href={workspaceAPI.memoryDownloadUrl(selected.path)} download={selected.name} title={t('files.download')} className="p-1.5 text-fg-muted hover:text-fg-secondary hover:bg-hover rounded-tag">
                <IconDownload aria-hidden="true" className="w-4 h-4" />
              </a>
              <button onClick={() => setPreviewModalOpen(true)} title={t('files.preview.fullscreen')} className="ui-btn-icon inline-flex items-center justify-center p-1.5 text-fg-muted hover:text-fg-secondary hover:bg-hover rounded-control">
                <IconArrowsDiagonal aria-hidden="true" className="w-4 h-4" />
              </button>
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
              {contentState === 'loading' && (
                <div className="flex items-center justify-center h-24"><LoadingSpinner /></div>
              )}
              {contentState === 'error' && (
                <div className="flex flex-col items-center justify-center h-24 gap-2 text-fg-muted">
                  <IconAlertTriangle aria-hidden="true" className="w-5 h-5 text-warning" />
                  <span className="text-sm">{t('memory.readFailed')}</span>
                  <button onClick={() => handleSelect(selected)} className="text-xs text-info hover:underline">{t('memory.retry')}</button>
                </div>
              )}
              {contentState === 'ready' && (
                <FilePreviewRenderer
                  node={selected}
                  content={content}
                  editing={editing}
                  editContent={editContent}
                  truncated={truncated}
                  previewLimitBytes={previewLimitBytes}
                  fileAccess={MEMORY_PREVIEW_FILE_ACCESS}
                  onEditChange={setEditContent}
                />
              )}
            </div>
            {previewModalOpen && (
              <PreviewModal
                node={selected}
                content={content}
                truncated={truncated}
                previewLimitBytes={previewLimitBytes}
                fileAccess={MEMORY_PREVIEW_FILE_ACCESS}
                onClose={() => setPreviewModalOpen(false)}
              />
            )}
          </>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center gap-3 text-fg-muted">
            <IconBrain aria-hidden="true" className="w-8 h-8 text-fg-muted" />
            <p className="text-sm">{t('memory.selectPrompt')}</p>
            <p className="text-xs text-center px-8">{t('memory.selectDesc')}</p>
          </div>
        )}
      </div>
    </div>
  );
}

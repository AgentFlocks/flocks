import { useState, useEffect, useCallback, useRef, useReducer } from 'react';
import {
  FolderOpen, Upload, Download, Trash2, Edit3, Save,
  X, ChevronRight, RefreshCw, FolderPlus,
  Brain, FileText, AlertTriangle, Search, ArrowLeft,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { useToast } from '@/components/common/Toast';
import { useConfirm } from '@/components/common/ConfirmDialog';
import {
  workspaceAPI, WorkspaceNode, formatBytes, formatDate, fileIcon,
} from '@/api/workspace';

// ─── Types ────────────────────────────────────────────────────────────────

type Tab = 'files' | 'memory';

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
      return { ...state, saving: false, editing: false, editContent: null, content: action.content };
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
  const [activeTab, setActiveTab] = useState<Tab>('files');
  const { t } = useTranslation('workspace');

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title="Workspace"
        description={t('description')}
        icon={<FolderOpen className="w-8 h-8" />}
      />

      <div className="flex gap-1 px-1 mb-4 border-b border-gray-200">
        <TabButton active={activeTab === 'files'} onClick={() => setActiveTab('files')} icon={<FolderOpen className="w-4 h-4" />} label={t('tabs.files')} />
        <TabButton active={activeTab === 'memory'} onClick={() => setActiveTab('memory')} icon={<Brain className="w-4 h-4" />} label={t('tabs.memory')} />
      </div>

      <div className="flex-1 min-h-0 overflow-hidden">
        {activeTab === 'files' ? <FilesTab /> : <MemoryTab />}
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
        active ? 'border-slate-700 text-slate-800' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
      }`}
    >
      {icon}
      {label}
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

  // Preview/edit panel — consolidated into a reducer
  const [panel, dispatchPanel] = useReducer(panelReducer, PANEL_INIT);

  // Upload / new-dir state
  const [uploading, setUploading] = useState(false);
  const [newDir, setNewDir] = useState<{ show: boolean; name: string }>({ show: false, name: '' });
  const [dragOver, setDragOver] = useState(false);

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
      toastError(t('files.toast.loadDirFailed'), e?.response?.data?.detail ?? e.message);
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
        toastError(t('files.toast.readFileFailed'), e?.response?.data?.detail ?? e.message);
      }
    }
  }, [loadDir, loadFileContent, toastError, t]);

  const handleRefresh = useCallback(async () => {
    await loadDir(currentPath, { preservePanel: true });

    if (panel.node?.is_text_file) {
      try {
        await loadFileContent(panel.node.path);
      } catch (e: any) {
        toastError(t('files.toast.readFileFailed'), e?.response?.data?.detail ?? e.message);
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
      loadDir(currentPath);
    } catch (e: any) {
      dispatchPanel({ type: 'cancel_edit' });
      toastError(t('files.toast.saveFailed'), e?.response?.data?.detail ?? e.message);
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
      toastError(t('files.toast.deleteFailed'), e?.response?.data?.detail ?? e.message);
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
      toastError(t('files.toast.uploadFailed'), e?.response?.data?.detail ?? e.message);
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
      toastError(t('files.toast.createDirFailed'), e?.response?.data?.detail ?? e.message);
    }
  }, [newDir.name, currentPath, loadDir, toastError, t]);

  const handleReveal = useCallback(async (node: WorkspaceNode) => {
    try {
      await workspaceAPI.reveal(node.path);
      toastSuccess(t('files.toast.revealSuccess'));
    } catch (e: any) {
      toastError(t('files.toast.revealFailed'), e?.response?.data?.detail ?? e.message);
    }
  }, [toastError, toastSuccess, t]);

  const breadcrumbs = currentPath ? ['', ...currentPath.split('/')] : [''];

  return (
    <div className="flex h-full gap-4">
      {/* File list */}
      <div className="flex-1 min-w-0 flex flex-col bg-white rounded-xl border border-gray-200 overflow-hidden">
        {/* Toolbar */}
        <div className="flex items-center gap-2 px-4 py-2 border-b border-gray-100 flex-shrink-0">
          <div className="flex items-center gap-1 flex-1 min-w-0 text-sm text-gray-600">
            {breadcrumbs.map((crumb, i) => {
              const path = breadcrumbs.slice(1, i + 1).join('/');
              const isLast = i === breadcrumbs.length - 1;
              return (
                <span key={i} className="flex items-center gap-1">
                  {i > 0 && <ChevronRight className="w-3 h-3 text-gray-300 flex-shrink-0" />}
                  <button
                    onClick={() => !isLast && loadDir(path)}
                    className={`truncate ${isLast ? 'text-gray-900 font-medium' : 'text-sky-700 hover:underline'}`}
                  >
                    {crumb === '' ? 'workspace' : crumb}
                  </button>
                </span>
              );
            })}
          </div>
          <div className="flex items-center gap-1 flex-shrink-0">
            {currentPath && (
              <button onClick={() => loadDir(currentPath.split('/').slice(0, -1).join('/'))} title={t('files.back')} className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded">
                <ArrowLeft className="w-4 h-4" />
              </button>
            )}
            <button
              onClick={handleRefresh}
              disabled={loading}
              title={t('files.refresh')}
              className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button onClick={() => setNewDir({ show: true, name: '' })} title={t('files.newDir')} className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded">
              <FolderPlus className="w-4 h-4" />
            </button>
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              title={t('files.upload')}
              className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded"
            >
              <Upload className="w-4 h-4" />
            </button>
            <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(e) => handleUpload(e.target.files)} />
          </div>
        </div>

        {newDir.show && (
          <div className="flex items-center gap-2 px-4 py-2 bg-slate-50 border-b border-slate-100">
            <FolderPlus className="w-4 h-4 text-slate-600 flex-shrink-0" />
            <input
              autoFocus
              value={newDir.name}
              onChange={(e) => setNewDir((d) => ({ ...d, name: e.target.value }))}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleCreateDir();
                if (e.key === 'Escape') setNewDir({ show: false, name: '' });
              }}
              placeholder={t('files.dirNamePlaceholder')}
              className="flex-1 text-sm bg-transparent border-none outline-none text-gray-800"
            />
            <button onClick={handleCreateDir} className="text-xs px-2 py-1 bg-slate-700 text-white rounded hover:bg-slate-800">{t('files.create')}</button>
            <button onClick={() => setNewDir({ show: false, name: '' })} className="text-gray-400 hover:text-gray-600">
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        <div
          className={`flex-1 overflow-y-auto relative ${dragOver ? 'ring-2 ring-sky-400 ring-inset bg-sky-50/80' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); handleUpload(e.dataTransfer.files); }}
        >
          {dragOver && (
            <div className="absolute inset-0 flex items-center justify-center z-10 pointer-events-none">
              <div className="flex flex-col items-center gap-2 text-sky-700">
                <Upload className="w-8 h-8" />
                <span className="text-sm font-medium">{t('files.dropHere')}</span>
              </div>
            </div>
          )}

          {loading ? (
            <div className="flex items-center justify-center h-32"><LoadingSpinner /></div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-32 text-gray-400">
              <FolderOpen className="w-8 h-8 mb-2 opacity-40" />
              <p className="text-sm">{t('files.emptyDir')}</p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-gray-50 dark:bg-zinc-900/95">
                <tr>
                  <th className="w-8 px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-zinc-500"></th>
                  <th className="px-2 py-2 text-left text-xs font-medium text-gray-500 dark:text-zinc-500">{t('files.columns.name')}</th>
                  <th className="w-24 px-4 py-2 text-right text-xs font-medium text-gray-500 dark:text-zinc-500">{t('files.columns.size')}</th>
                  <th className="w-36 px-4 py-2 text-right text-xs font-medium text-gray-500 dark:text-zinc-500">{t('files.columns.modified')}</th>
                  <th className="w-20"></th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr
                    key={item.path}
                    onClick={() => handleSelectNode(item)}
                    className={`group border-t border-gray-50 cursor-pointer transition-colors ${
                      panel.node?.path === item.path
                        ? 'bg-slate-100 dark:bg-zinc-800/70'
                        : 'hover:bg-gray-50 dark:hover:bg-zinc-900/70'
                    }`}
                  >
                    <td className="px-4 py-2 text-sm">{fileIcon(item)}</td>
                    <td className="max-w-0 truncate px-2 py-2 font-medium text-gray-800 dark:text-zinc-100">
                      <span className="block truncate">{item.name}</span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-2 text-right tabular-nums text-gray-400 dark:text-zinc-500">
                      {item.type === 'file' ? formatBytes(item.size ?? 0) : '—'}
                    </td>
                    <td className="whitespace-nowrap px-4 py-2 text-right text-xs text-gray-400 dark:text-zinc-500">
                      {formatDate(item.modified_at)}
                    </td>
                    <td className="px-2 py-2">
                      <div className="flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100" onClick={(e) => e.stopPropagation()}>
                        {item.type === 'file' && (
                          <a href={workspaceAPI.downloadUrl(item.path)} download={item.name} title={t('files.download')} className="p-1 text-gray-400 hover:text-gray-600 rounded hover:bg-gray-100">
                            <Download className="w-3.5 h-3.5" />
                          </a>
                        )}
                        <button onClick={() => handleReveal(item)} title={t('files.reveal')} className="p-1 text-gray-400 hover:text-gray-600 rounded hover:bg-gray-100">
                          <FolderOpen className="w-3.5 h-3.5" />
                        </button>
                        <button onClick={() => handleDelete(item)} title={t('files.delete')} className="p-1 text-gray-400 hover:text-slate-700 rounded hover:bg-slate-100">
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {uploading && (
          <div className="px-4 py-2 bg-slate-50 text-sm text-slate-700 flex items-center gap-2 border-t border-slate-100">
            <RefreshCw className="w-3.5 h-3.5 animate-spin" />
            {t('files.uploading')}
          </div>
        )}
      </div>

      {/* Right: preview / edit panel */}
      {panel.node && (
        <div className="w-96 flex-shrink-0 bg-white rounded-xl border border-gray-200 flex flex-col overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-2.5 border-b border-gray-100 flex-shrink-0">
            <span className="text-sm flex-shrink-0">{fileIcon(panel.node)}</span>
            <span className="flex-1 text-sm font-medium text-gray-800 truncate">{panel.node.name}</span>
            <div className="flex items-center gap-1 flex-shrink-0">
              {panel.node.is_text_file && !panel.editing && !panel.truncated && (
                <button onClick={() => dispatchPanel({ type: 'start_edit' })} title={t('files.edit')} className="p-1.5 text-gray-400 hover:text-slate-700 hover:bg-slate-100 rounded">
                  <Edit3 className="w-4 h-4" />
                </button>
              )}
              {panel.editing && (
                <>
                  <button onClick={handleSave} disabled={panel.saving} title={t('files.save')} className="p-1.5 text-green-600 hover:bg-green-50 rounded">
                    <Save className="w-4 h-4" />
                  </button>
                  <button onClick={() => dispatchPanel({ type: 'cancel_edit' })} title={t('files.cancel')} className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded">
                    <X className="w-4 h-4" />
                  </button>
                </>
              )}
              <a href={workspaceAPI.downloadUrl(panel.node.path)} download={panel.node.name} title={t('files.download')} className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded">
                <Download className="w-4 h-4" />
              </a>
              <button onClick={() => handleReveal(panel.node!)} title={t('files.reveal')} className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded">
                <FolderOpen className="w-4 h-4" />
              </button>
              <button onClick={() => dispatchPanel({ type: 'close' })} title={t('files.close')} className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded">
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>

          <div className="px-4 py-1.5 bg-gray-50 border-b border-gray-100 flex gap-4 text-xs text-gray-400 flex-shrink-0">
            <span>{formatBytes(panel.node.size ?? 0)}</span>
            <span>{formatDate(panel.node.modified_at)}</span>
          </div>

          <div className="flex-1 min-h-0 overflow-hidden">
            {panel.node.is_text_file ? (
              <div className="flex h-full flex-col">
                {panel.truncated && (
                  <div className="mx-4 mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                    {t('files.truncatedPreview', { limit: formatBytes(panel.previewLimitBytes ?? 0) })}
                  </div>
                )}
                {panel.editing ? (
                  <textarea
                    value={panel.editContent ?? ''}
                    onChange={(e) => dispatchPanel({ type: 'edit_change', text: e.target.value })}
                    className="w-full h-full resize-none p-4 text-sm font-mono text-gray-800 border-none outline-none bg-white"
                    spellCheck={false}
                  />
                ) : (
                  <pre className="w-full h-full overflow-auto p-4 text-sm font-mono text-gray-700 whitespace-pre-wrap break-words bg-white">
                    {panel.content ?? <LoadingSpinner />}
                  </pre>
                )}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-full gap-3 text-gray-400 p-6">
                <AlertTriangle className="w-10 h-10 text-orange-300" />
                <p className="text-sm text-center">{t('files.binaryPreview')}</p>
                <a
                  href={workspaceAPI.downloadUrl(panel.node.path)}
                  download={panel.node.name}
                  className="flex items-center gap-2 px-4 py-2 bg-slate-700 text-white text-sm rounded-lg hover:bg-slate-800"
                >
                  <Download className="w-4 h-4" />
                  {t('files.downloadFile')}
                </a>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Memory Tab ───────────────────────────────────────────────────────────

type MemoryLoadState = 'idle' | 'loading' | 'error';

function MemoryTab() {
  const { error: toastError } = useToast();
  const { t } = useTranslation('workspace');
  const [files, setFiles] = useState<WorkspaceNode[]>([]);
  const [loadState, setLoadState] = useState<MemoryLoadState>('loading');
  const [selected, setSelected] = useState<WorkspaceNode | null>(null);

  // Distinguish "loading content" from "content failed" to avoid
  // the '加载中...' placeholder getting stuck on error.
  const [contentState, setContentState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [content, setContent] = useState<string | null>(null);

  const [search, setSearch] = useState('');

  const load = useCallback(async () => {
    setLoadState('loading');
    try {
      const res = await workspaceAPI.listMemory();
      setFiles(Array.isArray(res.data) ? res.data : []);
      setLoadState('idle');
    } catch (e: any) {
      setLoadState('error');
      toastError(t('memory.loadMemoryFailed'), e?.response?.data?.detail ?? e.message);
    }
  }, [toastError, t]);

  useEffect(() => { load(); }, [load]);

  const handleSelect = async (node: WorkspaceNode) => {
    setSelected(node);
    setContent(null);
    setContentState('loading');
    try {
      const res = await workspaceAPI.readMemoryFile(node.path);
      setContent(res.data.content);
      setContentState('ready');
    } catch (e: any) {
      setContentState('error');
      toastError(t('memory.readFileFailed'), e?.response?.data?.detail ?? e.message);
    }
  };

  const filtered = files.filter((f) => f.name.toLowerCase().includes(search.toLowerCase()));

  return (
    <div className="flex h-full gap-4">
      <div className="w-72 flex-shrink-0 bg-white rounded-xl border border-gray-200 flex flex-col overflow-hidden">
        <div className="px-3 py-2.5 border-b border-gray-100 flex items-center gap-2">
          <Brain className="w-4 h-4 text-purple-500 flex-shrink-0" />
          <span className="text-sm font-medium text-gray-700">{t('memory.title')}</span>
          <span className="ml-auto text-xs text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded-full">{files.length}</span>
          <button onClick={load} title={t('memory.refresh')} className="text-gray-400 hover:text-gray-600">
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="px-3 py-2 border-b border-gray-100">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('memory.searchPlaceholder')}
              className="w-full pl-8 pr-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-300"
            />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {loadState === 'loading' ? (
            <div className="flex items-center justify-center h-24"><LoadingSpinner /></div>
          ) : loadState === 'error' ? (
            <div className="flex flex-col items-center justify-center h-24 text-gray-400 text-sm gap-2">
              <AlertTriangle className="w-5 h-5 text-orange-400" />
              <span>{t('memory.loadFailed')}</span>
              <button onClick={load} className="text-xs text-sky-700 hover:underline">{t('memory.retry')}</button>
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-24 text-gray-400 text-sm">
              <Brain className="w-6 h-6 mb-1 opacity-40" />
              {files.length === 0 ? t('memory.noFiles') : t('memory.noMatch')}
            </div>
          ) : (
            filtered.map((f) => (
              <button
                key={f.path}
                onClick={() => handleSelect(f)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 border-t border-gray-50 text-left transition-colors ${
                  selected?.path === f.path ? 'bg-purple-50 text-purple-700' : 'hover:bg-gray-50 text-gray-700'
                }`}
              >
                <FileText className={`w-4 h-4 flex-shrink-0 ${selected?.path === f.path ? 'text-purple-500' : 'text-gray-400'}`} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate">{f.name}</div>
                  <div className="text-xs text-gray-400">{formatDate(f.modified_at)} · {formatBytes(f.size ?? 0)}</div>
                </div>
              </button>
            ))
          )}
        </div>

        <div className="px-3 py-2 border-t border-gray-100 text-xs text-gray-400">
          {t('memory.readOnly')}
        </div>
      </div>

      <div className="flex-1 min-w-0 bg-white rounded-xl border border-gray-200 flex flex-col overflow-hidden">
        {selected ? (
          <>
            <div className="flex items-center gap-3 px-4 py-2.5 border-b border-gray-100 flex-shrink-0">
              <Brain className="w-4 h-4 text-purple-500 flex-shrink-0" />
              <span className="flex-1 text-sm font-medium text-gray-800 truncate">{selected.name}</span>
              <span className="text-xs text-gray-400">{formatBytes(selected.size ?? 0)}</span>
              <span className="text-xs text-gray-400">{formatDate(selected.modified_at)}</span>
            </div>
            <div className="flex-1 overflow-auto">
              {contentState === 'loading' && (
                <div className="flex items-center justify-center h-24"><LoadingSpinner /></div>
              )}
              {contentState === 'error' && (
                <div className="flex flex-col items-center justify-center h-24 gap-2 text-gray-400">
                  <AlertTriangle className="w-5 h-5 text-orange-400" />
                  <span className="text-sm">{t('memory.readFailed')}</span>
                  <button onClick={() => handleSelect(selected)} className="text-xs text-sky-700 hover:underline">{t('memory.retry')}</button>
                </div>
              )}
              {contentState === 'ready' && (
                <pre className="p-4 text-sm font-mono text-gray-700 whitespace-pre-wrap break-words">
                  {content}
                </pre>
              )}
            </div>
          </>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center gap-3 text-gray-400">
            <Brain className="w-12 h-12 opacity-20" />
            <p className="text-sm">{t('memory.selectPrompt')}</p>
            <p className="text-xs text-center px-8">{t('memory.selectDesc')}</p>
          </div>
        )}
      </div>
    </div>
  );
}

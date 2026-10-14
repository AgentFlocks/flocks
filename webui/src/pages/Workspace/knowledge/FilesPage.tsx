import { useEffect, useRef, useState } from 'react';
import { Folder, FolderInput, FolderPlus, Pencil, Trash2, Upload, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { knowledgebaseAPI, type KnowledgeEntry, type KnowledgeFile } from '@/api/knowledgebase';
import { formatBytes } from '@/api/workspace';
import { useConfirm } from '@/components/common/ConfirmDialog';
import { useToast } from '@/components/common/Toast';
import { WorkspaceFileSplit, FileListTable, FileListRow, FileListActions, fileActionClass, previewActionClass } from '@/components/common/WorkspaceFileView';
import { FolderPicker } from './FileBrowser';
import FilePreview from './FilePreview';
import { PAGE_SIZE, useKnowledgeQuery, useMountedRef } from './state';
import { CreatedAt, DirectoryNavigation, fileType, LoadState, Modal, Pagination } from './ui';

interface FilesPageProps {
  revision: number;
  onChanged: () => void;
  folderId?: string;
  onNavigate?: (id: string | undefined) => void;
  onFileTotal?: (total: number | undefined) => void;
}

export default function FilesPage({ folderId: requestedFolderId, onNavigate, ...props }: FilesPageProps) {
  const [localFolderId, setLocalFolderId] = useState(requestedFolderId);
  const folderId = onNavigate ? requestedFolderId : localFolderId;
  return <DirectoryPage key={JSON.stringify(folderId ?? null)} {...props} folderId={folderId} onNavigate={onNavigate ?? setLocalFolderId} />;
}

function DirectoryPage({ revision, onChanged, folderId, onNavigate, onFileTotal }: Omit<FilesPageProps, 'folderId' | 'onNavigate'> & {
  folderId?: string;
  onNavigate: (id: string | undefined) => void;
}) {
  const { t } = useTranslation('workspace');
  const confirm = useConfirm();
  const toast = useToast();
  const mounted = useMountedRef();
  const pending = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [preview, setPreview] = useState<KnowledgeFile | null>(null);
  const [busy, setBusy] = useState(false);
  const [editor, setEditor] = useState<{ entry: KnowledgeEntry | null; name: string } | null>(null);
  const [moving, setMoving] = useState<KnowledgeEntry | null>(null);
  const query = useKnowledgeQuery(JSON.stringify([folderId, q, page, revision]), signal => knowledgebaseAPI.directory({ parent_id: folderId, q, page, page_size: PAGE_SIZE }, signal));
  const canWrite = query.data?.current_folder.can_write === true;
  const canSaveName = editor !== null && Boolean(editor.name.trim()) && (editor.entry
    ? query.data?.items.some(entry => entry.id === editor.entry?.id && entry.can_manage === true) === true
    : canWrite);

  useEffect(() => {
    if (query.data) onFileTotal?.(query.data.file_total);
    else if (query.error) onFileTotal?.(undefined);
  }, [query.data, query.error, onFileTotal]);

  const mutate = async (operation: () => Promise<unknown>, onSuccess: () => void) => {
    if (pending.current || !mounted.current) return;
    pending.current = true;
    setBusy(true);
    try {
      await operation();
      if (mounted.current) { onSuccess(); onChanged(); }
    } catch (error) {
      if (mounted.current) toast.error(t('knowledge.loadFailed'), (error as Error).message);
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  };

  const upload = (file: File | undefined) => {
    const parent = query.data?.current_folder;
    if (!file || parent?.can_write !== true) return;
    void mutate(() => knowledgebaseAPI.upload(file, parent.id), () => toast.success(t('knowledge.files.upload')));
  };

  const saveName = () => {
    const name = editor?.name.trim();
    const parent = query.data?.current_folder;
    if (!editor || !name || !parent || !canSaveName) return;
    const entry = editor.entry;
    void mutate(() => entry
      ? knowledgebaseAPI.updateFile(entry.id, { name })
      : knowledgebaseAPI.createFolder({ name, parent_id: parent.id }), () => {
      setEditor(null);
      // The provider may replace an entry's ID during rename; reload its parent, not the old ID.
      if (preview?.id === entry?.id) setPreview(null);
      toast.success(t(entry ? 'knowledge.files.renamed' : 'knowledge.files.folderCreated'));
    });
  };

  const remove = async (entry: KnowledgeEntry) => {
    if (entry.can_manage !== true || pending.current) return;
    pending.current = true;
    setBusy(true);
    try {
      const accepted = await confirm({
        title: t('knowledge.files.deleteTitle'),
        description: t(entry.kind === 'folder' ? 'knowledge.files.deleteFolderConfirm' : 'knowledge.files.deleteConfirm', { name: entry.name }),
        variant: 'danger',
      });
      if (!accepted || !mounted.current) return;
      await knowledgebaseAPI.removeFile(entry.id);
      if (mounted.current) {
        if (preview?.id === entry.id) setPreview(null);
        if (editor?.entry?.id === entry.id) setEditor(null);
        if (query.data?.items.length === 1 && page > 1) setPage(page - 1);
        toast.success(t('knowledge.delete'));
        onChanged();
      }
    } catch (error) {
      if (mounted.current) toast.error(t('knowledge.loadFailed'), (error as Error).message);
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  };

  const move = (targetId: string) => {
    if (!moving || moving.kind !== 'file' || moving.can_manage !== true) return;
    const entry = moving;
    void mutate(() => knowledgebaseAPI.updateFile(entry.id, { parent_id: targetId }), () => {
      setMoving(null);
      if (preview?.id === entry.id) setPreview(null);
      if (query.data?.items.length === 1 && page > 1) setPage(page - 1);
      toast.success(t('knowledge.files.moved'));
    });
  };

  // The parent remounts this page on navigation, clearing search, pagination, preview and pending UI.
  const refresh = () => onChanged();

  return <>
    <WorkspaceFileSplit
      list={listWidth => <>
        <div className="flex-shrink-0 space-y-2 border-b border-gray-100 px-4 py-2">
          <DirectoryNavigation breadcrumbs={query.data?.breadcrumbs ?? []} currentFolderId={folderId} onNavigate={onNavigate} loading={query.loading} onRefresh={refresh} search={<input
            value={q}
            onChange={event => { setQ(event.target.value); setPage(1); }}
            aria-label={t('knowledge.files.search')}
            placeholder={t('knowledge.files.search')}
            title={t('knowledge.files.pageSearch')}
            className="w-full min-w-0 max-w-xs rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-slate-400 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
          />}>
            {canWrite && <>
              <button type="button" disabled={busy} onClick={() => setEditor({ entry: null, name: '' })} title={t('knowledge.files.newFolder')} className={`${previewActionClass} disabled:cursor-not-allowed disabled:opacity-50`}><FolderPlus className="h-4 w-4" /></button>
              <button type="button" disabled={busy} onClick={() => input.current?.click()} title={t('knowledge.files.upload')} className={`${previewActionClass} disabled:cursor-not-allowed disabled:opacity-50`}><Upload className="h-4 w-4" /></button>
              <input ref={input} type="file" className="hidden" onChange={event => { upload(event.target.files?.[0]); event.target.value = ''; }} />
            </>}
          </DirectoryNavigation>
          {query.data?.current_folder.can_write === false && <p className="text-xs text-gray-500">{t('knowledge.files.readOnlyFolder')}</p>}
        </div>
        {editor && <form onSubmit={event => { event.preventDefault(); saveName(); }} className="flex flex-shrink-0 items-center gap-2 border-b border-slate-100 bg-slate-50 px-4 py-2 dark:border-zinc-700 dark:bg-zinc-800">
          {editor.entry ? <Pencil className="h-4 w-4 flex-shrink-0 text-slate-500" /> : <FolderPlus className="h-4 w-4 flex-shrink-0 text-slate-500" />}
          <input autoFocus disabled={busy} value={editor.name} onChange={event => setEditor({ ...editor, name: event.target.value })} onKeyDown={event => { if (event.key === 'Escape' && !busy) setEditor(null); }} aria-label={editor.entry ? t('knowledge.files.renameName', { name: editor.entry.name }) : t('files.dirNamePlaceholder')} placeholder={t('files.dirNamePlaceholder')} className="min-w-0 flex-1 bg-transparent text-sm text-gray-800 outline-none dark:text-zinc-100" />
          <button type="submit" disabled={busy || !canSaveName} className="rounded bg-slate-700 px-2 py-1 text-xs text-white hover:bg-slate-800 disabled:opacity-50">{t(editor.entry ? 'knowledge.files.saveName' : 'files.create')}</button>
          <button type="button" disabled={busy} onClick={() => setEditor(null)} aria-label={t('knowledge.close')} className={previewActionClass}><X className="h-4 w-4" /></button>
        </form>}
        <LoadState loading={query.loading} error={query.error} retry={refresh}>
          <div className="relative min-h-0 flex-1 overflow-auto">
            <FileListTable aria-label={t('knowledge.files.table')} className="table-fixed" header={<>
              <th scope="col" className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-zinc-500">{t('knowledge.name')}</th>
              <th scope="col" className="w-20 px-4 py-2 text-right text-xs font-medium text-gray-500 dark:text-zinc-500">{t('knowledge.type')}</th>
              {listWidth >= 560 && <th scope="col" className="w-24 px-4 py-2 text-right text-xs font-medium text-gray-500 dark:text-zinc-500">{t('files.columns.size')}</th>}
              {listWidth >= 760 && <th scope="col" className="w-40 px-4 py-2 text-right text-xs font-medium text-gray-500 dark:text-zinc-500">{t('files.columns.modified')}</th>}
              <th scope="col" className="w-28 px-2 py-2 text-right text-xs font-medium text-gray-500 dark:text-zinc-500">{t('knowledge.actions')}</th>
            </>}>
              {query.data?.items.map(entry => (
                <FileListRow key={entry.id} selected={preview?.id === entry.id} onClick={() => entry.kind === 'folder' ? onNavigate(entry.id) : setPreview(entry)}>
                  <td className="max-w-0 px-4 py-2 font-medium text-gray-800 dark:text-zinc-100">
                    <button type="button" className="flex w-full min-w-0 items-center gap-2 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-slate-500" title={entry.name}>
                      {entry.kind === 'folder' && <Folder aria-hidden="true" className="h-4 w-4 flex-shrink-0 text-sky-600" />}<span className="truncate">{entry.name}</span>
                    </button>
                  </td>
                  <td className="whitespace-nowrap px-4 py-2 text-right text-gray-400 dark:text-zinc-500">{entry.kind === 'folder' ? t('knowledge.files.folder') : fileType(entry.name)}</td>
                  {listWidth >= 560 && <td className="whitespace-nowrap px-4 py-2 text-right tabular-nums text-gray-400 dark:text-zinc-500">{entry.kind === 'folder' || entry.size == null ? '—' : formatBytes(entry.size)}</td>}
                  {listWidth >= 760 && <td className="whitespace-nowrap px-4 py-2 text-right text-xs text-gray-400 dark:text-zinc-500"><CreatedAt value={entry.updated_at} /></td>}
                  <td className="px-2 py-2">
                    {entry.can_manage === true && <FileListActions>
                      <button type="button" disabled={busy} onClick={() => setEditor({ entry, name: entry.name })} title={t('knowledge.files.rename')} className={fileActionClass}><Pencil className="h-3.5 w-3.5" /></button>
                      {entry.kind === 'file' && <button type="button" disabled={busy} onClick={() => setMoving(entry)} title={t('knowledge.files.move')} className={fileActionClass}><FolderInput className="h-3.5 w-3.5" /></button>}
                      <button type="button" disabled={busy} onClick={() => void remove(entry)} title={t('knowledge.delete')} className={fileActionClass}><Trash2 className="h-3.5 w-3.5" /></button>
                    </FileListActions>}
                  </td>
                </FileListRow>
              ))}
              {query.data?.items.length === 0 && <tr><td colSpan={3 + Number(listWidth >= 560) + Number(listWidth >= 760)} className="h-32 px-4 text-center text-gray-400">{t('knowledge.files.empty')}</td></tr>}
            </FileListTable>
          </div>
          {query.data && <div className="flex-shrink-0"><Pagination page={page} total={query.data.total} onChange={setPage} /></div>}
        </LoadState>
      </>}
      preview={preview ? <FilePreview key={preview.id} file={preview} onClose={() => setPreview(null)} onLinked={onChanged} /> : null}
    />
    {moving && <Modal title={t('knowledge.files.moveName', { name: moving.name })} onClose={() => { if (!busy) setMoving(null); }}>
      <FolderPicker sourceParentId={query.data?.current_folder.id ?? moving.parent_id ?? undefined} busy={busy} onMove={move} />
    </Modal>}
  </>;
}

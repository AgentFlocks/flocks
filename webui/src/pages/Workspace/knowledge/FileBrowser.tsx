import { useState } from 'react';
import { Folder } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { knowledgebaseAPI, type KnowledgeFile } from '@/api/knowledgebase';
import { PAGE_SIZE, useKnowledgeQuery } from './state';
import { Button, DirectoryNavigation, inputClass, LoadState, Pagination } from './ui';

function useDirectoryBrowser() {
  const [folderId, setFolderId] = useState<string>();
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const query = useKnowledgeQuery(JSON.stringify([folderId, q, page]), signal => knowledgebaseAPI.directory({ parent_id: folderId, q, page, page_size: PAGE_SIZE }, signal));
  const navigate = (id: string | undefined) => { setFolderId(id); setQ(''); setPage(1); };
  const search = (value: string) => { setQ(value); setPage(1); };
  return { query, q, page, setPage, navigate, search };
}

export default function FileBrowser({
  selected,
  onSelection,
  excluded = [],
}: {
  selected: string[];
  onSelection: (ids: string[]) => void;
  excluded?: string[];
}) {
  const { t } = useTranslation('workspace');
  const { query, q, page, setPage, navigate, search } = useDirectoryBrowser();
  const toggle = (id: string) => onSelection(selected.includes(id) ? selected.filter(item => item !== id) : [...selected, id]);
  const entries = query.data?.items.filter(entry => entry.kind === 'folder' || !excluded.includes(entry.id));
  return <div className="space-y-3">
    <DirectoryNavigation breadcrumbs={query.data?.breadcrumbs ?? []} onNavigate={navigate} loading={query.loading} onRefresh={query.reload} />
    <input value={q} onChange={event => search(event.target.value)} aria-label={t('knowledge.files.search')} placeholder={t('knowledge.files.search')} title={t('knowledge.files.pageSearch')} className={inputClass} />
    <LoadState loading={query.loading} error={query.error} retry={query.reload}>
      <ul className="divide-y divide-gray-100 dark:divide-zinc-800">
        {entries?.map(entry => (
          <li key={entry.id}>
            {entry.kind === 'folder'
              ? <button type="button" onClick={() => navigate(entry.id)} className="flex w-full items-center gap-3 px-2 py-2 text-left text-sm hover:bg-gray-50 dark:hover:bg-zinc-800"><Folder aria-hidden="true" className="h-4 w-4 flex-shrink-0 text-sky-600" /><span className="min-w-0 flex-1 truncate">{entry.name}</span></button>
              : <label className="flex items-center gap-3 px-2 py-2 text-sm">
                <input type="checkbox" checked={selected.includes(entry.id)} onChange={() => toggle(entry.id)} />
                <span className="min-w-0 flex-1 truncate">{entry.name}</span>
              </label>}
          </li>
        ))}
      </ul>
      {entries?.length === 0 && <p className="px-2 py-4 text-sm text-gray-500">{t('knowledge.files.empty')}</p>}
      {query.data && <Pagination page={page} total={query.data.total} onChange={setPage} />}
    </LoadState>
  </div>;
}

/** Navigates real folders; choosing a target never selects or expands source files. */
export function FolderPicker({ sourceParentId, busy, onMove }: {
  sourceParentId?: string;
  busy: boolean;
  onMove: (id: string) => void;
}) {
  const { t } = useTranslation('workspace');
  const { query, q, page, setPage, navigate, search } = useDirectoryBrowser();
  const current = query.data?.current_folder;
  const canMove = !busy && current?.can_write === true && current.id !== sourceParentId;
  const folders = query.data?.items.filter(entry => entry.kind === 'folder');
  return <div className="space-y-3">
    <DirectoryNavigation breadcrumbs={query.data?.breadcrumbs ?? []} onNavigate={navigate} loading={query.loading} onRefresh={query.reload} />
    <input value={q} onChange={event => search(event.target.value)} aria-label={t('knowledge.files.search')} placeholder={t('knowledge.files.search')} className={inputClass} />
    <LoadState loading={query.loading} error={query.error} retry={query.reload}>
      <ul className="divide-y divide-gray-100 dark:divide-zinc-800">
        {folders?.map(folder => <li key={folder.id}><button type="button" onClick={() => navigate(folder.id)} className="flex w-full items-center gap-3 px-2 py-2 text-left text-sm hover:bg-gray-50 dark:hover:bg-zinc-800"><Folder aria-hidden="true" className="h-4 w-4 flex-shrink-0 text-sky-600" /><span className="min-w-0 flex-1 truncate">{folder.name}</span></button></li>)}
      </ul>
      {folders?.length === 0 && <p className="px-2 py-4 text-sm text-gray-500">{t('knowledge.files.noFoldersOnPage')}</p>}
      {query.data && <Pagination page={page} total={query.data.total} onChange={setPage} />}
    </LoadState>
    {current?.can_write === false && <p className="text-xs text-gray-500">{t('knowledge.files.readOnlyFolder')}</p>}
    <div className="flex justify-end"><Button disabled={!canMove} onClick={() => { if (canMove && current) onMove(current.id); }}>{t('knowledge.files.moveHere')}</Button></div>
  </div>;
}

export type { KnowledgeFile };

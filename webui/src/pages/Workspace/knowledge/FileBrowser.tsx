import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { knowledgebaseAPI, type KnowledgeFile } from '@/api/knowledgebase';
import { PAGE_SIZE, useKnowledgeQuery } from './state';
import { inputClass, LoadState, Pagination } from './ui';

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
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const query = useKnowledgeQuery(JSON.stringify([q, page]), signal => knowledgebaseAPI.files({ q, page, page_size: PAGE_SIZE }, signal));
  const toggle = (id: string) => onSelection(selected.includes(id) ? selected.filter(item => item !== id) : [...selected, id]);
  return <div className="space-y-3">
    <input value={q} onChange={event => { setQ(event.target.value); setPage(1); }} placeholder={t('knowledge.files.search')} className={inputClass} />
    <p className="text-xs text-gray-500">{t('knowledge.datasets.pageSearch')}</p>
    <LoadState loading={query.loading} error={query.error} retry={query.reload}>
      <ul className="divide-y divide-gray-100 dark:divide-zinc-800">
        {query.data?.items.filter(file => !excluded.includes(file.id)).map(file => (
          <li key={file.id}>
            <label className="flex items-center gap-3 px-2 py-2 text-sm">
              <input type="checkbox" checked={selected.includes(file.id)} onChange={() => toggle(file.id)} />
              <span className="min-w-0 flex-1 truncate">{file.name}</span>
            </label>
          </li>
        ))}
      </ul>
      {query.data && <Pagination page={page} total={query.data.total} onChange={setPage} />}
    </LoadState>
  </div>;
}

export type { KnowledgeFile };

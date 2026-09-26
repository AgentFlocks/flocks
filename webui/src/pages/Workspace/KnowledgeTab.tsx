import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { knowledgebaseAPI } from '@/api/knowledgebase';
import FilesPage from './knowledge/FilesPage';
import DatasetsPage from './knowledge/DatasetsPage';
import { useKnowledgeQuery } from './knowledge/state';
import { Button, LoadState } from './knowledge/ui';

export default function KnowledgeTab({ active = true }: { active?: boolean }) {
  const { t } = useTranslation('workspace');
  const [page, setPage] = useState<'files' | 'datasets'>('files');
  const [revision, setRevision] = useState(0);
  const status = useKnowledgeQuery(active ? 'integration-status' : null, signal => knowledgebaseAPI.status(signal));
  const ready = status.data?.ready === true;
  const changed = () => setRevision(value => value + 1);
  if (!active) return null;
  return <div className="space-y-4">
    <div role="tablist" aria-label={t('knowledge.navigation')} className="flex gap-2">
      <Button aria-selected={page === 'files'} onClick={() => setPage('files')}>{t('knowledge.files.title')}</Button>
      <Button aria-selected={page === 'datasets'} onClick={() => setPage('datasets')}>{t('knowledge.datasets.title')}</Button>
    </div>
    <LoadState loading={status.loading} error={status.error} retry={status.reload}>
      {!ready && status.data && <div role="status" className="rounded-lg border border-gray-200 p-4 text-sm text-gray-600 dark:border-zinc-700 dark:text-zinc-300">
        <p>{t('knowledge.unavailable.knowledgebase_not_configured')}</p>
        <p className="mt-1 text-xs text-gray-500">{t('knowledge.unavailable.knowledgebase_not_configuredHint')}</p>
        <Button className="mt-3" onClick={status.reload}>{t('knowledge.unavailable.check')}</Button>
      </div>}
      {ready && page === 'files' && <FilesPage revision={revision} onChanged={changed} />}
      {ready && page === 'datasets' && <DatasetsPage revision={revision} onChanged={changed} />}
    </LoadState>
  </div>;
}
